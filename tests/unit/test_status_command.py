import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from praetor.cli import app
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskStatus
from praetor.serialize import task_to_dict
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
    "review_failure",
    "waiting_on",
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


def test_status_json_matches_shared_serializer(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    task = _make_task("task-a", TaskStatus.pending)
    _write_task(tmp_path, task)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == [
        task_to_dict(task, {"task-a"}, repo_root=tmp_path, tasks=[task])
    ]


def test_status_json_includes_review_failure_and_waiting_on(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.review_failed))
    _write_task(
        tmp_path,
        _make_task("task-b", TaskStatus.pending, offset=1, depends_on=["task-a"]),
    )
    _write_run(tmp_path, "older-run", "task-a")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0
    tasks_by_id = {task["id"]: task for task in json.loads(result.output)}
    assert tasks_by_id["task-a"]["review_failure"]["run_id"] == "older-run"
    assert tasks_by_id["task-a"]["review_failure"]["summary"] == "Unsafe validation change."
    assert tasks_by_id["task-b"]["review_failure"] is None
    assert tasks_by_id["task-b"]["waiting_on"] == [
        {
            "task_id": "task-a",
            "status": "review_failed",
            "reason": "dependency_review_failed",
            "review_summary": "Unsafe validation change.",
        }
    ]


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


def test_status_table_includes_compact_note_column(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.review_failed))
    _write_task(
        tmp_path,
        _make_task("task-b", TaskStatus.pending, offset=1, depends_on=["task-a"]),
    )
    _write_run(tmp_path, "older-run", "task-a")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Note" in result.output
    assert "review: Unsafe validation change." in result.output
    assert "waiting on review_failed: task-a" in result.output


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


def _write_run(repo_root: Path, run_id: str, task_id: str) -> None:
    path = repo_root / ".praetor" / "runs" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": run_id,
                "status": "completed",
                "started_at": "2026-06-12T12:00:00Z",
                "finished_at": "2026-06-12T12:01:00Z",
                "max_parallel": 1,
                "base_branch": "main",
                "merge_strategy": None,
                "max_review_retries": 1,
                "task_runs": [
                    {
                        "task_id": task_id,
                        "status": "review_failed",
                        "started_at": "2026-06-12T12:00:10Z",
                        "finished_at": "2026-06-12T12:00:20Z",
                        "adapter": "mock",
                        "verify_command": "pytest",
                        "agent_exit_code": 0,
                        "verify_exit_code": 0,
                        "merge_status": None,
                        "detail": "review needs revision",
                        "review": {
                            "verdict": "needs_revision",
                            "severity": "error",
                            "summary": "Unsafe validation change.",
                            "findings": [
                                {
                                    "severity": "error",
                                    "file": "src/app.py",
                                    "line": 12,
                                    "message": "Input validation can be bypassed.",
                                    "recommendation": "Validate before writing.",
                                }
                            ],
                            "reviewer_adapter": "mock-reviewer",
                            "started_at": "2026-06-12T12:00:15Z",
                            "finished_at": "2026-06-12T12:00:18Z",
                            "duration_ms": 3000,
                        },
                    }
                ],
            }
        )
    )
    os.utime(path, (100, 100))
