import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from praetor.cli import app
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskStatus
from praetor.state import init_workspace, update_task_status

runner = CliRunner()

REQUIRED_STATUS_JSON_KEYS = {
    "id",
    "status",
    "depends_on",
    "parallel_ok",
    "merge_strategy",
    "agent",
    "verify",
    "review",
    "retry",
    "priority",
    "env",
    "context_files",
    "created",
    "ready",
}


def test_status_json_returns_empty_list_when_no_tasks(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == []


def test_status_json_includes_all_required_keys(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.pending))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0
    [task] = json.loads(result.output)
    assert REQUIRED_STATUS_JSON_KEYS - set(task) == set()


def test_status_json_ready_derives_from_dependencies(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("a-id", TaskStatus.pending))
    _write_task(
        tmp_path,
        _make_task("b-id", TaskStatus.pending, offset=1, depends_on=["a-id"]),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0
    tasks_by_id = {task["id"]: task for task in json.loads(result.output)}
    assert tasks_by_id["a-id"]["ready"] is True
    assert tasks_by_id["b-id"]["ready"] is False

    update_task_status(tmp_path, "a-id", TaskStatus.done)
    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0
    tasks_by_id = {task["id"]: task for task in json.loads(result.output)}
    assert tasks_by_id["b-id"]["ready"] is True


def test_status_json_serializes_datetime_with_z_suffix(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.pending))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0
    [task] = json.loads(result.output)
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z",
        task["created"],
    )


def test_status_without_json_flag_still_renders_table(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.done))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.output)
    assert "task-a" in result.output
    assert "done" in result.output


def _make_task(
    task_id: str,
    status: TaskStatus,
    *,
    offset: int = 0,
    depends_on: list[str] | None = None,
) -> Task:
    return Task(
        id=task_id,
        status=status,
        depends_on=depends_on or [],
        verify="pytest",
        created=datetime(2026, 6, 10, 17, 30, tzinfo=UTC) + timedelta(minutes=offset),
        body=f"# {task_id}\n",
    )


def _write_task(repo_root: Path, task: Task) -> None:
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")
