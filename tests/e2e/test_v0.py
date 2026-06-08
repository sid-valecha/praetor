from datetime import UTC, datetime, timedelta
from pathlib import Path

from praetor.adapters import MockAdapter
from praetor.dag import compute_ready_set
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskResult, TaskStatus
from praetor.runner import drain_queue
from praetor.state import get_task, init_workspace, list_tasks, update_task_status


class RecordingMockAdapter(MockAdapter):
    def __init__(self, *, exit_code: int = 0, stdout: str = "mock output\n") -> None:
        super().__init__(exit_code=exit_code, stdout=stdout)
        self.executed_task_ids: list[str] = []

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        for line in prompt.splitlines():
            if line.startswith("TASK_ID: "):
                self.executed_task_ids.append(line.removeprefix("TASK_ID: "))
                break
        return super().exec(prompt, cwd, timeout_s)


def make_task(
    task_id: str,
    *,
    offset: int = 0,
    status: TaskStatus = TaskStatus.pending,
    depends_on: list[str] | None = None,
    verify: str = "true",
) -> Task:
    return Task(
        id=task_id,
        status=status,
        depends_on=depends_on or [],
        verify=verify,
        created=datetime(2026, 6, 7, 12, 0, tzinfo=UTC) + timedelta(minutes=offset),
        body=f"# {task_id}\n\nTASK_ID: {task_id}\n",
    )


def write_task(repo_root: Path, task: Task) -> None:
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")


def task_statuses(repo_root: Path) -> dict[str, TaskStatus]:
    return {task.id: task.status for task in list_tasks(repo_root)}


def test_e2e_three_linear_tasks_drain_to_done(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("task-a", offset=0))
    write_task(tmp_path, make_task("task-b", offset=1, depends_on=["task-a"]))
    write_task(tmp_path, make_task("task-c", offset=2, depends_on=["task-b"]))
    adapter = RecordingMockAdapter(exit_code=0)

    drain_queue(tmp_path, adapter)

    assert task_statuses(tmp_path) == {
        "task-a": TaskStatus.done,
        "task-b": TaskStatus.done,
        "task-c": TaskStatus.done,
    }
    assert adapter.executed_task_ids == ["task-a", "task-b", "task-c"]


def test_e2e_verify_failure_blocks_downstream(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("task-a", offset=0, verify="false"))
    write_task(tmp_path, make_task("task-b", offset=1, depends_on=["task-a"]))

    drain_queue(tmp_path, RecordingMockAdapter(exit_code=0))

    assert get_task(tmp_path, "task-a").status is TaskStatus.failed
    assert get_task(tmp_path, "task-b").status is TaskStatus.blocked
    log_path = tmp_path / ".praetor" / "logs" / "task-a.log"
    assert log_path.is_file()
    assert log_path.read_text()


def test_e2e_resume_skips_completed_tasks(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("task-a", offset=0))
    write_task(tmp_path, make_task("task-b", offset=1, depends_on=["task-a"]))
    update_task_status(tmp_path, "task-a", TaskStatus.done)
    adapter = RecordingMockAdapter(exit_code=0)

    drain_queue(tmp_path, adapter)

    assert get_task(tmp_path, "task-a").status is TaskStatus.done
    assert get_task(tmp_path, "task-b").status is TaskStatus.done
    assert adapter.executed_task_ids == ["task-b"]


def test_e2e_cycle_detection_does_not_deadlock(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("task-a", offset=0, depends_on=["task-b"]))
    write_task(tmp_path, make_task("task-b", offset=1, depends_on=["task-a"]))
    adapter = RecordingMockAdapter(exit_code=0)

    assert compute_ready_set(list_tasks(tmp_path)) == []
    drain_queue(tmp_path, adapter)

    assert adapter.executed_task_ids == []
    assert task_statuses(tmp_path) == {
        "task-a": TaskStatus.pending,
        "task-b": TaskStatus.pending,
    }


def test_e2e_full_scenario(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("root", offset=0))
    write_task(tmp_path, make_task("left", offset=1, depends_on=["root"]))
    write_task(tmp_path, make_task("right", offset=2, depends_on=["root"]))
    write_task(tmp_path, make_task("merge", offset=3, depends_on=["left", "right"]))
    write_task(tmp_path, make_task("final", offset=4, depends_on=["merge"]))
    adapter = RecordingMockAdapter(exit_code=0)

    drain_queue(tmp_path, adapter)

    assert task_statuses(tmp_path) == {
        "root": TaskStatus.done,
        "left": TaskStatus.done,
        "right": TaskStatus.done,
        "merge": TaskStatus.done,
        "final": TaskStatus.done,
    }
    assert adapter.executed_task_ids == ["root", "left", "right", "merge", "final"]
