from pathlib import Path
import re
import subprocess

import pytest

from praetor.worktree import WorktreeError, create_worktree, list_worktrees, remove_worktree


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init")
    _git(repo_root, "config", "user.email", "praetor@example.com")
    _git(repo_root, "config", "user.name", "Praetor Tests")
    (repo_root / ".gitignore").write_text(".praetor/\n")
    (repo_root / "README.md").write_text("# Scratch\n")
    _git(repo_root, "add", ".gitignore", "README.md")
    _git(repo_root, "commit", "-m", "initial commit")
    return repo_root


def test_create_worktree_basic(scratch_repo: Path) -> None:
    worktree = create_worktree("task-001", scratch_repo)

    assert worktree.task_id == "task-001"
    assert worktree.path == scratch_repo / ".praetor" / "worktrees" / "task-001"
    assert worktree.branch == "praetor/task-001"
    assert re.fullmatch(r"[0-9a-f]{40}", worktree.base_ref)
    assert worktree.path.is_dir()


def test_create_worktree_duplicate_path(scratch_repo: Path) -> None:
    create_worktree("task-001", scratch_repo)

    with pytest.raises(WorktreeError, match="Worktree path already exists"):
        create_worktree("task-001", scratch_repo)


def test_create_worktree_duplicate_branch(scratch_repo: Path) -> None:
    _git(scratch_repo, "branch", "praetor/task-002")

    with pytest.raises(WorktreeError, match="Worktree branch already exists"):
        create_worktree("task-002", scratch_repo)


def test_list_worktrees_empty(scratch_repo: Path) -> None:
    assert list_worktrees(scratch_repo) == []


def test_list_worktrees_after_create(scratch_repo: Path) -> None:
    create_worktree("a", scratch_repo)
    create_worktree("b", scratch_repo)

    worktrees = list_worktrees(scratch_repo)

    assert {worktree.task_id for worktree in worktrees} == {"a", "b"}
    assert len(worktrees) == 2
    assert all(re.fullmatch(r"[0-9a-f]{40}", worktree.base_ref) for worktree in worktrees)


def test_remove_worktree_cleans_branch(scratch_repo: Path) -> None:
    worktree = create_worktree("task-003", scratch_repo)

    remove_worktree("task-003", scratch_repo)

    assert not worktree.path.exists()
    assert _git(scratch_repo, "branch", "--list", "praetor/task-003") == ""


def test_remove_worktree_missing_strict(scratch_repo: Path) -> None:
    with pytest.raises(WorktreeError, match="Worktree path does not exist"):
        remove_worktree("never-existed", scratch_repo, force=False)


def test_remove_worktree_missing_force(scratch_repo: Path) -> None:
    assert remove_worktree("never-existed", scratch_repo, force=True) is None


def test_worktree_isolation(scratch_repo: Path) -> None:
    worktree = create_worktree("task-004", scratch_repo)
    (worktree.path / "new_file.txt").write_text("worktree-only\n")

    assert _git(scratch_repo, "status", "--porcelain") == ""


def test_create_worktree_explicit_sha(scratch_repo: Path) -> None:
    first_sha = _git(scratch_repo, "rev-parse", "HEAD")
    (scratch_repo / "second.txt").write_text("second\n")
    _git(scratch_repo, "add", "second.txt")
    _git(scratch_repo, "commit", "-m", "second commit")
    assert _git(scratch_repo, "rev-parse", "HEAD") != first_sha

    worktree = create_worktree("task-005", scratch_repo, base_ref=first_sha)

    assert worktree.base_ref == first_sha
    assert _git(worktree.path, "rev-parse", "HEAD") == first_sha


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.rstrip("\n")
