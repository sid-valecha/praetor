from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from praetor.dag import compute_ready_set
from praetor.models import Task, TaskStatus
from praetor.recovery import latest_review_failure
from praetor.run_history import latest_run as load_latest_run
from praetor.state import list_tasks

Classification = Literal["autonomous", "needs_owner", "defer"]


class MaintainItem(BaseModel):
    source: str
    url: str | None = None
    classification: Classification
    fit: str
    risk: str
    proof: str
    blocker: str | None = None
    next_action: str


class LatestRunSummary(BaseModel):
    id: str
    status: str


class MaintainScan(BaseModel):
    repo_root: str
    items: list[MaintainItem] = Field(default_factory=list)
    latest_run: LatestRunSummary | None = None


def scan(repo_root: Path) -> MaintainScan:
    """Read-only scan of local Praetor state for the current repo."""
    tasks = list_tasks(repo_root)
    ready_ids = {task.id for task in compute_ready_set(tasks)}
    tasks_by_id = {task.id: task for task in tasks}

    items: list[MaintainItem] = []
    for task in tasks:
        item = _classify_task(repo_root, task, ready_ids, tasks_by_id)
        if item is not None:
            items.append(item)

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
