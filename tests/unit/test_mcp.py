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
    with pytest.raises(KeyError, match="Task not found: missing"):
        mcp_get_task(str(tmp_path), "missing")


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

    assert get_logs(str(tmp_path), "task-a") == {"task_id": "task-a", "log": ""}


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
