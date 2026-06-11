"""Task runner orchestration.

Only the main runner thread writes task markdown. Worker threads may execute
adapters, but they return TaskResult objects for the main thread to apply.
"""

from concurrent.futures import FIRST_COMPLETED, Future, wait
from dataclasses import dataclass
import subprocess
from pathlib import Path

from praetor.dag import compute_ready_set, propagate_blocked
from praetor.events import EventCallback, EventType, RunnerEvent
from praetor.merge import MergeResult, merge_task
from praetor.models import AgentAdapter, Task, TaskResult, TaskStatus
from praetor.pool import WorkerPool
from praetor.state import list_tasks, update_task_status
from praetor.worktree import Worktree, WorktreeError, create_worktree


class StaleRunningError(RuntimeError):
    """Raised when a previous run left tasks in running state."""


@dataclass(frozen=True)
class RunningTask:
    task: Task
    worktree: Worktree


def render_task_prompt(task: Task, context: str = "") -> str:
    parts = []
    if context:
        parts.append(context.strip())
    if task.body:
        parts.append(task.body.strip())
    if task.verify is not None:
        parts.append(f"Verify command: {task.verify}")
    return "\n\n".join(parts)


def run_once(
    repo_root: Path,
    adapter: AgentAdapter,
    on_event: EventCallback | None = None,
) -> bool:
    ready_tasks = compute_ready_set(list_tasks(repo_root))
    if not ready_tasks:
        return False

    task = ready_tasks[0]
    update_task_status(repo_root, task.id, TaskStatus.running)

    try:
        context_path = repo_root / ".praetor" / "context.md"
        context = context_path.read_text() if context_path.exists() else ""
        prompt = render_task_prompt(task, context)
        _emit(on_event, "task_dispatched", task_id=task.id)
        result = adapter.exec(prompt, cwd=repo_root)
    except Exception:
        _mark_failed_and_propagate(repo_root, task.id)
        _emit(on_event, "task_failed", task_id=task.id, detail="adapter exception")
        raise

    log_path = repo_root / ".praetor" / "logs" / f"{task.id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"{result.stdout}{result.stderr}")

    if result.exit_code != 0:
        _mark_failed_and_propagate(repo_root, task.id)
        _emit(on_event, "task_failed", task_id=task.id, detail="agent failed")
        return True

    if task.verify is None:
        update_task_status(repo_root, task.id, TaskStatus.done)
        _emit(on_event, "task_completed", task_id=task.id)
        return True

    verify_result = subprocess.run(
        task.verify,
        shell=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    with log_path.open("a") as log_file:
        log_file.write(f"{verify_result.stdout}{verify_result.stderr}")

    if verify_result.returncode != 0:
        _mark_failed_and_propagate(repo_root, task.id)
        _emit(on_event, "task_failed", task_id=task.id, detail="verify failed")
        return True

    update_task_status(repo_root, task.id, TaskStatus.done)
    _emit(on_event, "task_completed", task_id=task.id)
    return True


def drain_queue(
    repo_root: Path,
    adapter: AgentAdapter,
    max_parallel: int = 1,
    base_branch: str = "main",
    merge_strategy: str | None = None,
    on_event: EventCallback | None = None,
) -> None:
    _raise_on_stale_running(list_tasks(repo_root))

    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or max_parallel < 1:
        msg = "max_parallel must be >= 1"
        raise ValueError(msg)
    if merge_strategy not in {None, "auto", "manual"}:
        msg = "merge_strategy must be one of: auto, manual"
        raise ValueError(msg)

    _emit(on_event, "drain_started")
    try:
        if max_parallel == 1:
            while run_once(repo_root, adapter, on_event=on_event):
                pass
            return

        _drain_parallel(
            repo_root,
            adapter,
            max_parallel,
            base_branch,
            merge_strategy,
            on_event,
        )
    finally:
        _emit(on_event, "drain_finished")


def _drain_parallel(
    repo_root: Path,
    adapter: AgentAdapter,
    max_parallel: int,
    base_branch: str,
    merge_strategy: str | None,
    on_event: EventCallback | None,
) -> None:
    in_flight: dict[Future[TaskResult], RunningTask] = {}

    with WorkerPool(max_parallel) as pool:
        while True:
            made_progress = _submit_ready_tasks(
                repo_root,
                adapter,
                pool,
                in_flight,
                base_branch,
                on_event,
            )
            if not in_flight:
                if not made_progress:
                    return
                continue

            completed_futures, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in completed_futures:
                running_task = in_flight.pop(future)
                _complete_parallel_task(
                    repo_root,
                    future,
                    running_task,
                    merge_strategy,
                    on_event,
                )


def _submit_ready_tasks(
    repo_root: Path,
    adapter: AgentAdapter,
    pool: WorkerPool,
    in_flight: dict[Future[TaskResult], RunningTask],
    base_branch: str,
    on_event: EventCallback | None,
) -> bool:
    capacity = pool.max_parallel - len(in_flight)
    if capacity <= 0:
        return False

    ready_tasks = compute_ready_set(list_tasks(repo_root))
    if not ready_tasks:
        return False

    solo_tasks = [task for task in ready_tasks if not task.parallel_ok]
    if solo_tasks:
        if in_flight:
            return False
        return _submit_parallel_task(
            repo_root, adapter, pool, in_flight, solo_tasks[0], base_branch, on_event
        )

    submitted = False
    for task in ready_tasks[:capacity]:
        submitted = (
            _submit_parallel_task(repo_root, adapter, pool, in_flight, task, base_branch, on_event)
            or submitted
        )
    return submitted


def _submit_parallel_task(
    repo_root: Path,
    adapter: AgentAdapter,
    pool: WorkerPool,
    in_flight: dict[Future[TaskResult], RunningTask],
    task: Task,
    base_branch: str,
    on_event: EventCallback | None,
) -> bool:
    try:
        worktree = create_worktree(task.id, repo_root, base_branch=base_branch)
    except WorktreeError as exc:
        _write_task_log(repo_root, task.id, f"Worktree collision for {task.id}: {exc}\n")
        _mark_failed_and_propagate(repo_root, task.id)
        _emit(on_event, "task_failed", task_id=task.id, detail="worktree setup failed")
        return True

    update_task_status(repo_root, task.id, TaskStatus.running)
    context_path = repo_root / ".praetor" / "context.md"
    context = context_path.read_text() if context_path.exists() else ""
    prompt = render_task_prompt(task, context)
    _emit(on_event, "task_dispatched", task_id=task.id)
    future = pool.submit(adapter, prompt, worktree.path)
    in_flight[future] = RunningTask(task=task, worktree=worktree)
    return True


def _complete_parallel_task(
    repo_root: Path,
    future: Future[TaskResult],
    running_task: RunningTask,
    run_merge_strategy_override: str | None,
    on_event: EventCallback | None,
) -> None:
    task = running_task.task
    try:
        result = future.result()
    except Exception as exc:  # noqa: BLE001
        _write_task_log(repo_root, task.id, f"Adapter failed for {task.id}: {exc}\n")
        _mark_failed_and_propagate(repo_root, task.id)
        _emit(on_event, "task_failed", task_id=task.id, detail="adapter exception")
        return

    _write_task_log(repo_root, task.id, f"{result.stdout}{result.stderr}")

    if result.exit_code != 0:
        _mark_failed_and_propagate(repo_root, task.id)
        _emit(on_event, "task_failed", task_id=task.id, detail="agent failed")
        return

    if task.verify is not None:
        verify_result = subprocess.run(
            task.verify,
            shell=True,
            cwd=running_task.worktree.path,
            capture_output=True,
            text=True,
            check=False,
        )
        _append_task_log(repo_root, task.id, f"{verify_result.stdout}{verify_result.stderr}")

        if verify_result.returncode != 0:
            _mark_failed_and_propagate(repo_root, task.id)
            _emit(on_event, "task_failed", task_id=task.id, detail="verify failed")
            return

    if not _commit_worktree_changes(repo_root, task.id, running_task.worktree.path):
        _mark_failed_and_propagate(repo_root, task.id)
        _emit(on_event, "task_failed", task_id=task.id, detail="commit failed")
        return

    strategy = run_merge_strategy_override or task.merge_strategy
    if strategy == "manual":
        update_task_status(repo_root, task.id, TaskStatus.pending_merge)
        _emit(on_event, "task_completed", task_id=task.id)
        _emit(on_event, "task_pending_merge", task_id=task.id)
        return

    _emit(on_event, "merge_started", task_id=task.id)
    merge_result = merge_task(
        task.id,
        repo_root,
        base_branch=running_task.worktree.base_branch,
    )
    _handle_merge_result(repo_root, merge_result, on_event)


def _raise_on_stale_running(tasks: list[Task]) -> None:
    stale_task_ids = [task.id for task in tasks if task.status is TaskStatus.running]
    if not stale_task_ids:
        return

    joined_task_ids = ", ".join(stale_task_ids)
    msg = (
        f"Stale running task(s) detected: {joined_task_ids}. "
        "Inspect those tasks' worktrees and logs before retrying."
    )
    raise StaleRunningError(msg)


def _write_task_log(repo_root: Path, task_id: str, content: str) -> None:
    log_path = repo_root / ".praetor" / "logs" / f"{task_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(content)


def _append_task_log(repo_root: Path, task_id: str, content: str) -> None:
    log_path = repo_root / ".praetor" / "logs" / f"{task_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log_file:
        log_file.write(content)


def _commit_worktree_changes(repo_root: Path, task_id: str, worktree_path: Path) -> bool:
    add_result = _run_git(["add", "-A"], worktree_path)
    _append_task_log(repo_root, task_id, f"{add_result.stdout}{add_result.stderr}")
    if add_result.returncode != 0:
        return False

    commit_result = _run_git(
        [
            "-c",
            "user.name=Praetor",
            "-c",
            "user.email=praetor@local",
            "commit",
            "--allow-empty",
            "-m",
            f"praetor: task {task_id}",
        ],
        worktree_path,
    )
    _append_task_log(repo_root, task_id, f"{commit_result.stdout}{commit_result.stderr}")
    return commit_result.returncode == 0


def _handle_merge_result(
    repo_root: Path,
    merge_result: MergeResult,
    on_event: EventCallback | None,
) -> None:
    if merge_result.success:
        update_task_status(repo_root, merge_result.task_id, TaskStatus.done)
        _emit(on_event, "merge_succeeded", task_id=merge_result.task_id)
        _emit(on_event, "task_completed", task_id=merge_result.task_id)
        return

    log_content = f"{merge_result.message}\n"
    if merge_result.conflict_files:
        log_content += "Conflict files:\n"
        log_content += "".join(f"- {path}\n" for path in merge_result.conflict_files)
    _append_task_log(repo_root, merge_result.task_id, log_content)
    update_task_status(repo_root, merge_result.task_id, TaskStatus.merge_failed)
    _emit(on_event, "merge_failed", task_id=merge_result.task_id, detail=merge_result.message)


def _emit(
    callback: EventCallback | None,
    event_type: EventType,
    *,
    task_id: str | None = None,
    detail: str | None = None,
) -> None:
    if callback is None:
        return
    callback(RunnerEvent(type=event_type, task_id=task_id, detail=detail))


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=1,
            stdout="",
            stderr=f"Git command failed to start: {exc}",
        )


def _mark_failed_and_propagate(repo_root: Path, task_id: str) -> None:
    update_task_status(repo_root, task_id, TaskStatus.failed)
    for blocked_task_id in propagate_blocked(list_tasks(repo_root)):
        update_task_status(repo_root, blocked_task_id, TaskStatus.blocked)
