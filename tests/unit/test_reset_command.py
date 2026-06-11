from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess

from typer.testing import CliRunner

from praetor.cli import app
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskStatus
from praetor.state import get_task, init_workspace
from praetor.worktree import create_worktree

runner = CliRunner()


def test_reset_single_task_sets_status_pending(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", status=TaskStatus.failed))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["reset", "task-a"])

    assert result.exit_code == 0
    assert get_task(tmp_path, "task-a").status is TaskStatus.pending


def test_reset_stale_running_task(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", status=TaskStatus.running))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["reset", "task-a"])

    assert result.exit_code == 0
    assert get_task(tmp_path, "task-a").status is TaskStatus.pending
    assert "Reset task-a (was: running)" in result.output


def test_reset_unknown_task_id_prints_error_but_continues(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", status=TaskStatus.failed))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["reset", "missing", "task-a"])

    assert result.exit_code == 0
    assert "Error: task not found: missing" in result.output
    assert get_task(tmp_path, "task-a").status is TaskStatus.pending


def test_reset_clean_worktree_removes_worktree_directory_and_branch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = _scratch_repo(tmp_path)
    _write_task(repo_root, _make_task("task-a", status=TaskStatus.failed))
    worktree = create_worktree("task-a", repo_root)
    monkeypatch.chdir(repo_root)

    result = runner.invoke(app, ["reset", "task-a", "--clean-worktree"])

    assert result.exit_code == 0
    assert not worktree.path.exists()
    assert not _branch_exists(repo_root, "praetor/task-a")


def test_reset_all_stale_finds_running_tasks(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("running-task", status=TaskStatus.running, offset=0))
    _write_task(tmp_path, _make_task("pending-task", status=TaskStatus.pending, offset=1))
    _write_task(tmp_path, _make_task("done-task", status=TaskStatus.done, offset=2))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["reset", "--all-stale"])

    assert result.exit_code == 0
    assert get_task(tmp_path, "running-task").status is TaskStatus.pending
    assert get_task(tmp_path, "pending-task").status is TaskStatus.pending
    assert get_task(tmp_path, "done-task").status is TaskStatus.done
    assert "Reset running-task (was: running)" in result.output
    assert "pending-task" not in result.output
    assert "done-task" not in result.output


def test_reset_rejects_both_explicit_ids_and_all_stale(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", status=TaskStatus.running))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["reset", "task-a", "--all-stale"])

    assert result.exit_code != 0
    assert "Use either" in result.output


def _make_task(
    task_id: str,
    *,
    status: TaskStatus,
    offset: int = 0,
) -> Task:
    return Task(
        id=task_id,
        status=status,
        verify="true",
        created=datetime(2026, 6, 10, 12, 0, tzinfo=UTC) + timedelta(minutes=offset),
        body=f"# {task_id}\n",
    )


def _write_task(repo_root: Path, task: Task) -> None:
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")


def _scratch_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-b", "main")
    _git(repo_root, "config", "user.email", "praetor@example.com")
    _git(repo_root, "config", "user.name", "Praetor Tests")
    (repo_root / ".gitignore").write_text(".praetor/\n")
    (repo_root / "README.md").write_text("# Scratch\n")
    _git(repo_root, "add", ".gitignore", "README.md")
    _git(repo_root, "commit", "-m", "initial commit")
    init_workspace(repo_root)
    return repo_root


def _branch_exists(repo_root: Path, branch: str) -> bool:
    completed = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.rstrip("\n")
