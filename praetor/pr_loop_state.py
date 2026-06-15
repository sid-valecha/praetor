from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

PrLoopState = Literal["needs_repair", "waiting", "clean", "blocked"]


class PRLoopStateResult(BaseModel):
    state: PrLoopState
    actionable_review_items: list[str] = Field(default_factory=list)
    waiting_review_items: list[str] = Field(default_factory=list)
    failing_checks: list[str] = Field(default_factory=list)
    pending_checks: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)


def classify_pr_loop_state(raw: Mapping[str, Any] | None) -> PRLoopStateResult:
    """Classify PR loop state from already-fetched intake-style payload."""

    if not isinstance(raw, Mapping):
        return PRLoopStateResult(
            state="blocked",
            blocked_reasons=["Intake payload is missing or not a mapping."],
        )

    if _is_terminal_pr(raw):
        return PRLoopStateResult(state="clean")

    blocked_reasons: list[str] = []
    blocked_reasons.extend(_collect_review_thread_diagnostics(raw))

    actionable_review_items: list[str] = []
    waiting_review_items: list[str] = []
    failing_checks: list[str] = []
    pending_checks: list[str] = []

    review_decision = _normalize_upper(raw.get("reviewDecision"))
    if review_decision == "CHANGES_REQUESTED":
        actionable_review_items.append("Review decision requested changes.")

    thread_signals = _collect_review_thread_items(raw)
    actionable_review_items.extend(thread_signals["actionable"])
    waiting_review_items.extend(thread_signals["waiting"])

    actionable_review_items.extend(_collect_comment_review_signals(raw))
    actionable_review_items.extend(_collect_latest_review_signals(raw))

    checks_present, check_failing, check_pending = _collect_check_statuses(
        raw.get("statusCheckRollup")
    )
    failing_checks.extend(check_failing)
    pending_checks.extend(check_pending)

    if blocked_reasons:
        state: PrLoopState = "blocked"
    elif actionable_review_items or failing_checks:
        state = "needs_repair"
    elif waiting_review_items or pending_checks or not checks_present:
        state = "waiting"
    else:
        state = "clean"

    return PRLoopStateResult(
        state=state,
        actionable_review_items=actionable_review_items,
        waiting_review_items=waiting_review_items,
        failing_checks=failing_checks,
        pending_checks=pending_checks,
        blocked_reasons=blocked_reasons,
    )


def _is_terminal_pr(raw: Mapping[str, Any]) -> bool:
    state = _normalize_upper(raw.get("state"))
    if state in {"CLOSED", "MERGED"}:
        return True
    return bool(raw.get("mergedAt"))


def _collect_review_thread_diagnostics(raw: Mapping[str, Any]) -> list[str]:
    diagnostics: list[str] = []
    review_threads = _get_review_threads_node(raw)

    if review_threads is None:
        diagnostics.append("Review thread payload is missing.")

    if isinstance(review_threads, Mapping):
        page_info = review_threads.get("pageInfo")
        if isinstance(page_info, Mapping):
            has_next_page = _normalize_bool(page_info.get("hasNextPage"))
            if has_next_page is True:
                diagnostics.append(
                    "Review threads unavailable: review thread results were truncated."
                )
            elif has_next_page is None:
                diagnostics.append("Review thread payload includes unexpected page state.")

        elif "nodes" in review_threads and not isinstance(review_threads["nodes"], list):
            diagnostics.append("Review thread payload had invalid `nodes` shape.")
        elif "nodes" not in review_threads:
            diagnostics.append("Review thread payload did not include thread nodes.")

    if isinstance(review_threads, list) and not review_threads:
        diagnostics.append("Review threads payload was empty list.")

    thread_payload_error = _collect_review_thread_error_payload(raw)
    diagnostics.extend(thread_payload_error)

    if "errors" in raw and isinstance(raw["errors"], list):
        diagnostics.extend(_normalize_graphql_messages(raw["errors"]))

    return list(dict.fromkeys(diagnostics))


def _collect_review_thread_items(raw: Mapping[str, Any]) -> dict[str, list[str]]:
    threads = _iter_review_threads(raw)
    actionable: list[str] = []
    waiting: list[str] = []

    for thread in threads:
        if not isinstance(thread, Mapping):
            continue

        if not _is_unresolved(thread):
            continue

        is_outdated = _normalize_bool(thread.get("isOutdated")) is True
        kind = "outdated review thread" if is_outdated else "review thread"
        comment = _first_comment(thread.get("comments"))
        signal = f"Unresolved {kind}." if comment is None else _format_thread_signal(kind, comment)

        if is_outdated:
            waiting.append(signal)
        else:
            actionable.append(signal)

    return {
        "actionable": actionable,
        "waiting": waiting,
    }


def _collect_comment_review_signals(raw: Mapping[str, Any]) -> list[str]:
    signals: list[str] = []
    for comment in _as_list(raw.get("comments")):
        if not isinstance(comment, Mapping):
            continue
        if _is_unresolved(comment):
            body = _truncate((comment.get("body") or "").strip())
            signals.append(
                f"Unresolved review comment: {body}" if body else "Unresolved review comment."
            )

    return signals


def _collect_latest_review_signals(raw: Mapping[str, Any]) -> list[str]:
    signals: list[str] = []
    for review in _as_list(raw.get("latestReviews")):
        if not isinstance(review, Mapping):
            continue

        state = _normalize_upper(review.get("state"))
        if state == "CHANGES_REQUESTED":
            body = _truncate((review.get("body") or "").strip())
            signals.append(
                f"Latest review: CHANGES_REQUESTED - {body}"
                if body
                else "Latest review: CHANGES_REQUESTED."
            )
            continue

        if _is_unresolved(review):
            body = _truncate((review.get("body") or "").strip())
            signals.append(
                f"Unresolved review signal: {body}" if body else "Unresolved review signal."
            )

    return signals


def _collect_check_statuses(
    raw: Any,
) -> tuple[bool, list[str], list[str]]:
    if raw is None:
        return False, [], []

    if isinstance(raw, list):
        checks_present = False
        failing_checks: list[str] = []
        pending_checks: list[str] = []
        for entry in raw:
            present, check_failing, check_pending = _collect_check_statuses(entry)
            checks_present = checks_present or present
            failing_checks.extend(check_failing)
            pending_checks.extend(check_pending)
        return checks_present, _dedupe(failing_checks), _dedupe(pending_checks)

    if not isinstance(raw, Mapping):
        return False, [], []

    checks_present = False
    failing_checks: list[str] = []
    pending_checks: list[str] = []

    if _looks_like_check_node(raw):
        checks_present = True
        check_name = _as_check_name(raw) or "check"
        check_state = _normalize_lower(raw.get("state"))
        check_status = _normalize_lower(raw.get("status"))
        check_conclusion = _normalize_lower(raw.get("conclusion"))

        outcome = _check_outcome(check_state, check_status, check_conclusion)
        if outcome == "failing":
            detail = _choose_check_detail(
                check_name,
                check_state,
                check_status,
                check_conclusion,
                prefer_conclusion=True,
            )
            failing_checks.append(f"Failing check: {detail}.")
        elif outcome == "pending":
            detail = _choose_check_detail(
                check_name,
                check_state,
                check_status,
                check_conclusion,
                prefer_conclusion=False,
            )
            pending_checks.append(f"Pending check: {detail}.")
        elif outcome == "unknown":
            detail = _choose_check_detail(
                check_name,
                check_state,
                check_status,
                check_conclusion,
                prefer_conclusion=False,
            )
            pending_checks.append(f"Unknown check state: {detail}.")

    for key in ("checkRuns", "nodes", "checks", "commits", "statusCheckRollup"):
        if key in raw:
            nested_present, nested_failing, nested_pending = _collect_check_statuses(raw[key])
            checks_present = checks_present or nested_present
            failing_checks.extend(nested_failing)
            pending_checks.extend(nested_pending)

    return checks_present, _dedupe(failing_checks), _dedupe(pending_checks)


def _looks_like_check_node(raw: Mapping[str, Any]) -> bool:
    return any(key in raw for key in ("name", "status", "conclusion", "state"))


def _check_outcome(
    state: str,
    status: str,
    conclusion: str,
) -> Literal["pass", "failing", "pending", "unknown"]:
    failing = {
        "failed",
        "failure",
        "timed_out",
        "cancelled",
        "action_required",
        "error",
        "startup_failure",
        "stale",
    }
    passing = {"success", "passed", "neutral", "skipped"}
    pending = {"in_progress", "queued", "waiting", "pending", "running"}

    if conclusion in failing or state in failing or status in failing:
        return "failing"

    if conclusion in passing:
        return "pass"
    if state in passing:
        return "pass"
    if status in passing:
        return "pass"

    if state in pending:
        return "pending"
    if status in pending:
        return "pending"

    return "unknown"


def _choose_check_detail(
    name: str,
    state: str,
    status: str,
    conclusion: str,
    prefer_conclusion: bool,
) -> str:
    if prefer_conclusion and conclusion:
        return f"{name} (conclusion={conclusion})"
    if state:
        return f"{name} (state={state})"
    if status:
        return f"{name} (status={status})"
    if conclusion:
        return f"{name} (conclusion={conclusion})"
    return name


def _get_review_threads_node(raw: Mapping[str, Any]) -> Mapping[str, Any] | list[Any] | None:
    if "reviewThreads" in raw:
        review_threads = raw["reviewThreads"]
        if isinstance(review_threads, (Mapping, list)):
            return review_threads

    graphql_data = raw.get("data")
    if isinstance(graphql_data, Mapping):
        repository = graphql_data.get("repository")
        if isinstance(repository, Mapping):
            pull_request = repository.get("pullRequest")
            if isinstance(pull_request, Mapping):
                review_threads = pull_request.get("reviewThreads")
                if isinstance(review_threads, (Mapping, list)):
                    return review_threads

    return None


def _iter_review_threads(raw: Mapping[str, Any]) -> list[Any]:
    review_threads = _get_review_threads_node(raw)
    if isinstance(review_threads, list):
        return review_threads
    if isinstance(review_threads, Mapping):
        return _as_list(review_threads.get("nodes"))
    return []


def _collect_review_thread_error_payload(raw: Mapping[str, Any]) -> list[str]:
    signals: list[str] = []
    for key in (
        "reviewThreadsDiagnostic",
        "review_threads_diagnostic",
        "review_threads_unavailable",
        "reviewThreadsUnavailable",
        "review-threads-unavailable",
        "reviewThreadsUnavailableReason",
    ):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            signals.append(value.strip())
    return _dedupe(signals)


def _normalize_graphql_messages(errors: list[Any]) -> list[str]:
    messages = [
        _normalize_text(error.get("message")) for error in errors if isinstance(error, Mapping)
    ]
    messages = [message for message in messages if message]
    if not messages:
        return []
    return [f"Review threads unavailable: {'; '.join(messages)}"]


def _normalize_graphql_error_text(raw: Mapping[str, Any]) -> list[str]:
    return _normalize_graphql_messages(raw.get("errors", []))


def _is_unresolved(node: Mapping[str, Any]) -> bool:
    if _normalize_lower(node.get("isResolved")) == "false":
        return True
    if _normalize_lower(node.get("is_resolved")) == "false":
        return True
    return _normalize_lower(node.get("state")) in {"open", "unresolved"}


def _first_comment(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        for item in _as_list(value.get("nodes")):
            if isinstance(item, Mapping):
                return item
        if _is_comment_like(value):
            return value

    for item in _as_list(value):
        if isinstance(item, Mapping):
            return item
    return None


def _is_comment_like(value: Mapping[str, Any]) -> bool:
    return isinstance(value.get("body"), str) or (
        isinstance(value.get("path"), str) and value.get("body") is not None
    )


def _format_thread_signal(kind: str, comment: Mapping[str, Any]) -> str:
    path = comment.get("path")
    line = comment.get("line")
    body = _truncate((comment.get("body") or "").strip())

    location = None
    if isinstance(path, str) and path.strip():
        location = path.strip()
        if isinstance(line, int):
            location = f"{location}:{line}"

    signal = f"Unresolved {kind}"
    if location:
        signal = f"{signal}: {location}"
    if body:
        signal = f"{signal} - {body}"
    return signal


def _normalize_lower(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _normalize_upper(value: Any) -> str:
    return _normalize_lower(value).upper()


def _normalize_bool(value: Any) -> bool | None:
    value = _normalize_lower(value)
    if value in {"1", "true", "yes", "y"}:
        return True
    if value in {"0", "false", "no", "n"}:
        return False
    return None


def _as_check_name(node: Mapping[str, Any]) -> str | None:
    if isinstance(node.get("name"), str):
        return _normalize_text(node.get("name")) or None
    if isinstance(node.get("context"), str):
        return _normalize_text(node.get("context")) or None
    return None


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _truncate(value: str, max_len: int = 180) -> str:
    if len(value) <= max_len:
        return value
    return f"{value[: max_len - 3]}..."


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
