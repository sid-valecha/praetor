from pathlib import Path

from typer.testing import CliRunner

from praetor.adapters.mock import MockAdapter
from praetor.cli import app
from praetor.state import init_workspace

runner = CliRunner()


def test_run_rejects_merge_strategy_with_default_max_parallel(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run", "--merge-strategy", "auto"])

    assert result.exit_code != 0
    assert "only applies in parallel mode" in result.output
    assert "--max-parallel" in result.output


def test_run_rejects_merge_strategy_with_max_parallel_1(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["run", "--merge-strategy", "auto", "--max-parallel", "1"],
    )

    assert result.exit_code != 0
    assert "only applies in parallel mode" in result.output
    assert "--max-parallel" in result.output


def test_run_accepts_merge_strategy_with_max_parallel_greater_than_one(
    tmp_path: Path, monkeypatch
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("praetor.commands.run.get_adapter", lambda adapter: MockAdapter())

    result = runner.invoke(
        app,
        ["run", "--merge-strategy", "auto", "--max-parallel", "2"],
    )

    assert result.exit_code == 0
