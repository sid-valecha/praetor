from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import queue
import signal
import sys
import threading
from types import FrameType

from praetor.dag import compute_ready_set
from praetor.events import EventCallback
from praetor.models import AgentAdapter, TaskStatus
from praetor.runner import drain_queue
from praetor.state import list_tasks

try:
    from watchdog.events import FileCreatedEvent, FileSystemEventHandler
    from watchdog.observers import Observer
except ModuleNotFoundError:
    FileCreatedEvent = None
    FileSystemEventHandler = object
    Observer = None


SUCCESS_STATUSES = {TaskStatus.done, TaskStatus.pending_merge}


@dataclass(frozen=True)
class LoopOptions:
    max_parallel: int = 1
    base_branch: str = "main"
    merge_strategy: str | None = None
    poll_interval: float = 5.0
    once: bool = False
    max_iterations: int | None = None
    max_runtime_s: float | None = None
    max_review_retries: int | None = None


class TaskFileHandler(FileSystemEventHandler):
    def __init__(self, wake_event: threading.Event, task_ids: queue.SimpleQueue[str]) -> None:
        self._wake = wake_event
        self._task_ids = task_ids

    def on_created(self, event) -> None:  # noqa: ANN001
        if (
            FileCreatedEvent is not None
            and isinstance(event, FileCreatedEvent)
            and event.src_path.endswith(".md")
        ):
            self._task_ids.put(Path(event.src_path).stem)
            self._wake.set()


def loop_queue(
    repo_root: Path,
    adapter: AgentAdapter,
    options: LoopOptions,
    on_event: EventCallback | None = None,
) -> None:
    stop_event = threading.Event()
    wake_event = threading.Event()
    detected_task_ids: queue.SimpleQueue[str] = queue.SimpleQueue()
    previous_sigint_handler = _install_sigint_handler(stop_event)

    observer: Observer | None = None
    try:
        _drain_and_log(repo_root, adapter, options, on_event)
        if options.once:
            return

        observer = _start_observer(repo_root, wake_event, detected_task_ids)

        while not stop_event.is_set():
            ready_tasks = compute_ready_set(list_tasks(repo_root))
            if ready_tasks:
                _drain_and_log(repo_root, adapter, options, on_event)
                continue

            print("[waiting] queue empty; watching .praetor/tasks/...", file=sys.stderr)
            wake_event.wait(timeout=options.poll_interval)
            if stop_event.is_set():
                break

            if wake_event.is_set():
                wake_event.clear()
                task_id = _drain_latest_detected_task_id(detected_task_ids)
                if task_id is None:
                    print("[wake] new task detected: unknown", file=sys.stderr)
                else:
                    print(f"[wake] new task detected: {task_id}", file=sys.stderr)
            else:
                print("[wake] poll interval elapsed", file=sys.stderr)
    finally:
        if observer is not None:
            observer.stop()
            observer.join()
        _restore_sigint_handler(previous_sigint_handler)


def _drain_and_log(
    repo_root: Path,
    adapter: AgentAdapter,
    options: LoopOptions,
    on_event: EventCallback | None,
) -> None:
    before_successful = _successful_task_ids(repo_root)
    drain_queue(
        repo_root,
        adapter,
        max_parallel=options.max_parallel,
        base_branch=options.base_branch,
        merge_strategy=options.merge_strategy,
        on_event=on_event,
        max_iterations=options.max_iterations,
        max_runtime_s=options.max_runtime_s,
        max_review_retries=options.max_review_retries,
    )
    completed_count = len(_successful_task_ids(repo_root) - before_successful)
    if options.max_parallel == 1:
        print(f"[drained] {completed_count} tasks completed (sequential)", file=sys.stderr)
    else:
        print(
            f"[drained] {completed_count} tasks completed (max_parallel={options.max_parallel})",
            file=sys.stderr,
        )


def _successful_task_ids(repo_root: Path) -> set[str]:
    return {task.id for task in list_tasks(repo_root) if task.status in SUCCESS_STATUSES}


def _start_observer(
    repo_root: Path,
    wake_event: threading.Event,
    detected_task_ids: queue.SimpleQueue[str],
) -> object | None:
    if Observer is None:
        print(
            "Warning: watchdog is not installed; falling back to polling.",
            file=sys.stderr,
        )
        return None

    tasks_dir = repo_root / ".praetor" / "tasks"
    observer = Observer()
    handler = TaskFileHandler(wake_event, detected_task_ids)
    try:
        observer.schedule(handler, str(tasks_dir), recursive=False)
        observer.start()
    except Exception as exc:  # noqa: BLE001
        print(
            f"Warning: filesystem watch unavailable ({exc}); falling back to polling.",
            file=sys.stderr,
        )
        return None
    return observer


def _install_sigint_handler(stop_event: threading.Event):
    if threading.current_thread() is not threading.main_thread():
        return None

    previous_handler = signal.getsignal(signal.SIGINT)
    reported = False

    def handle_sigint(_signum: int, _frame: FrameType | None) -> None:
        nonlocal reported
        if not reported:
            print("Shutting down...", file=sys.stderr)
            print(
                "[stopping] received SIGINT, will exit after current pass",
                file=sys.stderr,
            )
            reported = True
        stop_event.set()

    signal.signal(signal.SIGINT, handle_sigint)
    return previous_handler


def _restore_sigint_handler(previous_handler) -> None:
    if previous_handler is not None and threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, previous_handler)


def _drain_latest_detected_task_id(detected_task_ids: queue.SimpleQueue[str]) -> str | None:
    latest_task_id: str | None = None
    while True:
        try:
            latest_task_id = detected_task_ids.get_nowait()
        except queue.Empty:
            return latest_task_id
