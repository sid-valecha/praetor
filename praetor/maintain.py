from collections.abc import Callable, Iterable
from hashlib import sha1
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from praetor.dag import compute_ready_set
from praetor.models import MAX_TASK_ID_LENGTH, Task, TaskStatus
from praetor.recovery import latest_review_failure
from praetor.run_history import latest_run as load_latest_run
from praetor.state import list_tasks
from praetor.task_creation import create_task

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


def write_proposals_to_tasks(
    repo_root: Path,
    proposals: list[MaintainItem],
) -> tuple[list[str], list[str]]:
    """Create task files for proposals using deterministic IDs, skipping duplicates."""
    existing_tasks = list_tasks(repo_root)
    existing_tasks_by_id = {task.id: task for task in existing_tasks}
    existing_task_ids = set(existing_tasks_by_id)
    created_task_ids: list[str] = []
    skipped_task_ids: list[str] = []

    for proposal in proposals:
        task_id = _proposal_task_id(proposal)
        if task_id in existing_task_ids:
            skipped_task_ids.append(task_id)
            continue

        covered_task_id = _existing_task_covering_proposal(
            existing_tasks_by_id.values(),
            proposal,
        )
        if covered_task_id is not None:
            skipped_task_ids.append(covered_task_id)
            continue

        created_task = create_task(
            repo_root=repo_root,
            title=proposal.title or proposal.source,
            depends_on=[],
            verify=proposal.suggested_verify,
            context_files=proposal.context_files,
            body=_proposal_task_body(proposal),
            task_id=task_id,
        )
        created_task_ids.append(task_id)
        existing_task_ids.add(task_id)
        existing_tasks_by_id[task_id] = created_task

    return created_task_ids, skipped_task_ids


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


def _proposal_task_id(item: MaintainItem) -> str:
    seed = f"{item.source}|{item.url or ''}|{_proposal_feedback_signature(item)}"
    return _proposal_task_id_from_seed(item, seed)


def _legacy_proposal_task_id(item: MaintainItem) -> str:
    seed = f"{item.source}|{item.url or ''}"
    return _proposal_task_id_from_seed(item, seed)


def _proposal_task_id_from_seed(item: MaintainItem, seed: str) -> str:
    digest = sha1(seed.encode("utf-8")).hexdigest()[:8]
    source_slug = _slugify_for_task_id(item.source)
    max_slug_length = MAX_TASK_ID_LENGTH - len("maintain-") - 1 - len(digest)
    if max_slug_length < 1:
        max_slug_length = 1
    return f"maintain-{source_slug[:max_slug_length]}-{digest}"


def _existing_task_covering_proposal(
    existing_tasks: Iterable[Task],
    proposal: MaintainItem,
) -> str | None:
    legacy_task_id = _legacy_proposal_task_id(proposal)
    for task in existing_tasks:
        if task.id == legacy_task_id and _task_matches_proposal(task, proposal):
            return task.id

    for task in existing_tasks:
        if _task_matches_proposal(task, proposal):
            return task.id

    return None


def _task_matches_proposal(task: Task, proposal: MaintainItem) -> bool:
    signature = _proposal_feedback_proof(proposal)
    task_ids = {
        _proposal_task_id(proposal),
        _legacy_proposal_task_id(proposal),
    }
    if not signature:
        return task.id in task_ids

    body = task.body or ""
    if task.id not in task_ids and not _task_body_scopes_to_proposal(body, proposal):
        return False
    return all(line in body for line in signature.splitlines() if line)


def _task_body_scopes_to_proposal(body: str, proposal: MaintainItem) -> bool:
    if proposal.url and proposal.url in body:
        return True
    return proposal.source in body


def _proposal_feedback_signature(item: MaintainItem) -> str:
    proof = _proposal_feedback_proof(item)
    context_files = "\0".join(sorted(item.context_files))
    return "\0".join(
        [
            proof,
            context_files,
            item.blocker or "",
            item.next_action,
        ]
    )


def _proposal_feedback_proof(item: MaintainItem) -> str:
    proof_lines = item.proof.splitlines()
    if proof_lines and re.match(r"^(?:Pull request|Issue) #\d+:", proof_lines[0]):
        proof_lines = proof_lines[1:]

    if item.source.startswith("github:pull_request:"):
        actionable_lines: list[str] = []
        collecting_actionable_block = False
        for line in proof_lines:
            if _is_actionable_pr_proof_line(line):
                collecting_actionable_block = True
                actionable_lines.append(line.strip())
            elif collecting_actionable_block:
                actionable_lines.append(line.strip())
        if actionable_lines:
            return "\n".join(actionable_lines)

    return "\n".join(proof_lines).strip()


def _is_actionable_pr_proof_line(line: str) -> bool:
    line = line.strip()
    return line.startswith(
        (
            "Unresolved review thread:",
            "Unresolved outdated review thread:",
            "Unresolved review signal:",
            "Unresolved review comment:",
            "Latest review:",
            "Review decision:",
            "Failing check:",
            "Pending check:",
            "Unknown check state:",
        )
    )


def _proposal_task_body(item: MaintainItem) -> str:
    heading = f"# {item.title or item.source}"
    if item.description is not None:
        return f"{heading}\n\n{item.description}"

    lines = [
        heading,
        "",
        f"Source: {item.url or item.source}",
        f"Fit: {item.fit}",
        f"Risk: {item.risk}",
        f"Proof: {item.proof}",
        f"Blocker: {item.blocker or 'No explicit blocker.'}",
        f"Next action: {item.next_action}",
    ]
    return "\n".join(lines)


def _slugify_for_task_id(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "proposal"


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
        path = (match.group("line_path") or match.group("dotted_path")).strip()
        if not path or path in files:
            continue
        if not _is_context_file_candidate(path):
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


def _is_context_file_candidate(path: str) -> bool:
    if not any(char.isalpha() for char in path):
        return False
    if "/" in path:
        return True
    if "." not in path:
        return True

    suffix = path.rsplit(".", 1)[-1]
    return any(char.isalpha() for char in suffix)


_GITHUB_NUMBER_RE = re.compile(r"#(?P<number>\d+)$")
_CONTEXT_FILE_RE = re.compile(
    r"(?<![\w./:-])"
    r"(?:"
    r"(?P<line_path>[A-Za-z0-9._/-]+):\d+"
    r"|(?P<dotted_path>[A-Za-z0-9._/-]+\.[A-Za-z0-9_][A-Za-z0-9._-]*)"
    r")"
    r"(?!(?::\d+)?\w)",
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
