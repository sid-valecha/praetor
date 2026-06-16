"""Task runner orchestration.

Only the main runner thread writes task markdown. Worker threads may execute
adapters, but they return TaskResult objects for the main thread to apply.
"""

from collections.abc import Iterable
from concurrent.futures import FIRST_COMPLETED, Future, wait
from dataclasses import dataclass
import subprocess
from pathlib import Path
from time import perf_counter

from praetor.config import resolve_max_review_retries
from praetor.dag import compute_ready_set, propagate_blocked
from praetor.events import EventCallback, EventType, RunnerEvent
from praetor.merge_queue import merge_one_task
from praetor.models import (
    AgentAdapter,
    ReviewResult,
    Task,
    TaskResult,
    TaskStatus,
    validate_task_id,
)
from praetor.pool import WorkerPool
from praetor.recovery import format_review_failure_for_prompt, latest_review_failure
from praetor.review import format_review_for_log, run_task_review
from praetor.run_history import RunRecorder
from praetor.state import list_tasks, update_task, update_task_status
from praetor.worktree import Worktree, WorktreeError, create_worktree, get_worktree


class StaleRunningError(RuntimeError):
    """Raised when a previous run left tasks in running state."""


@dataclass(frozen=True)
class RunningTask:
    task: Task
    worktree: Worktree


@dataclass
class DrainGuardrails:
    max_iterations: int | None = None
    max_runtime_s: float | None = None
    started_at: float = 0.0
    iterations: int = 0

    def __post_init__(self) -> None:
        self.started_at = perf_counter()

    def can_start_task(self) -> bool:
        if self.remaining_iterations() <= 0:
            return False
        if (
            self.max_runtime_s is not None
            and perf_counter() - self.started_at >= self.max_runtime_s
        ):
            return False
        return True

    def remaining_iterations(self) -> int:
        if self.max_iterations is None:
            return 1_000_000_000
        return max(self.max_iterations - self.iterations, 0)

    def consume_iteration(self) -> None:
        self.iterations += 1

    def reached_limit(self) -> bool:
        return not self.can_start_task()


def render_task_prompt(
    task: Task,
    context: str = "",
    review_failure: dict | None = None,
) -> str:
    parts = [
        (
            "Praetor runs are non-interactive. You are authorized to make the "
            "scoped edits needed for this task. Do not ask for permission before "
            "normal in-scope file changes; ask only if the task requires "
            "destructive or out-of-scope work."
        )
    ]
    if context:
        parts.append(context.strip())
    if review_failure is not None:
        parts.append(format_review_failure_for_prompt(review_failure))
    if task.body:
        parts.append(task.body.strip())
    if task.verify is not None:
        parts.append(f"Verify command: {task.verify}")
    return "\n\n".join(parts)


def run_once(
    repo_root: Path,
    adapter: AgentAdapter,
    on_event: EventCallback | None = None,
    recorder: RunRecorder | None = None,
    max_review_retries: int = 0,
    reviewer_adapter: AgentAdapter | None = None,
    task_ids: Iterable[str] | None = None,
) -> bool:
    selected_task_ids = _normalize_task_filter(task_ids)
    ready_tasks = _filter_tasks_by_ids(
        compute_ready_set(list_tasks(repo_root)),
        selected_task_ids,
    )
    if not ready_tasks:
        return False

    task = ready_tasks[0]
    update_task_status(repo_root, task.id, TaskStatus.running)
    if recorder is not None:
        recorder.start_task(
            task.id,
            adapter=adapter.name,
            verify_command=task.verify,
            executor_model=getattr(adapter, "model", None),
            executor_effort=getattr(adapter, "effort", None),
        )

    try:
        context_path = repo_root / ".praetor" / "context.md"
        context = context_path.read_text() if context_path.exists() else ""
        prompt = render_task_prompt(
            task,
            context,
            review_failure=latest_review_failure(repo_root, task.id),
        )
        _emit(on_event, "task_dispatched", task_id=task.id)
        result = adapter.exec(prompt, cwd=repo_root)
    except Exception:
        _mark_failed_and_propagate(repo_root, task.id)
        if recorder is not None:
            recorder.finish_task(task.id, status="failed", detail="adapter exception")
        _emit(on_event, "task_failed", task_id=task.id, detail="adapter exception")
        raise

    log_path = _task_log_path(repo_root, task.id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"{result.stdout}{result.stderr}")

    if result.exit_code != 0:
        _mark_failed_and_propagate(repo_root, task.id)
        if recorder is not None:
            recorder.finish_task(
                task.id,
                status="failed",
                detail="agent failed",
                agent_exit_code=result.exit_code,
            )
        _emit(on_event, "task_failed", task_id=task.id, detail="agent failed")
        return True

    verify_output = ""
    verify_exit_code: int | None = None
    if task.verify is None:
        review_result = _review_if_needed(
            repo_root,
            task,
            adapter,
            cwd=repo_root,
            agent_result=result,
            verify_output=verify_output,
            verify_exit_code=verify_exit_code,
            recorder=recorder,
            on_event=on_event,
            max_review_retries=max_review_retries,
            reviewer_adapter=reviewer_adapter,
        )
        if review_result is not None and review_result.verdict != "pass":
            return True
        update_task_status(repo_root, task.id, TaskStatus.done)
        if recorder is not None:
            recorder.finish_task(
                task.id,
                status="done",
                agent_exit_code=result.exit_code,
                review=review_result,
            )
        _emit(on_event, "task_completed", task_id=task.id)
        return True

    verify_result = subprocess.run(
        task.verify,
        shell=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    verify_output = f"{verify_result.stdout}{verify_result.stderr}"
    verify_exit_code = verify_result.returncode
    with log_path.open("a") as log_file:
        log_file.write(verify_output)

    if verify_result.returncode != 0:
        _mark_failed_and_propagate(repo_root, task.id)
        if recorder is not None:
            recorder.finish_task(
                task.id,
                status="failed",
                detail="verify failed",
                agent_exit_code=result.exit_code,
                verify_exit_code=verify_exit_code,
            )
        _emit(on_event, "task_failed", task_id=task.id, detail="verify failed")
        return True

    review_result = _review_if_needed(
        repo_root,
        task,
        adapter,
        cwd=repo_root,
        agent_result=result,
        verify_output=verify_output,
        verify_exit_code=verify_exit_code,
        recorder=recorder,
        on_event=on_event,
        max_review_retries=max_review_retries,
        reviewer_adapter=reviewer_adapter,
    )
    if review_result is not None and review_result.verdict != "pass":
        return True

    update_task_status(repo_root, task.id, TaskStatus.done)
    if recorder is not None:
        recorder.finish_task(
            task.id,
            status="done",
            agent_exit_code=result.exit_code,
            verify_exit_code=verify_exit_code,
            review=review_result,
        )
    _emit(on_event, "task_completed", task_id=task.id)
    return True


def drain_queue(
    repo_root: Path,
    adapter: AgentAdapter,
    max_parallel: int = 1,
    base_branch: str = "main",
    merge_strategy: str | None = None,
    on_event: EventCallback | None = None,
    max_iterations: int | None = None,
    max_runtime_s: float | None = None,
    max_review_retries: int | None = None,
    reviewer_adapter: AgentAdapter | None = None,
    task_ids: Iterable[str] | None = None,
) -> None:
    _raise_on_stale_running(list_tasks(repo_root))
    resolved_max_review_retries = resolve_max_review_retries(repo_root, max_review_retries)
    selected_task_ids = _normalize_task_filter(task_ids)

    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or max_parallel < 1:
        msg = "max_parallel must be >= 1"
        raise ValueError(msg)
    if merge_strategy not in {None, "auto", "manual"}:
        msg = "merge_strategy must be one of: auto, manual"
        raise ValueError(msg)
    if max_iterations is not None and max_iterations < 1:
        msg = "max_iterations must be >= 1"
        raise ValueError(msg)
    if max_runtime_s is not None and max_runtime_s <= 0:
        msg = "max_runtime_s must be > 0"
        raise ValueError(msg)

    guardrails = DrainGuardrails(max_iterations=max_iterations, max_runtime_s=max_runtime_s)
    recorder = RunRecorder(
        repo_root,
        max_parallel=max_parallel,
        base_branch=base_branch,
        merge_strategy=merge_strategy,
        max_review_retries=resolved_max_review_retries,
    )
    _emit(on_event, "drain_started", detail=recorder.run_id)
    run_status = "completed"
    try:
        if max_parallel == 1:
            while guardrails.can_start_task():
                if not run_once(
                    repo_root,
                    adapter,
                    on_event=on_event,
                    recorder=recorder,
                    max_review_retries=resolved_max_review_retries,
                    reviewer_adapter=reviewer_adapter,
                    task_ids=selected_task_ids,
                ):
                    break
                guardrails.consume_iteration()
            return

        _drain_parallel(
            repo_root,
            adapter,
            max_parallel,
            base_branch,
            merge_strategy,
            on_event,
            recorder,
            guardrails,
            resolved_max_review_retries,
            reviewer_adapter,
            selected_task_ids,
        )
    except Exception:
        run_status = "failed"
        raise
    finally:
        if run_status == "completed" and _guardrail_stopped_ready_work(
            repo_root,
            guardrails,
            selected_task_ids,
        ):
            run_status = "stopped"
        recorder.finish_run(run_status)
        _emit(on_event, "drain_finished")


def _normalize_task_filter(task_ids: Iterable[str] | None) -> set[str] | None:
    if task_ids is None:
        return None
    return set(task_ids)


def _filter_tasks_by_ids(tasks: list[Task], task_ids: set[str] | None) -> list[Task]:
    if task_ids is None:
        return tasks
    return [task for task in tasks if task.id in task_ids]


def _guardrail_stopped_ready_work(
    repo_root: Path,
    guardrails: DrainGuardrails,
    task_ids: set[str] | None,
) -> bool:
    ready_tasks = _filter_tasks_by_ids(compute_ready_set(list_tasks(repo_root)), task_ids)
    return guardrails.reached_limit() and bool(ready_tasks)


def _drain_parallel(
    repo_root: Path,
    adapter: AgentAdapter,
    max_parallel: int,
    base_branch: str,
    merge_strategy: str | None,
    on_event: EventCallback | None,
    recorder: RunRecorder,
    guardrails: DrainGuardrails,
    max_review_retries: int,
    reviewer_adapter: AgentAdapter | None,
    task_ids: set[str] | None,
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
                recorder,
                guardrails,
                task_ids,
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
                    adapter,
                    future,
                    running_task,
                    merge_strategy,
                    on_event,
                    recorder,
                    max_review_retries,
                    reviewer_adapter,
                )


def _submit_ready_tasks(
    repo_root: Path,
    adapter: AgentAdapter,
    pool: WorkerPool,
    in_flight: dict[Future[TaskResult], RunningTask],
    base_branch: str,
    on_event: EventCallback | None,
    recorder: RunRecorder,
    guardrails: DrainGuardrails,
    task_ids: set[str] | None,
) -> bool:
    if not guardrails.can_start_task():
        return False

    capacity = pool.max_parallel - len(in_flight)
    capacity = min(capacity, guardrails.remaining_iterations())
    if capacity <= 0:
        return False

    ready_tasks = _filter_tasks_by_ids(compute_ready_set(list_tasks(repo_root)), task_ids)
    if not ready_tasks:
        return False

    solo_tasks = [task for task in ready_tasks if not task.parallel_ok]
    if solo_tasks:
        if in_flight:
            return False
        return _submit_parallel_task(
            repo_root,
            adapter,
            pool,
            in_flight,
            solo_tasks[0],
            base_branch,
            on_event,
            recorder,
            guardrails,
        )

    submitted = False
    for task in ready_tasks[:capacity]:
        submitted = (
            _submit_parallel_task(
                repo_root,
                adapter,
                pool,
                in_flight,
                task,
                base_branch,
                on_event,
                recorder,
                guardrails,
            )
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
    recorder: RunRecorder,
    guardrails: DrainGuardrails,
) -> bool:
    try:
        worktree = _worktree_for_parallel_task(task, repo_root, base_branch)
    except WorktreeError as exc:
        _write_task_log(repo_root, task.id, f"Worktree collision for {task.id}: {exc}\n")
        _mark_failed_and_propagate(repo_root, task.id)
        recorder.start_task(
            task.id,
            adapter=adapter.name,
            verify_command=task.verify,
            executor_model=getattr(adapter, "model", None),
            executor_effort=getattr(adapter, "effort", None),
        )
        recorder.finish_task(task.id, status="failed", detail="worktree setup failed")
        _emit(on_event, "task_failed", task_id=task.id, detail="worktree setup failed")
        return True

    update_task_status(repo_root, task.id, TaskStatus.running)
    recorder.start_task(
        task.id,
        adapter=adapter.name,
        verify_command=task.verify,
        executor_model=getattr(adapter, "model", None),
        executor_effort=getattr(adapter, "effort", None),
    )
    guardrails.consume_iteration()
    context_path = repo_root / ".praetor" / "context.md"
    context = context_path.read_text() if context_path.exists() else ""
    prompt = render_task_prompt(
        task,
        context,
        review_failure=latest_review_failure(repo_root, task.id),
    )
    _emit(on_event, "task_dispatched", task_id=task.id)
    future = pool.submit(adapter, prompt, worktree.path)
    in_flight[future] = RunningTask(task=task, worktree=worktree)
    return True


def _complete_parallel_task(
    repo_root: Path,
    adapter: AgentAdapter,
    future: Future[TaskResult],
    running_task: RunningTask,
    run_merge_strategy_override: str | None,
    on_event: EventCallback | None,
    recorder: RunRecorder,
    max_review_retries: int,
    reviewer_adapter: AgentAdapter | None,
) -> None:
    task = running_task.task
    try:
        result = future.result()
    except Exception as exc:  # noqa: BLE001
        _write_task_log(repo_root, task.id, f"Adapter failed for {task.id}: {exc}\n")
        _mark_failed_and_propagate(repo_root, task.id)
        recorder.finish_task(task.id, status="failed", detail="adapter exception")
        _emit(on_event, "task_failed", task_id=task.id, detail="adapter exception")
        return

    _write_task_log(repo_root, task.id, f"{result.stdout}{result.stderr}")

    if result.exit_code != 0:
        _mark_failed_and_propagate(repo_root, task.id)
        recorder.finish_task(
            task.id,
            status="failed",
            detail="agent failed",
            agent_exit_code=result.exit_code,
        )
        _emit(on_event, "task_failed", task_id=task.id, detail="agent failed")
        return

    verify_output = ""
    verify_exit_code: int | None = None
    if task.verify is not None:
        verify_result = subprocess.run(
            task.verify,
            shell=True,
            cwd=running_task.worktree.path,
            capture_output=True,
            text=True,
            check=False,
        )
        verify_output = f"{verify_result.stdout}{verify_result.stderr}"
        verify_exit_code = verify_result.returncode
        _append_task_log(repo_root, task.id, verify_output)

        if verify_result.returncode != 0:
            _mark_failed_and_propagate(repo_root, task.id)
            recorder.finish_task(
                task.id,
                status="failed",
                detail="verify failed",
                agent_exit_code=result.exit_code,
                verify_exit_code=verify_exit_code,
            )
            _emit(on_event, "task_failed", task_id=task.id, detail="verify failed")
            return

    review_result = _review_if_needed(
        repo_root,
        task,
        adapter,
        cwd=running_task.worktree.path,
        agent_result=result,
        verify_output=verify_output,
        verify_exit_code=verify_exit_code,
        recorder=recorder,
        on_event=on_event,
        max_review_retries=max_review_retries,
        reviewer_adapter=reviewer_adapter,
    )
    if review_result is not None and review_result.verdict != "pass":
        return

    if not _commit_worktree_changes(repo_root, task.id, running_task.worktree.path):
        _mark_failed_and_propagate(repo_root, task.id)
        recorder.finish_task(
            task.id,
            status="failed",
            detail="commit failed",
            agent_exit_code=result.exit_code,
            verify_exit_code=verify_exit_code,
            review=review_result,
        )
        _emit(on_event, "task_failed", task_id=task.id, detail="commit failed")
        return

    strategy = run_merge_strategy_override or task.merge_strategy
    if strategy == "manual":
        update_task_status(repo_root, task.id, TaskStatus.pending_merge)
        recorder.finish_task(
            task.id,
            status="pending_merge",
            agent_exit_code=result.exit_code,
            verify_exit_code=verify_exit_code,
            review=review_result,
            merge_status="manual",
        )
        _emit(on_event, "task_completed", task_id=task.id)
        _emit(on_event, "task_pending_merge", task_id=task.id)
        return

    merge_result = merge_one_task(
        repo_root,
        task.id,
        base_branch=running_task.worktree.base_branch,
        on_event=on_event,
    )
    recorder.finish_task(
        task.id,
        status="done" if merge_result.success else "merge_failed",
        agent_exit_code=result.exit_code,
        verify_exit_code=verify_exit_code,
        review=review_result,
        merge_status=merge_result.message,
    )


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
    log_path = _task_log_path(repo_root, task_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(content)


def _append_task_log(repo_root: Path, task_id: str, content: str) -> None:
    log_path = _task_log_path(repo_root, task_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log_file:
        log_file.write(content)


def _task_log_path(repo_root: Path, task_id: str) -> Path:
    logs_dir = (repo_root / ".praetor" / "logs").resolve()
    log_path = (logs_dir / f"{validate_task_id(task_id)}.log").resolve()
    if log_path.parent != logs_dir:
        msg = f"Invalid task log path for {task_id}"
        raise ValueError(msg)
    return log_path


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


def _review_if_needed(
    repo_root: Path,
    task: Task,
    adapter: AgentAdapter,
    *,
    cwd: Path,
    agent_result: TaskResult,
    verify_output: str,
    verify_exit_code: int | None,
    recorder: RunRecorder | None,
    on_event: EventCallback | None,
    max_review_retries: int,
    reviewer_adapter: AgentAdapter | None,
) -> ReviewResult | None:
    if task.review == "off":
        return None

    _emit(on_event, "task_review_started", task_id=task.id)
    review_adapter = _review_adapter(adapter, reviewer_adapter)
    review = run_task_review(
        task,
        review_adapter,
        cwd=cwd,
        agent_result=agent_result,
        verify_output=verify_output,
    )
    _append_task_log(repo_root, task.id, format_review_for_log(review))
    if recorder is not None:
        recorder.record_review(task.id, review)

    if review.verdict == "pass":
        _emit(on_event, "task_review_succeeded", task_id=task.id)
        return review

    if review.verdict == "blocked":
        _mark_blocked_and_propagate(repo_root, task.id)
        if recorder is not None:
            recorder.finish_task(
                task.id,
                status="blocked",
                detail="review blocked",
                agent_exit_code=agent_result.exit_code,
                verify_exit_code=verify_exit_code,
                review=review,
            )
        _emit(on_event, "task_review_failed", task_id=task.id, detail="review blocked")
        return review

    if task.retry < max_review_retries:
        update_task(
            repo_root,
            task.id,
            status=TaskStatus.pending,
            retry=task.retry + 1,
        )
        if recorder is not None:
            recorder.finish_task(
                task.id,
                status="pending",
                detail="review retry scheduled",
                agent_exit_code=agent_result.exit_code,
                verify_exit_code=verify_exit_code,
                review=review,
            )
        _emit(
            on_event,
            "task_review_failed",
            task_id=task.id,
            detail="review retry scheduled",
        )
        return review

    update_task_status(repo_root, task.id, TaskStatus.review_failed)
    if recorder is not None:
        recorder.finish_task(
            task.id,
            status="review_failed",
            detail="review needs revision",
            agent_exit_code=agent_result.exit_code,
            verify_exit_code=verify_exit_code,
            review=review,
        )
    _emit(on_event, "task_review_failed", task_id=task.id, detail="review needs revision")
    return review


def _worktree_for_parallel_task(
    task: Task,
    repo_root: Path,
    base_branch: str,
) -> Worktree:
    if latest_review_failure(repo_root, task.id) is not None:
        worktree = get_worktree(task.id, repo_root)
        if worktree is not None and worktree.base_branch == base_branch:
            return worktree
    return create_worktree(task.id, repo_root, base_branch=base_branch)


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


def _review_adapter(
    adapter: AgentAdapter,
    explicit_reviewer_adapter: AgentAdapter | None = None,
) -> AgentAdapter:
    selected_adapter = explicit_reviewer_adapter or adapter
    factory = getattr(selected_adapter, "for_review", None)
    if not callable(factory):
        return selected_adapter
    review_adapter = factory()
    return review_adapter


def _mark_failed_and_propagate(repo_root: Path, task_id: str) -> None:
    update_task_status(repo_root, task_id, TaskStatus.failed)
    for blocked_task_id in propagate_blocked(list_tasks(repo_root)):
        update_task_status(repo_root, blocked_task_id, TaskStatus.blocked)


def _mark_blocked_and_propagate(repo_root: Path, task_id: str) -> None:
    update_task_status(repo_root, task_id, TaskStatus.blocked)
    for blocked_task_id in propagate_blocked(list_tasks(repo_root)):
        update_task_status(repo_root, blocked_task_id, TaskStatus.blocked)
