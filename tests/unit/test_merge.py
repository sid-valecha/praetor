from pathlib import Path
import subprocess

import pytest

from praetor.merge import merge_task
from praetor.worktree import create_worktree


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-b", "main")
    _git(repo_root, "config", "user.email", "praetor@example.com")
    _git(repo_root, "config", "user.name", "Praetor Tests")
    (repo_root / "README.md").write_text("# Scratch\n")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-m", "initial commit")
    return repo_root


def test_merge_clean_branch_succeeds_with_no_ff_commit(scratch_repo: Path) -> None:
    _create_task_branch(scratch_repo, "task-a", "task-a.txt", "task a\n")

    result = merge_task("task-a", scratch_repo)

    assert result.success is True
    assert result.message == "merged"
    assert result.merge_commit_sha == _git(scratch_repo, "rev-parse", "HEAD")


def test_merge_returns_failure_on_dirty_base_repo(scratch_repo: Path) -> None:
    _create_task_branch(scratch_repo, "task-a", "task-a.txt", "task a\n")
    (scratch_repo / "dirty.txt").write_text("dirty\n")

    result = merge_task("task-a", scratch_repo)

    assert result.success is False
    assert result.message == "base repo has uncommitted changes; refusing to merge"


def test_merge_idempotent_for_already_merged_branch(scratch_repo: Path) -> None:
    _create_task_branch(scratch_repo, "task-a", "task-a.txt", "task a\n")
    first = merge_task("task-a", scratch_repo)

    second = merge_task("task-a", scratch_repo)

    assert first.success is True
    assert second.success is True
    assert second.message == "already merged"
    assert second.merge_commit_sha == _git(scratch_repo, "rev-parse", "HEAD")


def test_merge_returns_failure_on_missing_branch(scratch_repo: Path) -> None:
    result = merge_task("missing", scratch_repo)

    assert result.success is False
    assert result.message == "branch not found: praetor/missing"


def test_merge_conflict_aborts_cleanly(scratch_repo: Path) -> None:
    (scratch_repo / "shared.txt").write_text("base\n")
    _git(scratch_repo, "add", "shared.txt")
    _git(scratch_repo, "commit", "-m", "add shared")
    _git(scratch_repo, "checkout", "-b", "praetor/task-a")
    (scratch_repo / "shared.txt").write_text("task\n")
    _git(scratch_repo, "commit", "-am", "task edit")
    _git(scratch_repo, "checkout", "main")
    (scratch_repo / "shared.txt").write_text("main\n")
    _git(scratch_repo, "commit", "-am", "main edit")

    result = merge_task("task-a", scratch_repo)

    assert result.success is False
    assert result.message == "merge conflict"
    assert result.conflict_files == ["shared.txt"]
    assert _git(scratch_repo, "status", "--porcelain") == ""
    assert not (scratch_repo / ".git" / "MERGE_HEAD").exists()


def test_merge_uses_no_ff(scratch_repo: Path) -> None:
    _create_task_branch(scratch_repo, "task-a", "task-a.txt", "task a\n")

    result = merge_task("task-a", scratch_repo)

    assert result.merge_commit_sha is not None
    parents = _git(
        scratch_repo,
        "rev-list",
        "--parents",
        "-n",
        "1",
        result.merge_commit_sha,
    ).split()
    assert len(parents) == 3


def test_merge_uses_branch_recorded_by_worktree_metadata(scratch_repo: Path) -> None:
    (scratch_repo / ".gitignore").write_text(".praetor/\n")
    _git(scratch_repo, "add", ".gitignore")
    _git(scratch_repo, "commit", "-m", "ignore praetor")
    _git(scratch_repo, "branch", "praetor")
    worktree = create_worktree("task-a", scratch_repo)
    (worktree.path / "task-a.txt").write_text("task a\n")
    _git(worktree.path, "add", "task-a.txt")
    _git(worktree.path, "commit", "-m", "task a")

    result = merge_task("task-a", scratch_repo)

    assert result.success is True
    assert result.message == "merged"
    assert worktree.branch != "praetor/task-a"
    assert _git(scratch_repo, "branch", "--contains", worktree.branch).strip() != ""


def _create_task_branch(
    repo_root: Path,
    task_id: str,
    filename: str,
    content: str,
) -> None:
    _git(repo_root, "checkout", "-b", f"praetor/{task_id}")
    (repo_root / filename).write_text(content)
    _git(repo_root, "add", filename)
    _git(repo_root, "commit", "-m", f"task {task_id}")
    _git(repo_root, "checkout", "main")


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.rstrip("\n")
