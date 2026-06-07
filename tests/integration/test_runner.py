from datetime import UTC, datetime, timedelta
from pathlib import Path

from praetor.adapters import MockAdapter
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskStatus
from praetor.runner import drain_queue, run_once
from praetor.state import get_task, init_workspace


def make_task(
    task_id: str,
    *,
    offset: int = 0,
    status: TaskStatus = TaskStatus.pending,
    depends_on: list[str] | None = None,
    verify: str | None = None,
) -> Task:
    return Task(
        id=task_id,
        status=status,
        depends_on=depends_on or [],
        verify=verify,
        created=datetime(2026, 6, 7, 12, 0, tzinfo=UTC) + timedelta(minutes=offset),
        body=f"# Task {task_id}\n",
    )


def write_task(repo_root: Path, task: Task) -> None:
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")


def test_run_once_returns_false_on_empty_queue(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    assert run_once(tmp_path, MockAdapter()) is False


def test_run_once_processes_one_task(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A"))

    run_once(tmp_path, MockAdapter(exit_code=0))

    assert get_task(tmp_path, "A").status is TaskStatus.done


def test_run_once_returns_true_when_task_processed(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A"))

    assert run_once(tmp_path, MockAdapter(exit_code=0)) is True


def test_drain_queue_linear_three_tasks(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", offset=0))
    write_task(tmp_path, make_task("B", offset=1, depends_on=["A"]))
    write_task(tmp_path, make_task("C", offset=2, depends_on=["B"]))

    drain_queue(tmp_path, MockAdapter(exit_code=0))

    assert get_task(tmp_path, "A").status is TaskStatus.done
    assert get_task(tmp_path, "B").status is TaskStatus.done
    assert get_task(tmp_path, "C").status is TaskStatus.done


def test_task_failure_marks_failed_and_propagates_blocked(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", offset=0))
    write_task(tmp_path, make_task("B", offset=1, depends_on=["A"]))

    run_once(tmp_path, MockAdapter(exit_code=1))

    assert get_task(tmp_path, "A").status is TaskStatus.failed
    assert get_task(tmp_path, "B").status is TaskStatus.blocked


def test_verify_failure_marks_failed(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", verify="false"))

    run_once(tmp_path, MockAdapter(exit_code=0))

    assert get_task(tmp_path, "A").status is TaskStatus.failed


def test_verify_success_marks_done(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", verify="true"))

    run_once(tmp_path, MockAdapter(exit_code=0))

    assert get_task(tmp_path, "A").status is TaskStatus.done


def test_log_file_written(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A"))

    run_once(tmp_path, MockAdapter(exit_code=0, stdout="task output\n"))

    log_path = tmp_path / ".praetor" / "logs" / "A.log"
    assert log_path.is_file()
    assert log_path.read_text()


def test_resume_skips_done_tasks(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", status=TaskStatus.done))

    drain_queue(tmp_path, MockAdapter(exit_code=1))

    assert get_task(tmp_path, "A").status is TaskStatus.done
