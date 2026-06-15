from collections.abc import Callable, Iterable
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from praetor.dag import compute_ready_set
from praetor.models import Task, TaskStatus
from praetor.recovery import latest_review_failure
from praetor.run_history import latest_run as load_latest_run
from praetor.state import list_tasks

Classification = Literal["autonomous", "needs_owner", "defer"]
GithubProvider = Callable[..., Iterable["MaintainItem"]]


class MaintainItem(BaseModel):
    source: str
    url: str | None = None
    classification: Classification
    fit: str
    risk: str
    proof: str
    blocker: str | None = None
    next_action: str
    title: str | None = None
    description: str | None = None
    suggested_verify: str | None = None
    context_files: list[str] = Field(default_factory=list)


class LatestRunSummary(BaseModel):
    id: str
    status: str


class MaintainScan(BaseModel):
    repo_root: str
    items: list[MaintainItem] = Field(default_factory=list)
    latest_run: LatestRunSummary | None = None


def scan(
    repo_root: Path,
    *,
    include_github: bool = False,
    github_pr: int | None = None,
    github_issue: int | None = None,
    github_provider: GithubProvider | None = None,
) -> MaintainScan:
    """Read-only scan of local Praetor state for the current repo."""
    tasks = list_tasks(repo_root)
    ready_ids = {task.id for task in compute_ready_set(tasks)}
    tasks_by_id = {task.id: task for task in tasks}

    items: list[MaintainItem] = []
    for task in tasks:
        item = _classify_task(repo_root, task, ready_ids, tasks_by_id)
        if item is not None:
            items.append(item)

    if include_github or github_pr is not None or github_issue is not None:
        provider = github_provider or _default_github_provider
        if github_pr is None and github_issue is None:
            items.extend(provider(repo_root))
        else:
            items.extend(provider(repo_root, github_pr=github_pr, github_issue=github_issue))

    latest_run_record = load_latest_run(repo_root)
    latest_run = (
        LatestRunSummary(id=latest_run_record.id, status=latest_run_record.status)
        if latest_run_record is not None
        else None
    )

    return MaintainScan(
        repo_root=str(repo_root),
        items=items,
        latest_run=latest_run,
    )


def proposals_from_scan(scan_result: MaintainScan) -> list[MaintainItem]:
    """Return deterministic repair proposals from deterministic scan findings."""
    proposals: list[MaintainItem] = []
    for item in scan_result.items:
        proposal = as_repair_proposal(item)
        if proposal is not None:
            proposals.append(proposal)
    return proposals


def as_repair_proposal(item: MaintainItem) -> MaintainItem | None:
    """Convert a maintain item into a deterministic task-shaped proposal."""
    if item.classification != "needs_owner":
        return None
    if not _is_actionable_github_source(item.source):
        return None
    if _is_review_thread_intake_diagnostic(item):
        return None

    context_files = _extract_context_files(item.proof)
    return item.model_copy(
        update={
            "title": _proposal_title(item),
            "description": _proposal_description(item),
            "suggested_verify": _infer_suggested_verify(item),
            "context_files": context_files,
        }
    )


def _proposal_title(item: MaintainItem) -> str:
    if item.source.startswith("github:pull_request:"):
        number = _extract_github_number(item.source) or "PR"
        summary = _extract_subject_from_proof(item.proof, "Pull request")
        return f"Address pull request feedback for #{number}: {summary}"

    if item.source.startswith("github:issue:"):
        number = _extract_github_number(item.source) or "issue"
        summary = _extract_subject_from_proof(item.proof, "Issue")
        return f"Address issue #{number}: {summary}"

    if item.source.startswith("github:"):
        number = _extract_github_number(item.source)
        if number is not None:
            return f"Address GitHub item #{number}"
        return f"Address GitHub item: {item.source}"

    return item.source


def _is_actionable_github_source(source: str) -> bool:
    return source.startswith("github:issue:") or source.startswith("github:pull_request:")


def _is_review_thread_intake_diagnostic(item: MaintainItem) -> bool:
    if not item.source.startswith("github:pull_request:"):
        return False

    fit = item.fit.lower()
    proof = item.proof.lower()
    blocker = (item.blocker or "").lower()
    next_action = item.next_action.lower()
    return (
        "review-thread intake is unavailable" in fit
        or "review-thread intake is unavailable" in blocker
        or "review threads unavailable:" in proof
        or "fix github auth/api access and rerun the intake" in next_action
    )


def _proposal_description(item: MaintainItem) -> str:
    description_lines = [
        f"Source: {item.url or item.source}",
        f"Fit: {item.fit}",
        f"Risk: {item.risk}",
        f"Proof: {item.proof}",
        f"Blocker: {item.blocker or 'No explicit blocker.'}",
    ]
    return "\n".join(description_lines)


def _infer_suggested_verify(item: MaintainItem) -> str | None:
    del item
    return None


def _extract_github_number(source: str) -> str | None:
    match = _GITHUB_NUMBER_RE.search(source)
    if match is None:
        return None
    return match.group("number")


def _extract_subject_from_proof(proof: str, fallback: str) -> str:
    first_line = proof.splitlines()[0] if proof else ""
    if ":" in first_line:
        _, _, title = first_line.partition(": ")
        return title.strip() or fallback
    return fallback


def _extract_context_files(raw_proof: str) -> list[str]:
    files: list[str] = []
    for match in _CONTEXT_FILE_RE.finditer(raw_proof):
        path = match.group("path").strip()
        if not path or path in files:
            continue
        if (
            path.startswith("http://")
            or path.startswith("https://")
            or path.startswith("//")
            or _HOST_STYLE_PATH_RE.match(path.split("/")[0])
        ):
            continue
        files.append(path)
    return files


_GITHUB_NUMBER_RE = re.compile(r"#(?P<number>\d+)$")
_CONTEXT_FILE_RE = re.compile(
    r"(?<!\w)(?P<path>[A-Za-z0-9._/-]+\.[A-Za-z0-9_][A-Za-z0-9._-]*)"
    r"(?::\d+)?(?!(?::\d+)?\w)",
)
_HOST_STYLE_PATH_RE = re.compile(
    r"^[A-Za-z0-9_-]+\.(?:com|org|net|io|co|dev|app|edu|gov|info|me|ai)$"
)


def _default_github_provider(
    repo_root: Path,
    *,
    github_pr: int | None = None,
    github_issue: int | None = None,
) -> Iterable[MaintainItem]:
    try:
        from praetor.github_intake import scan_focused_github, scan_github
    except ModuleNotFoundError:
        return [
            MaintainItem(
                source="github:intake",
                classification="needs_owner",
                fit="GitHub intake was requested but the provider is unavailable.",
                risk="External issue, PR, and CI state cannot be included in this scan.",
                proof="praetor.github_intake could not be imported.",
                blocker="GitHub provider module is unavailable.",
                next_action="Run local maintain scan without --github, or install a version with GitHub intake.",
            ),
        ]

    if github_pr is not None or github_issue is not None:
        raw_items = scan_focused_github(
            repo_root,
            pr_number=github_pr,
            issue_number=github_issue,
        )
    else:
        raw_items = scan_github(repo_root)
    return [MaintainItem(**item.to_maintain_payload()) for item in raw_items]


def _classify_task(
    repo_root: Path,
    task: Task,
    ready_ids: set[str],
    tasks_by_id: dict[str, Task],
) -> MaintainItem | None:
    source = f"task:{task.id}"
    status = task.status

    if status is TaskStatus.done:
        return None

    if status is TaskStatus.pending:
        if task.id in ready_ids:
            return _classify_ready_pending(source, task)
        return _classify_waiting_pending(source, task, tasks_by_id)

    if status is TaskStatus.running:
        return MaintainItem(
            source=source,
            classification="needs_owner",
            fit="Task is marked running; maintain does not verify runner liveness yet.",
            risk=(
                "A stale or active running state can block dependents; resetting an "
                "active task can cause duplicate execution or log overwrites."
            ),
            proof=_proof_with_verify(task, "Task currently marked running."),
            blocker="Task may be stale, but no liveness check has been performed.",
            next_action=(
                "Inspect active runners/logs, then run "
                f"praetor reset {task.id} only if no runner is active."
            ),
        )

    if status is TaskStatus.pending_merge:
        return MaintainItem(
            source=source,
            classification="needs_owner",
            fit="Task passed verify and is waiting for merge to base branch.",
            risk="Unmerged work accumulates and can conflict with later tasks.",
            proof=_proof_with_verify(task, "Verify already passed; awaiting merge."),
            blocker="Awaiting human merge decision (merge_strategy=manual).",
            next_action=f"praetor merge {task.id}",
        )

    if status is TaskStatus.merge_failed:
        return MaintainItem(
            source=source,
            classification="needs_owner",
            fit="Merge of completed task into base branch failed.",
            risk="Merge conflict or branch-state issue; requires human inspection.",
            proof=_proof_with_verify(task, "Verify passed but merge failed."),
            blocker="Merge failed; conflict or branch state needs human resolution.",
            next_action=f"praetor merge {task.id} --retry",
        )

    if status is TaskStatus.review_failed:
        return _classify_review_failed(repo_root, source, task)

    if status is TaskStatus.failed:
        return MaintainItem(
            source=source,
            classification="needs_owner",
            fit="Task failed during execution or verify.",
            risk="Failure may have left partial work; downstream tasks may be blocked.",
            proof=_proof_with_verify(task, "Task ended in failed state."),
            blocker="Task failed; reset and re-run after investigating logs.",
            next_action=f"praetor reset {task.id}",
        )

    if status is TaskStatus.blocked:
        return MaintainItem(
            source=source,
            classification="defer",
            fit="Task is blocked by an upstream failure or block.",
            risk="No autonomous action available until upstream is resolved.",
            proof=_proof_with_verify(task, "Task blocked by upstream failure."),
            blocker="Upstream dependency failed or is blocked.",
            next_action=f"praetor logs {task.id}",
        )

    if status is TaskStatus.cancelled:
        return MaintainItem(
            source=source,
            classification="defer",
            fit="Task was cancelled.",
            risk="No action expected; cancelled tasks are excluded from drains.",
            proof=_proof_with_verify(task, "Task is cancelled."),
            blocker=None,
            next_action="No action; cancelled tasks remain on disk for audit.",
        )

    return None


def _classify_ready_pending(source: str, task: Task) -> MaintainItem:
    if not task.verify:
        return MaintainItem(
            source=source,
            classification="needs_owner",
            fit="Pending task is ready to run but has no verify command.",
            risk="Without verify, executor output cannot be trusted; the trust gate is off.",
            proof="No verify command attached to this task.",
            blocker="Missing verify command; add one before letting an agent drain this task.",
            next_action=f"Edit .praetor/tasks/{task.id}.md to add a verify command.",
        )

    return MaintainItem(
        source=source,
        classification="autonomous",
        fit="Pending task is ready to run and has a verify command.",
        risk="Standard runner risk; verify and reviewer gates apply.",
        proof=f"Verify command: {task.verify}",
        blocker=None,
        next_action="praetor run",
    )


def _classify_waiting_pending(
    source: str,
    task: Task,
    tasks_by_id: dict[str, Task],
) -> MaintainItem:
    unresolved = [
        dep
        for dep in task.depends_on
        if tasks_by_id.get(dep) is None or tasks_by_id[dep].status is not TaskStatus.done
    ]
    blocker = (
        f"Waiting on dependencies: {', '.join(unresolved)}"
        if unresolved
        else "Waiting on unresolved dependencies."
    )
    return MaintainItem(
        source=source,
        classification="defer",
        fit="Pending task depends on work that is not yet done.",
        risk="No autonomous action until upstream tasks finish.",
        proof=_proof_with_verify(task, "Awaiting dependencies."),
        blocker=blocker,
        next_action="praetor status",
    )


def _classify_review_failed(repo_root: Path, source: str, task: Task) -> MaintainItem:
    review = latest_review_failure(repo_root, task.id)
    if review is not None:
        summary = review.get("summary") or "Reviewer requested revisions."
        blocker = str(summary)
        proof_lines = [f"Reviewer verdict: {review.get('verdict')}"]
        findings = review.get("findings") or []
        if findings:
            proof_lines.append(f"{len(findings)} reviewer finding(s) recorded.")
        if task.verify:
            proof_lines.append(f"Verify command: {task.verify}")
        proof = "\n".join(proof_lines)
    else:
        blocker = "Reviewer rejected the last attempt; no structured findings recorded."
        proof = _proof_with_verify(task, "Reviewer rejected the last attempt.")

    return MaintainItem(
        source=source,
        classification="needs_owner",
        fit="Task was rejected by the adversarial reviewer.",
        risk="Reviewer findings must be addressed before retry budget is reset.",
        proof=proof,
        blocker=blocker,
        next_action=f"praetor reset {task.id}",
    )


def _proof_with_verify(task: Task, prefix: str) -> str:
    if task.verify:
        return f"{prefix}\nVerify command: {task.verify}"
    return prefix
