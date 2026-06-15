from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel

GHClassification = Literal["autonomous", "needs_owner", "defer"]
GhRunner = Callable[[list[str]], tuple[int, str, str]]

_REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          isResolved
          isOutdated
          comments(first: 20) {
            nodes {
              body
              path
              line
              author {
                login
              }
            }
          }
        }
      }
    }
  }
}
"""


class GitHubIntakeItem(BaseModel):
    source: str
    url: str | None = None
    classification: GHClassification
    fit: str
    risk: str
    proof: str
    blocker: str | None = None
    next_action: str

    def to_maintain_payload(self) -> dict[str, Any]:
        """Convert to a dictionary shaped like MaintainItem data."""
        return {
            "source": self.source,
            "url": self.url,
            "classification": self.classification,
            "fit": self.fit,
            "risk": self.risk,
            "proof": self.proof,
            "blocker": self.blocker,
            "next_action": self.next_action,
        }


class _ScanError(RuntimeError):
    pass


def scan_github_intake(
    repo: str | None = None,
    *,
    issue_number: int | None = None,
    pr_number: int | None = None,
    runner: GhRunner | None = None,
) -> list[GitHubIntakeItem]:
    run = runner or _run_gh_json
    if issue_number is not None and pr_number is not None:
        return [_diagnostic_item("Choose only one focused GitHub target: issue or pull request.")]

    issue_command = [
        "gh",
        "issue",
        "list",
        "--state",
        "open",
        "--limit",
        "100",
        "--json",
        "number,title,body,labels,url",
    ]
    pr_command = [
        "gh",
        "pr",
        "list",
        "--state",
        "open",
        "--limit",
        "100",
        "--json",
        "number,title,body,labels,url,state,mergedAt,reviewDecision,latestReviews,reviews,comments,statusCheckRollup,commits",
    ]
    if repo is not None:
        issue_command[3:3] = ["--repo", repo]
        pr_command[3:3] = ["--repo", repo]

    diagnostics: list[GitHubIntakeItem] = []
    try:
        if issue_number is not None:
            issue = _run_object_query(
                run,
                _build_issue_view_command(repo, issue_number),
                f"issue #{issue_number}",
            )
            issues = [issue]
            pull_requests: list[dict[str, Any]] = []
        elif pr_number is not None:
            pull_request = _run_object_query(
                run,
                _build_pr_view_command(repo, pr_number),
                f"pull request #{pr_number}",
            )
            issues = []
            pull_requests = [pull_request]
        else:
            try:
                issues = _run_query(run, issue_command, "open issues")
            except _ScanError as exc:
                issues = []
                if not _is_issues_disabled_error(exc):
                    diagnostics.append(_diagnostic_item(str(exc)))
            try:
                pull_requests = _run_query(run, pr_command, "open pull requests")
            except _ScanError as exc:
                pull_requests = []
                diagnostics.append(_diagnostic_item(str(exc)))
    except _ScanError as exc:
        return [_diagnostic_item(str(exc))]

    if not issues and not pull_requests and diagnostics:
        return [diagnostics[0]]

    repo_slug = repo
    if repo_slug is None and pull_requests:
        repo_slug = _resolve_repo_slug(run)

    items: list[GitHubIntakeItem] = list(diagnostics)
    for item in issues:
        parsed = _classify_issue(repo or "current", item)
        if parsed is not None:
            items.append(parsed)
    for item in pull_requests:
        review_thread_signals = _fetch_review_thread_signals(
            run,
            repo_slug,
            item.get("number"),
        )
        parsed = _classify_pull_request(
            repo or "current",
            item,
            extra_review_signals=review_thread_signals,
        )
        if parsed is not None:
            items.append(parsed)
    return items


def _is_issues_disabled_error(exc: _ScanError) -> bool:
    return "issues are disabled" in str(exc).lower()


def scan_github(repo_root: Path) -> list[GitHubIntakeItem]:
    def runner(command: list[str]) -> tuple[int, str, str]:
        return _run_gh_json(command, cwd=repo_root)

    return scan_github_intake(runner=runner)


def scan_focused_github(
    repo_root: Path,
    *,
    issue_number: int | None = None,
    pr_number: int | None = None,
) -> list[GitHubIntakeItem]:
    def runner(command: list[str]) -> tuple[int, str, str]:
        return _run_gh_json(command, cwd=repo_root)

    return scan_github_intake(
        issue_number=issue_number,
        pr_number=pr_number,
        runner=runner,
    )


def _build_issue_view_command(repo: str | None, issue_number: int) -> list[str]:
    command = [
        "gh",
        "issue",
        "view",
        str(issue_number),
        "--json",
        "number,title,body,labels,url,state",
    ]
    if repo is not None:
        command[4:4] = ["--repo", repo]
    return command


def _build_pr_view_command(repo: str | None, pr_number: int) -> list[str]:
    command = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--json",
        "number,title,body,labels,url,state,mergedAt,reviewDecision,latestReviews,reviews,comments,statusCheckRollup,commits,mergeStateStatus,mergeable,isDraft",
    ]
    if repo is not None:
        command[4:4] = ["--repo", repo]
    return command


def _run_query(
    runner: GhRunner,
    command: list[str],
    context: str,
) -> list[dict[str, Any]]:
    try:
        return_code, stdout, stderr = runner(command)
    except FileNotFoundError as exc:
        raise _ScanError(f"Cannot run gh for {context}; command missing: {exc}")
    except subprocess.TimeoutExpired as exc:
        raise _ScanError(f"Timed out running gh for {context} after {exc.timeout} seconds") from exc

    if return_code != 0:
        error = (stderr or "").strip() or f"gh returned exit code {return_code}"
        raise _ScanError(f"Cannot run gh for {context}: {error}")

    if not stdout.strip():
        return []

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise _ScanError(f"Failed to parse gh JSON for {context}: {exc}") from exc

    if not isinstance(parsed, list):
        raise _ScanError(f"Unexpected gh JSON shape for {context}; expected list")

    return parsed


def _run_object_query(
    runner: GhRunner,
    command: list[str],
    context: str,
) -> dict[str, Any]:
    try:
        return_code, stdout, stderr = runner(command)
    except FileNotFoundError as exc:
        raise _ScanError(f"Cannot run gh for {context}; command missing: {exc}")
    except subprocess.TimeoutExpired as exc:
        raise _ScanError(f"Timed out running gh for {context} after {exc.timeout} seconds") from exc

    if return_code != 0:
        error = (stderr or "").strip() or f"gh returned exit code {return_code}"
        raise _ScanError(f"Cannot run gh for {context}: {error}")

    if not stdout.strip():
        return {}

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise _ScanError(f"Failed to parse gh JSON for {context}: {exc}") from exc

    if not isinstance(parsed, dict):
        raise _ScanError(f"Unexpected gh JSON shape for {context}; expected object")

    return parsed


def _run_gh_json(command: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        cwd=cwd,
        text=True,
        timeout=15,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _classify_issue(repo: str, raw: dict[str, Any]) -> GitHubIntakeItem | None:
    number = raw.get("number")
    if number is None:
        return None

    number_text = str(number)
    source = f"github:issue:{repo}#{number_text}"
    title = (raw.get("title") or "").strip()
    body = (raw.get("body") or "").strip()
    url = raw.get("url")

    proof = f"Issue #{number_text}: {title}" if title else f"Issue #{number_text}"
    if body:
        proof = f"{proof}\n{_truncate(body)}"

    if _is_issue_closed(raw):
        return GitHubIntakeItem(
            source=source,
            url=_as_url(url),
            classification="defer",
            fit="Issue is already closed; intake is historical.",
            risk="No active owner triage remains for this issue.",
            proof=proof + "\nState: closed.",
            blocker=None,
            next_action="No action required for this closed issue.",
        )

    return GitHubIntakeItem(
        source=source,
        url=_as_url(url),
        classification="needs_owner",
        fit="Open issue requires owner triage before autonomous execution.",
        risk="No owner review has yet been applied to this request.",
        proof=proof,
        blocker="Issue is open and user-facing; requires human review.",
        next_action="Owner: triage issue and create praetor task with verification.",
    )


def _classify_pull_request(
    repo: str,
    raw: dict[str, Any],
    *,
    extra_review_signals: list[str] | None = None,
) -> GitHubIntakeItem | None:
    number = raw.get("number")
    if number is None:
        return None

    number_text = str(number)
    source = f"github:pull_request:{repo}#{number_text}"
    title = (raw.get("title") or "").strip()
    body = (raw.get("body") or "").strip()
    url = _as_url(raw.get("url"))

    proof = f"Pull request #{number_text}: {title}" if title else f"Pull request #{number_text}"
    if body:
        proof = f"{proof}\n{_truncate(body)}"

    review_decision = _normalize_review_decision(raw.get("reviewDecision"))
    extra_review_signals, review_thread_diagnostics = _split_review_thread_signals(
        extra_review_signals or []
    )

    if _is_pr_merged(raw):
        if extra_review_signals or review_thread_diagnostics:
            proof = proof + "\nHistorical review thread(s) still visible after merge."
        return GitHubIntakeItem(
            source=source,
            url=url,
            classification="defer",
            fit="Pull request is already merged; intake is historical.",
            risk="No active merge-blocking action remains for this PR.",
            proof=proof + "\nState: merged.",
            blocker=None,
            next_action="No action required for this merged PR.",
        )

    if _is_pr_closed_unmerged(raw):
        return GitHubIntakeItem(
            source=source,
            url=url,
            classification="defer",
            fit="Pull request is already closed without merge; intake is historical.",
            risk="No active merge-blocking action remains for this closed PR.",
            proof=proof + "\nState: closed.",
            blocker=None,
            next_action="No action required for this closed PR.",
        )

    review_signals = _collect_review_signals(raw)
    if extra_review_signals:
        review_signals.extend(extra_review_signals)
    check_failures = _collect_check_failures(raw)

    if review_signals:
        return GitHubIntakeItem(
            source=source,
            url=url,
            classification="needs_owner",
            fit="Open PR has review feedback that needs owner action.",
            risk="Applying changes without review closure can introduce regressions.",
            proof=proof + "\n" + "\n".join(review_signals),
            blocker="Open review feedback must be resolved.",
            next_action="Owner: resolve review feedback before merge.",
        )

    if review_decision == "CHANGES_REQUESTED":
        return GitHubIntakeItem(
            source=source,
            url=url,
            classification="needs_owner",
            fit="Open PR review decision requires changes before merge.",
            risk="Reviewer requested changes; merge would violate author feedback.",
            proof=proof + "\nReview decision: changes requested.",
            blocker="Reviewer requested changes.",
            next_action="Owner: resolve requested changes and ask author to rebase/refresh.",
        )

    if check_failures:
        return GitHubIntakeItem(
            source=source,
            url=url,
            classification="needs_owner",
            fit="Open PR has failing checks that block safe completion.",
            risk="Failing checks reduce confidence in merge safety.",
            proof=proof + "\n" + "\n".join(check_failures),
            blocker="CI/checks are not yet passing.",
            next_action="Owner: wait for checks to pass or request a remediation action from the author.",
        )

    if review_thread_diagnostics:
        return GitHubIntakeItem(
            source=source,
            url=url,
            classification="needs_owner",
            fit="Open PR review-thread intake is unavailable.",
            risk="Review state cannot be fully confirmed until GitHub thread intake succeeds.",
            proof=proof + "\n" + "\n".join(review_thread_diagnostics),
            blocker="Review-thread intake is unavailable.",
            next_action="Owner: fix GitHub auth/API access and rerun the intake.",
        )

    if review_decision == "APPROVED" and _checks_are_passing(raw):
        return GitHubIntakeItem(
            source=source,
            url=url,
            classification="defer",
            fit="Open PR is approved with passing checks; no immediate owner action required.",
            risk="Minimal; monitor merge completion but avoid mutation from this intake slice.",
            proof=proof + "\nReview decision: approved.\nChecks: passing.",
            blocker=None,
            next_action="No mutation required now; monitor for merge progress.",
        )

    return GitHubIntakeItem(
        source=source,
        url=url,
        classification="defer",
        fit="Open PR is pending review/check finalization.",
        risk="Low-risk informational item until review or checks complete.",
        proof=proof + "\nStatus: open and not explicitly blocked.",
        blocker=None,
        next_action="No mutation required now; monitor for final merge conditions.",
    )


def _split_review_thread_signals(signals: list[str]) -> tuple[list[str], list[str]]:
    review_signals: list[str] = []
    diagnostics: list[str] = []
    for signal in signals:
        if _is_review_thread_diagnostic(signal):
            diagnostics.append(signal)
        else:
            review_signals.append(signal)
    return review_signals, diagnostics


def _is_review_thread_diagnostic(signal: str) -> bool:
    return signal.startswith("Review threads unavailable:")


def _is_issue_closed(raw: dict[str, Any]) -> bool:
    state = _normalize_review_decision(raw.get("state"))
    return state == "CLOSED"


def _resolve_repo_slug(runner: GhRunner) -> str | None:
    try:
        data = _run_object_query(
            runner,
            ["gh", "repo", "view", "--json", "owner,name"],
            "current repository",
        )
    except _ScanError:
        return None

    owner = data.get("owner")
    owner_login = owner.get("login") if isinstance(owner, dict) else None
    name = data.get("name")
    if isinstance(owner_login, str) and isinstance(name, str):
        return f"{owner_login}/{name}"
    return None


def _is_pr_merged(raw: dict[str, Any]) -> bool:
    state = _normalize_review_decision(raw.get("state"))
    merged_at = raw.get("mergedAt")
    return state == "MERGED" or (isinstance(merged_at, str) and bool(merged_at.strip()))


def _is_pr_closed_unmerged(raw: dict[str, Any]) -> bool:
    state = _normalize_review_decision(raw.get("state"))
    return state == "CLOSED" and not _is_pr_merged(raw)


def _fetch_review_thread_signals(
    runner: GhRunner,
    repo: str | None,
    number: object,
) -> list[str]:
    if repo is None or number is None:
        return []
    if "/" not in repo:
        return []

    owner, name = repo.split("/", 1)
    try:
        data = _run_object_query(
            runner,
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={_REVIEW_THREADS_QUERY}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"number={number}",
            ],
            f"review threads for PR #{number}",
        )
    except _ScanError as exc:
        return [f"Review threads unavailable: {exc}"]

    return _collect_review_thread_signals(data)


def _collect_review_thread_signals(data: dict[str, Any]) -> list[str]:
    payload = data.get("data")
    if not isinstance(payload, dict):
        return _graphql_unavailable_signal(data)

    repository = payload.get("repository")
    if not isinstance(repository, dict):
        return _graphql_unavailable_signal(data)

    pull_request = repository.get("pullRequest")
    if not isinstance(pull_request, dict):
        return _graphql_unavailable_signal(data)

    review_threads = pull_request.get("reviewThreads")
    if not isinstance(review_threads, dict):
        return []

    signals: list[str] = []
    for thread in _to_list(review_threads.get("nodes")):
        if not isinstance(thread, dict) or not _is_unresolved(thread):
            continue
        if thread.get("isOutdated") is True:
            continue
        comments = thread.get("comments")
        comment_nodes = comments.get("nodes") if isinstance(comments, dict) else []
        comment = _first_dict(comment_nodes)
        if comment is None:
            signals.append("Unresolved review thread.")
            continue

        location = _format_review_comment_location(comment)
        body = _truncate((comment.get("body") or "").strip())
        if location and body:
            signals.append(f"Unresolved review thread: {location} - {body}")
        elif location:
            signals.append(f"Unresolved review thread: {location}")
        elif body:
            signals.append(f"Unresolved review thread: {body}")
        else:
            signals.append("Unresolved review thread.")
    return signals


def _graphql_unavailable_signal(data: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    for error in _to_list(data.get("errors")):
        if not isinstance(error, dict):
            continue
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            messages.append(message.strip())
    if messages:
        return [f"Review threads unavailable: {'; '.join(messages)}"]
    return ["Review threads unavailable: GraphQL response did not include pull request data."]


def _first_dict(value: object) -> dict[str, Any] | None:
    for item in _to_list(value):
        if isinstance(item, dict):
            return item
    return None


def _format_review_comment_location(comment: dict[str, Any]) -> str | None:
    path = comment.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    line = comment.get("line")
    if isinstance(line, int):
        return f"{path}:{line}"
    return path


def _collect_review_signals(raw: dict[str, Any]) -> list[str]:
    signals: list[str] = []

    latest_reviews = _to_list(raw.get("latestReviews"))
    for review in latest_reviews:
        if not isinstance(review, dict):
            continue
        state = _normalize_review_decision(review.get("state"))
        if state == "CHANGES_REQUESTED":
            body = _truncate((review.get("body") or "").strip())
            if body:
                signals.append(f"Latest review: CHANGES_REQUESTED - {body}")
            else:
                signals.append("Latest review: CHANGES_REQUESTED.")
        if _is_unresolved(review):
            body = _truncate((review.get("body") or "").strip())
            if body:
                signals.append(f"Unresolved review signal: {body}")

    for comment in _to_list(raw.get("comments")):
        if not isinstance(comment, dict):
            continue
        if _is_unresolved(comment):
            body = _truncate((comment.get("body") or "").strip())
            if body:
                signals.append(f"Unresolved review comment: {body}")
    return signals


def _collect_check_failures(raw: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    _collect_check_failures_from_node(raw, failures)
    return failures


def _checks_are_passing(raw: dict[str, Any]) -> bool:
    rollup = raw.get("statusCheckRollup")
    return _check_node_is_passing(rollup)


def _check_node_is_passing(value: Any) -> bool:
    if isinstance(value, list):
        return bool(value) and all(_check_node_is_passing(item) for item in value)

    if not isinstance(value, dict):
        return False

    conclusion = _normalize_lower_text(value.get("conclusion"))
    state = _normalize_lower_text(value.get("state"))
    status = _normalize_lower_text(value.get("status"))

    if conclusion and conclusion not in {"success", "passed", "neutral", "skipped"}:
        return False
    if state and state not in {"success", "passed", "completed"}:
        return False
    if status and status not in {"success", "passed", "completed"}:
        return False

    child_results: list[bool] = []
    for key in ("checkRuns", "nodes", "checks", "commits", "statusCheckRollup"):
        if key in value:
            child_results.append(_check_node_is_passing(value.get(key)))
    if child_results:
        return all(child_results)

    if conclusion in {"success", "passed", "neutral", "skipped"}:
        return True
    if state in {"success", "passed"}:
        return True
    return status in {"success", "passed"}


def _collect_check_failures_from_node(value: Any, failures: list[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_check_failures_from_node(item, failures)
        return

    if not isinstance(value, dict):
        return

    if _is_check_status_failing(value):
        raw_name = value.get("name")
        name = raw_name if isinstance(raw_name, str) else None
        failures.append(_format_check_failure(name, value))

    if "checkRuns" in value:
        _collect_check_failures_from_node(value.get("checkRuns"), failures)
    if "nodes" in value:
        _collect_check_failures_from_node(value.get("nodes"), failures)
    if "checks" in value:
        _collect_check_failures_from_node(value.get("checks"), failures)
    if "commits" in value:
        _collect_check_failures_from_node(value.get("commits"), failures)
    if "statusCheckRollup" in value and not _looks_like_check_object(value):
        _collect_check_failures_from_node(value.get("statusCheckRollup"), failures)


def _looks_like_check_object(node: dict[str, Any]) -> bool:
    return any(key in node for key in ("name", "status", "conclusion"))


def _is_check_status_failing(node: dict[str, Any]) -> bool:
    conclusion = _normalize_lower_text(node.get("conclusion"))
    state = _normalize_lower_text(node.get("state"))
    status = _normalize_lower_text(node.get("status"))

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
    passed = {"success", "passed", "skipped", "neutral", "ok"}

    if conclusion and conclusion in failing:
        return True
    if state and state in failing:
        return True
    if status and status in {
        "failed",
        "failure",
        "timed_out",
        "cancelled",
        "action_required",
        "startup_failure",
        "stale",
    }:
        return True
    if conclusion and state and conclusion == "failure":
        return True
    if state == "failure" and status == "completed":
        return True
    if (
        state
        and state in {"completed", "success"}
        and conclusion
        and conclusion in {"failure", "timed_out", "cancelled"}
    ):
        return True
    if state in passed or conclusion in passed or status in passed:
        return False
    if state == "completed" and status == "completed":
        return False
    return False


def _format_check_failure(name: str | None, node: dict[str, Any]) -> str:
    check_name = name or "check"
    conclusion = _normalize_text(node.get("conclusion")) or "unknown"
    status = _normalize_text(node.get("status")) or _normalize_text(node.get("state")) or "unknown"
    return f"Failing check: {check_name} (status={status}, conclusion={conclusion})."


def _is_unresolved(node: dict[str, Any]) -> bool:
    if _normalize_lower_text(node.get("isResolved")) == "false":
        return True
    if _normalize_lower_text(node.get("is_resolved")) == "false":
        return True
    state = _normalize_lower_text(node.get("state"))
    return state in {"open", "unresolved"}


def _as_url(raw_url: object) -> str | None:
    if isinstance(raw_url, str):
        stripped = raw_url.strip()
        return stripped if stripped else None
    return None


def _normalize_review_decision(value: object) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return ""
    return normalized.upper()


def _normalize_text(value: object) -> str:
    return _normalize_lower_text(value).upper()


def _normalize_lower_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text.lower() if text else ""


def _to_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return []


def _diagnostic_item(message: str) -> GitHubIntakeItem:
    return GitHubIntakeItem(
        source="github:intake",
        classification="needs_owner",
        fit="GitHub intake failed; cannot complete remote scan safely.",
        risk="Without remote scan, maintenance items may be incomplete or stale.",
        proof=message,
        blocker="GitHub CLI command failed.",
        next_action="Owner: fix `gh` installation/auth and rerun the scan.",
        url=None,
    )


def _truncate(value: str, max_len: int = 220) -> str:
    if len(value) <= max_len:
        return value
    return f"{value[: max_len - 3]}..."
