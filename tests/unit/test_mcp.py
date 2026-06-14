import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from praetor.frontmatter import dump_task, parse_task
from praetor.mcp import add_task
from praetor.mcp import get_logs
from praetor.mcp import get_latest_run
from praetor.mcp import get_task as mcp_get_task
from praetor.mcp import init_workspace as mcp_init_workspace
from praetor.mcp import list_tasks as mcp_list_tasks
from praetor.mcp import merge_all_pending
from praetor.mcp import merge_task as mcp_merge_task
from praetor.mcp import next_ready
from praetor.mcp import start_drain
from praetor.models import Task, TaskStatus
from praetor.runner import StaleRunningError
from praetor.state import get_task, init_workspace
from praetor.worktree import create_worktree

REQUIRED_TASK_KEYS = {
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


def test_init_workspace_returns_notes(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    result = mcp_init_workspace(str(tmp_path))

    assert isinstance(result["notes"], list)
    assert any(".gitignore was updated to exclude .praetor/" in note for note in result["notes"])


def test_list_tasks_returns_full_task_objects_with_ready(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.pending))

    [task] = mcp_list_tasks(str(tmp_path))

    assert set(task) == REQUIRED_TASK_KEYS
    assert task["id"] == "task-a"
    assert task["ready"] is True


def test_get_task_returns_single_task_or_keyerror(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.pending))

    task = mcp_get_task(str(tmp_path), "task-a")

    assert task["id"] == "task-a"
    assert "ready" not in task
    assert task["review_failure"] is None
    assert task["waiting_on"] == []
    with pytest.raises(KeyError, match="Task not found: missing"):
        mcp_get_task(str(tmp_path), "missing")


def test_mcp_tasks_expose_review_failure_and_waiting_on(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.review_failed))
    _write_task(
        tmp_path,
        _make_task("task-b", TaskStatus.pending, offset=1, depends_on=["task-a"]),
    )
    _write_run(tmp_path, "older-run", "task-a")

    listed = {task["id"]: task for task in mcp_list_tasks(str(tmp_path))}
    fetched = mcp_get_task(str(tmp_path), "task-a")

    assert listed["task-a"]["review_failure"]["summary"] == "Unsafe validation change."
    assert fetched["review_failure"]["run_id"] == "older-run"
    assert listed["task-b"]["waiting_on"] == [
        {
            "task_id": "task-a",
            "status": "review_failed",
            "reason": "dependency_review_failed",
            "review_summary": "Unsafe validation change.",
        }
    ]


def test_next_ready_returns_ready_ids(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.pending))
    _write_task(
        tmp_path,
        _make_task("task-b", TaskStatus.pending, offset=1, depends_on=["task-a"]),
    )

    assert next_ready(str(tmp_path)) == ["task-a"]


def test_add_task_creates_task_file(tmp_path: Path) -> None:
    result = add_task(
        str(tmp_path),
        "Add MCP task",
        depends_on=["foundation"],
        verify="pytest tests/unit/test_mcp.py",
        review="strict",
    )

    task_path = tmp_path / ".praetor" / "tasks" / f"{result['id']}.md"
    assert task_path.is_file()
    task = parse_task(task_path)
    assert task.id == result["id"]
    assert task.depends_on == ["foundation"]
    assert task.verify == "pytest tests/unit/test_mcp.py"
    assert task.review == "strict"
    assert task.parallel_ok is True


def test_merge_task_returns_structured_result(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.pending_merge))
    _git(tmp_path, "add", ".gitignore")
    _git(tmp_path, "commit", "-m", "ignore praetor")
    worktree = create_worktree("task-a", tmp_path)
    (worktree.path / "task-a.txt").write_text("task a\n")
    _git(worktree.path, "add", "task-a.txt")
    _git(worktree.path, "commit", "-m", "task a")

    result = mcp_merge_task(str(tmp_path), "task-a")

    assert result["success"] is True
    assert result["message"] == "merged"
    assert isinstance(result["merge_commit_sha"], str)
    assert result["conflict_files"] is None
    assert get_task(tmp_path, "task-a").status is TaskStatus.done


def test_merge_all_pending_returns_structured_results(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.pending_merge))
    _git(tmp_path, "add", ".gitignore")
    _git(tmp_path, "commit", "-m", "ignore praetor")
    worktree = create_worktree("task-a", tmp_path)
    (worktree.path / "task-a.txt").write_text("task a\n")
    _git(worktree.path, "add", "task-a.txt")
    _git(worktree.path, "commit", "-m", "task a")

    results = merge_all_pending(str(tmp_path))

    assert results == [{"task_id": "task-a", "success": True, "message": "merged"}]
    assert get_task(tmp_path, "task-a").status is TaskStatus.done


def test_get_logs_returns_empty_for_missing_log(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.pending))

    assert get_logs(str(tmp_path), "task-a") == {
        "task_id": "task-a",
        "log": "",
        "review_failure": None,
    }


def test_get_logs_rejects_unknown_task_even_if_orphan_log_exists(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    (tmp_path / ".praetor" / "logs" / "missing.log").write_text("orphan log\n")

    with pytest.raises(KeyError, match="Task not found: missing"):
        get_logs(str(tmp_path), "missing")


def test_get_logs_rejects_path_escape_task_id(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    (tmp_path / ".praetor" / "evil.log").write_text("secret\n")

    with pytest.raises(ValueError, match="Invalid task id"):
        get_logs(str(tmp_path), "../evil")


def test_get_logs_exposes_review_failure_for_review_failed_task(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.review_failed))
    _write_run(tmp_path, "older-run", "task-a")
    (tmp_path / ".praetor" / "logs" / "task-a.log").write_text("raw log\n")

    result = get_logs(str(tmp_path), "task-a")

    assert result["task_id"] == "task-a"
    assert result["log"] == "raw log\n"
    assert result["review_failure"]["summary"] == "Unsafe validation change."


def test_get_latest_run_returns_none_when_missing(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    assert get_latest_run(str(tmp_path)) is None


def test_start_drain_propagates_stale_running_error(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.running))

    with pytest.raises(StaleRunningError, match="task-a"):
        start_drain(str(tmp_path))


def test_start_drain_passes_model_and_effort_to_claude_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_drain_queue(repo_root: Path, adapter: object, **kwargs: object) -> None:
        captured["repo_root"] = repo_root
        captured["adapter"] = adapter
        captured.update(kwargs)

    monkeypatch.setattr("praetor.mcp.drain_queue", fake_drain_queue)

    result = start_drain(str(tmp_path), model="haiku", effort="low")

    assert result == {"status": "completed"}
    adapter = captured["adapter"]
    assert getattr(adapter, "model") == "haiku"
    assert getattr(adapter, "effort") == "low"


def test_start_drain_accepts_executor_adapter_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_drain_queue(repo_root: Path, adapter: object, **kwargs: object) -> None:
        captured["repo_root"] = repo_root
        captured["adapter"] = adapter
        captured.update(kwargs)

    monkeypatch.setattr("praetor.mcp.drain_queue", fake_drain_queue)

    result = start_drain(str(tmp_path), adapter="codex", model="spark", effort="medium")

    assert result == {"status": "completed"}
    adapter = captured["adapter"]
    assert getattr(adapter, "name") == "codex"
    assert getattr(adapter, "model") == "gpt-5.3-codex-spark"
    assert getattr(adapter, "effort") == "medium"


def test_start_drain_passes_max_review_retries_to_drain_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_drain_queue(repo_root: Path, adapter: object, **kwargs: object) -> None:
        captured["repo_root"] = repo_root
        captured["adapter"] = adapter
        captured.update(kwargs)

    monkeypatch.setattr("praetor.mcp.drain_queue", fake_drain_queue)

    result = start_drain(str(tmp_path), max_review_retries=2)

    assert result == {"status": "completed"}
    assert captured["max_review_retries"] == 2


def test_start_drain_passes_reviewer_adapter_to_drain_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_drain_queue(repo_root: Path, adapter: object, **kwargs: object) -> None:
        captured["repo_root"] = repo_root
        captured["adapter"] = adapter
        captured.update(kwargs)

    monkeypatch.setattr("praetor.mcp.drain_queue", fake_drain_queue)

    result = start_drain(
        str(tmp_path),
        model="haiku",
        effort="low",
        reviewer_model="opus",
        reviewer_effort="high",
    )

    assert result == {"status": "completed"}
    assert getattr(captured["adapter"], "model") == "haiku"
    assert getattr(captured["adapter"], "effort") == "low"
    reviewer = captured["reviewer_adapter"]
    assert getattr(reviewer, "model") == "opus"
    assert getattr(reviewer, "effort") == "high"


def test_start_drain_routes_reviewer_as_independent_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_drain_queue(repo_root: Path, adapter: object, **kwargs: object) -> None:
        captured["repo_root"] = repo_root
        captured["adapter"] = adapter
        captured.update(kwargs)

    monkeypatch.setattr("praetor.mcp.drain_queue", fake_drain_queue)

    result = start_drain(
        str(tmp_path),
        model="haiku",
        effort="low",
        reviewer_adapter="codex",
        reviewer_model="spark",
        reviewer_effort="medium",
    )

    assert result == {"status": "completed"}
    assert getattr(captured["adapter"], "name") == "claude"
    assert getattr(captured["adapter"], "model") == "haiku"
    reviewer = captured["reviewer_adapter"]
    assert getattr(reviewer, "name") == "codex"
    assert getattr(reviewer, "model") == "gpt-5.3-codex-spark"
    assert getattr(reviewer, "effort") == "medium"


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


def _init_git_repo(repo_root: Path) -> None:
    _git(repo_root, "init", "-b", "main")
    _git(repo_root, "config", "user.email", "praetor@example.com")
    _git(repo_root, "config", "user.name", "Praetor Tests")
    (repo_root / "README.md").write_text("# Scratch\n")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-m", "initial commit")


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.rstrip("\n")
