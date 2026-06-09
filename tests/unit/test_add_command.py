from pathlib import Path

from typer.testing import CliRunner

from praetor.cli import app
from praetor.frontmatter import parse_task
from praetor.models import Task
from praetor.state import init_workspace


runner = CliRunner()


def added_task(repo_root: Path) -> Task:
    task_paths = list((repo_root / ".praetor" / "tasks").glob("*.md"))
    assert len(task_paths) == 1
    return parse_task(task_paths[0])


def test_add_command_defaults_parallel_ok_true(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["add", "--title", "Parallel default"])

    assert result.exit_code == 0
    assert added_task(tmp_path).parallel_ok is True


def test_add_command_accepts_parallel_ok_flag(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["add", "--title", "Parallel explicit", "--parallel-ok"])

    assert result.exit_code == 0
    assert added_task(tmp_path).parallel_ok is True


def test_add_command_accepts_no_parallel_ok_flag(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["add", "--title", "Parallel disabled", "--no-parallel-ok"])

    assert result.exit_code == 0
    assert added_task(tmp_path).parallel_ok is False


def test_add_command_accepts_merge_strategy(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["add", "--title", "Auto merge", "--merge-strategy", "auto"],
    )

    assert result.exit_code == 0
    assert added_task(tmp_path).merge_strategy == "auto"
