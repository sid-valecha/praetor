from datetime import UTC, datetime
from pathlib import Path

import pytest

from praetor.frontmatter import dump_task, parse_task
from praetor.models import Task, TaskStatus
from praetor.state import (
    get_task,
    init_workspace,
    list_tasks,
    read_global_state,
    update_task_status,
    write_global_state,
)


def make_task(task_id: str, created: datetime, status: TaskStatus = TaskStatus.pending) -> Task:
    return Task(
        id=task_id,
        status=status,
        created=created,
        body=f"# Task {task_id}\n",
    )


def test_init_workspace_creates_dirs(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    assert (tmp_path / ".praetor" / "tasks").is_dir()
    assert (tmp_path / ".praetor" / "logs").is_dir()
    assert (tmp_path / ".praetor" / "state.json").is_file()
    assert (tmp_path / ".praetor" / "context.md").is_file()


def test_init_workspace_idempotent(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    init_workspace(tmp_path)

    assert (tmp_path / ".praetor" / "tasks").is_dir()
    assert (tmp_path / ".praetor" / "logs").is_dir()


def test_init_workspace_creates_gitignore_when_absent(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    gitignore = tmp_path / ".gitignore"
    assert gitignore.is_file()
    assert ".praetor/" in gitignore.read_text().splitlines()


def test_init_workspace_appends_to_existing_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("node_modules/\n*.log\n")

    init_workspace(tmp_path)

    lines = (tmp_path / ".gitignore").read_text().splitlines()
    assert "node_modules/" in lines
    assert "*.log" in lines
    assert ".praetor/" in lines


def test_init_workspace_does_not_duplicate_praetor_gitignore_entry(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".praetor/\n*.log\n")

    init_workspace(tmp_path)

    text = (tmp_path / ".gitignore").read_text()
    assert text.count(".praetor/") == 1


def test_init_workspace_recognizes_unslashed_praetor_gitignore_entry(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".praetor\n")

    init_workspace(tmp_path)

    text = (tmp_path / ".gitignore").read_text()
    assert text.count(".praetor") == 1


def test_init_workspace_returns_gitignore_note_when_modified(tmp_path: Path) -> None:
    notes = init_workspace(tmp_path)

    assert any(".gitignore" in note and "Commit" in note for note in notes)


def test_init_workspace_returns_no_note_when_gitignore_already_correct(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".praetor/\n")

    notes = init_workspace(tmp_path)

    assert notes == []


def test_init_workspace_seeds_context_from_claude_md(tmp_path: Path) -> None:
    context = "# Repo Context\n\nUse this for task context.\n"
    (tmp_path / "CLAUDE.md").write_text(context)

    init_workspace(tmp_path)

    assert (tmp_path / ".praetor" / "context.md").read_text() == context


def test_init_workspace_creates_empty_context_when_no_claude_md(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    context = (tmp_path / ".praetor" / "context.md").read_text()
    assert context


def test_list_tasks_empty(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    assert list_tasks(tmp_path) == []


def test_list_tasks_returns_sorted_by_created(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    tasks_dir = tmp_path / ".praetor" / "tasks"
    newer = make_task("002-newer", datetime(2026, 5, 24, 14, 22, tzinfo=UTC))
    older = make_task("001-older", datetime(2026, 5, 23, 14, 22, tzinfo=UTC))
    dump_task(newer, tasks_dir / "002-newer.md")
    dump_task(older, tasks_dir / "001-older.md")

    tasks = list_tasks(tmp_path)

    assert [task.id for task in tasks] == ["001-older", "002-newer"]


def test_get_task_found(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    task = make_task("001-found", datetime(2026, 5, 23, 14, 22, tzinfo=UTC))
    dump_task(task, tmp_path / ".praetor" / "tasks" / "001-found.md")

    found = get_task(tmp_path, "001-found")

    assert found.id == "001-found"


def test_get_task_not_found(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    with pytest.raises(KeyError, match="Task not found: missing"):
        get_task(tmp_path, "missing")


def test_update_task_status_persists(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    path = tmp_path / ".praetor" / "tasks" / "001-task.md"
    task = make_task("001-task", datetime(2026, 5, 23, 14, 22, tzinfo=UTC))
    dump_task(task, path)

    update_task_status(tmp_path, "001-task", TaskStatus.done)

    assert parse_task(path).status is TaskStatus.done


def test_write_read_global_state_round_trips(tmp_path: Path) -> None:
    data = {"version": 1, "last_run": "2026-06-06T12:00:00Z", "active_run_id": "run-1"}

    write_global_state(tmp_path, data)

    assert read_global_state(tmp_path) == data


def test_atomic_write_uses_tmp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = {"version": 1, "last_run": None}
    write_global_state(tmp_path, original)

    def raise_during_replace(self: Path, target: Path) -> Path:
        raise OSError(f"replace failed for {target}")

    monkeypatch.setattr(Path, "replace", raise_during_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_global_state(tmp_path, {"version": 1, "last_run": "later"})

    assert read_global_state(tmp_path) == original
