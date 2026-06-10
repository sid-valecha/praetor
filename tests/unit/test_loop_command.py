import os
import signal
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from praetor.adapters.mock import MockAdapter
from praetor.cli import app
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskStatus
from praetor.state import get_task, init_workspace

runner = CliRunner()


def test_loop_once_drains_and_exits(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("praetor.commands.loop.get_adapter", lambda adapter: MockAdapter())

    result = runner.invoke(app, ["loop", "--once"])

    assert result.exit_code == 0
    assert get_task(tmp_path, "task-a").status is TaskStatus.done
    assert "[drained] 1 tasks completed (sequential)" in result.output


def test_loop_handles_empty_queue_with_once(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("praetor.commands.loop.get_adapter", lambda adapter: MockAdapter())

    result = runner.invoke(app, ["loop", "--once"])

    assert result.exit_code == 0
    assert "[drained] 0 tasks completed (sequential)" in result.output


def test_loop_rejects_merge_strategy_with_default_max_parallel(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["loop", "--merge-strategy", "auto"])

    assert result.exit_code != 0
    assert "only applies in parallel mode" in result.output


def test_loop_picks_up_new_task_during_wait(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("praetor.commands.loop.get_adapter", lambda adapter: MockAdapter())

    worker = threading.Thread(target=_write_task_then_stop_loop, args=(tmp_path,), daemon=True)
    worker.start()

    result = runner.invoke(app, ["loop", "--poll-interval", "0.1"])

    worker.join(timeout=1)
    assert result.exit_code == 0
    assert get_task(tmp_path, "task-a").status is TaskStatus.done
    assert "[wake]" in result.output
    assert "[stopping] received SIGINT, will exit after current pass" in result.output


def _write_task_then_stop_loop(repo_root: Path) -> None:
    time.sleep(0.2)
    _write_task(repo_root, _make_task("task-a"))
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if get_task(repo_root, "task-a").status is TaskStatus.done:
            os.kill(os.getpid(), signal.SIGINT)
            return
        time.sleep(0.02)
    os.kill(os.getpid(), signal.SIGINT)


def _make_task(task_id: str, *, offset: int = 0) -> Task:
    return Task(
        id=task_id,
        status=TaskStatus.pending,
        verify="true",
        created=datetime(2026, 6, 10, 12, 0, tzinfo=UTC) + timedelta(minutes=offset),
        body=f"# {task_id}\n",
    )


def _write_task(repo_root: Path, task: Task) -> None:
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")
