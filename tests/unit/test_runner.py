from datetime import UTC, datetime
from pathlib import Path

import pytest

from praetor.adapters import MockAdapter
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskResult, TaskStatus
from praetor.runner import StaleRunningError, drain_queue, run_once
from praetor.state import get_task, init_workspace


class RaisingMockAdapter(MockAdapter):
    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        raise RuntimeError("adapter failed")


def test_runner_marks_failed_on_adapter_exception(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    task = Task(
        id="task-a",
        status=TaskStatus.pending,
        created=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        body="# task-a\n",
    )
    dump_task(task, tmp_path / ".praetor" / "tasks" / "task-a.md")

    with pytest.raises(RuntimeError, match="adapter failed"):
        run_once(tmp_path, RaisingMockAdapter())

    assert get_task(tmp_path, "task-a").status is TaskStatus.failed


def test_drain_queue_raises_on_stale_running(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    task = Task(
        id="task-a",
        status=TaskStatus.running,
        created=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        body="# task-a\n",
    )
    dump_task(task, tmp_path / ".praetor" / "tasks" / "task-a.md")

    with pytest.raises(StaleRunningError, match="task-a"):
        drain_queue(tmp_path, MockAdapter())


def test_drain_queue_allows_pending_merge_resting_state(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    task = Task(
        id="task-a",
        status=TaskStatus.pending_merge,
        created=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        body="# task-a\n",
    )
    dump_task(task, tmp_path / ".praetor" / "tasks" / "task-a.md")

    drain_queue(tmp_path, MockAdapter())

    assert get_task(tmp_path, "task-a").status is TaskStatus.pending_merge


def test_drain_queue_task_filter_skips_unselected_ready_tasks(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    repair = Task(
        id="repair-a",
        status=TaskStatus.pending,
        created=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        body="# repair-a\n",
    )
    unrelated = Task(
        id="unrelated-a",
        status=TaskStatus.pending,
        created=datetime(2026, 6, 7, 12, 1, tzinfo=UTC),
        body="# unrelated-a\n",
    )
    dump_task(repair, tmp_path / ".praetor" / "tasks" / "repair-a.md")
    dump_task(unrelated, tmp_path / ".praetor" / "tasks" / "unrelated-a.md")

    drain_queue(tmp_path, MockAdapter(), task_ids={"repair-a"})

    assert get_task(tmp_path, "repair-a").status is TaskStatus.done
    assert get_task(tmp_path, "unrelated-a").status is TaskStatus.pending


def test_drain_queue_rejects_boolean_max_review_retries(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    with pytest.raises(ValueError, match="max_review_retries"):
        drain_queue(tmp_path, MockAdapter(), max_review_retries=True)
