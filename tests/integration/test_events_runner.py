from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess

import pytest

from praetor.events import RunnerEvent
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskResult, TaskStatus
from praetor.runner import drain_queue
from praetor.state import init_workspace


class RecordingAdapter:
    name = "recording"

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        task_id = _task_id_from_prompt(prompt)
        return TaskResult(exit_code=0, stdout=f"{task_id} done\n", stderr="", duration_ms=0)


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
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


def test_drain_emits_events_in_sequential_mode(scratch_repo: Path) -> None:
    _write_task(scratch_repo, _make_task("task-a", offset=0))
    _write_task(scratch_repo, _make_task("task-b", offset=1))
    events: list[RunnerEvent] = []

    drain_queue(scratch_repo, RecordingAdapter(), on_event=events.append)

    assert [(event.type, event.task_id) for event in events] == [
        ("drain_started", None),
        ("task_dispatched", "task-a"),
        ("task_completed", "task-a"),
        ("task_dispatched", "task-b"),
        ("task_completed", "task-b"),
        ("drain_finished", None),
    ]


def test_drain_emits_failure_events(scratch_repo: Path) -> None:
    _write_task(scratch_repo, _make_task("task-a", verify="false"))
    events: list[RunnerEvent] = []

    drain_queue(scratch_repo, RecordingAdapter(), on_event=events.append)

    assert ("task_failed", "task-a") in [(event.type, event.task_id) for event in events]


def test_drain_emits_merge_events_in_auto_mode(scratch_repo: Path) -> None:
    _write_task(scratch_repo, _make_task("task-a", offset=0))
    _write_task(scratch_repo, _make_task("task-b", offset=1))
    events: list[RunnerEvent] = []

    drain_queue(
        scratch_repo,
        RecordingAdapter(),
        max_parallel=2,
        merge_strategy="auto",
        on_event=events.append,
    )

    event_pairs = [(event.type, event.task_id) for event in events]
    for task_id in {"task-a", "task-b"}:
        assert ("merge_started", task_id) in event_pairs
        assert ("merge_succeeded", task_id) in event_pairs


def _make_task(
    task_id: str,
    *,
    offset: int = 0,
    verify: str = "true",
) -> Task:
    return Task(
        id=task_id,
        status=TaskStatus.pending,
        verify=verify,
        created=datetime(2026, 6, 10, 12, 0, tzinfo=UTC) + timedelta(minutes=offset),
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
