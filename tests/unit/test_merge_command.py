from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess

from typer.testing import CliRunner

from praetor.cli import app
from praetor.frontmatter import dump_task
from praetor.merge import MergeResult
from praetor.models import Task, TaskStatus
from praetor.state import get_task, init_workspace

runner = CliRunner()


def test_merge_single_pending_merge_task_succeeds(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.pending_merge))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "praetor.merge_queue.merge_task",
        lambda task_id, repo_root, base_branch: MergeResult(
            task_id=task_id,
            success=True,
            merge_commit_sha="abc123",
            message="merged",
        ),
    )

    result = runner.invoke(app, ["merge", "task-a"])

    assert result.exit_code == 0
    assert get_task(tmp_path, "task-a").status is TaskStatus.done


def test_merge_skips_non_pending_merge_tasks(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.done))
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(
        "praetor.merge_queue.merge_task",
        lambda task_id, repo_root, base_branch: calls.append(task_id),
    )

    result = runner.invoke(app, ["merge", "task-a"])

    assert result.exit_code == 0
    assert calls == []
    assert "Skipping task-a" in result.output


def test_merge_all_processes_in_dependency_order(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(
        tmp_path,
        _make_task("child", TaskStatus.pending_merge, offset=0, depends_on=["parent"]),
    )
    _write_task(tmp_path, _make_task("parent", TaskStatus.pending_merge, offset=1))
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_merge(task_id: str, repo_root: Path, base_branch: str) -> MergeResult:
        calls.append(task_id)
        return MergeResult(
            task_id=task_id,
            success=True,
            merge_commit_sha=task_id,
            message="merged",
        )

    monkeypatch.setattr("praetor.merge_queue.merge_task", fake_merge)

    result = runner.invoke(app, ["merge", "--all"])

    assert result.exit_code == 0
    assert calls == ["parent", "child"]


def test_merge_retry_processes_merge_failed_tasks(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.merge_failed))
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_merge(task_id: str, repo_root: Path, base_branch: str) -> MergeResult:
        calls.append(task_id)
        return MergeResult(
            task_id=task_id,
            success=True,
            merge_commit_sha="abc123",
            message="merged",
        )

    monkeypatch.setattr("praetor.merge_queue.merge_task", fake_merge)

    result = runner.invoke(app, ["merge", "--retry", "task-a"])

    assert result.exit_code == 0
    assert calls == ["task-a"]
    assert get_task(tmp_path, "task-a").status is TaskStatus.done


def test_merge_handles_dirty_base_repo(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-b", "main")
    _git(repo_root, "config", "user.email", "praetor@example.com")
    _git(repo_root, "config", "user.name", "Praetor Tests")
    (repo_root / "README.md").write_text("# Scratch\n")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-m", "initial commit")
    init_workspace(repo_root)
    _write_task(repo_root, _make_task("task-a", TaskStatus.pending_merge))
    (repo_root / "dirty.txt").write_text("dirty\n")
    monkeypatch.chdir(repo_root)

    result = runner.invoke(app, ["merge", "task-a"])

    assert result.exit_code == 0
    assert get_task(repo_root, "task-a").status is TaskStatus.merge_failed
    log_text = (repo_root / ".praetor" / "logs" / "task-a.log").read_text()
    assert "base repo has uncommitted changes" in log_text


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
        created=datetime(2026, 6, 8, 12, 0, tzinfo=UTC) + timedelta(minutes=offset),
        body=f"# {task_id}\n",
    )


def _write_task(repo_root: Path, task: Task) -> None:
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.rstrip("\n")
