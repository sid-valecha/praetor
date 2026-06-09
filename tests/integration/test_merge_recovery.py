from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess

from typer.testing import CliRunner

from praetor.cli import app
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskResult, TaskStatus
from praetor.runner import drain_queue
from praetor.state import get_task, init_workspace

runner = CliRunner()


class WritingAdapter:
    name = "writing"

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        task_id = _task_id_from_prompt(prompt)
        (cwd / f"{task_id}.txt").write_text(f"{task_id}\n")
        return TaskResult(exit_code=0, stdout=f"{task_id} done\n", stderr="", duration_ms=0)


def test_manual_parallel_merge_recovery_flow(tmp_path: Path, monkeypatch) -> None:
    repo_root = _scratch_repo(tmp_path)
    _write_task(repo_root, _make_task("task-a", offset=0))
    _write_task(repo_root, _make_task("task-b", offset=1))
    _write_task(repo_root, _make_task("task-c", offset=2))

    drain_queue(repo_root, WritingAdapter(), max_parallel=3)

    assert get_task(repo_root, "task-a").status is TaskStatus.pending_merge
    assert get_task(repo_root, "task-b").status is TaskStatus.pending_merge
    assert get_task(repo_root, "task-c").status is TaskStatus.pending_merge

    monkeypatch.chdir(repo_root)
    result = runner.invoke(app, ["merge", "--all"])

    assert result.exit_code == 0
    assert get_task(repo_root, "task-a").status is TaskStatus.done
    assert get_task(repo_root, "task-b").status is TaskStatus.done
    assert get_task(repo_root, "task-c").status is TaskStatus.done
    assert _branch_contains(repo_root, "main", "praetor/task-a")
    assert _branch_contains(repo_root, "main", "praetor/task-b")
    assert _branch_contains(repo_root, "main", "praetor/task-c")


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


def _make_task(task_id: str, *, offset: int = 0) -> Task:
    return Task(
        id=task_id,
        status=TaskStatus.pending,
        verify="true",
        created=datetime(2026, 6, 8, 12, 0, tzinfo=UTC) + timedelta(minutes=offset),
        body=f"# {task_id}\n\nTASK_ID: {task_id}\n",
    )


def _write_task(repo_root: Path, task: Task) -> None:
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")


def _task_id_from_prompt(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith("TASK_ID: "):
            return line.removeprefix("TASK_ID: ")
    raise AssertionError("prompt missing TASK_ID marker")


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.rstrip("\n")


def _branch_contains(repo_root: Path, branch: str, contains: str) -> bool:
    return branch in _git(repo_root, "branch", "--contains", contains)
