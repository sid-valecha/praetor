from pathlib import Path

from typer.testing import CliRunner

from praetor.adapters.mock import MockAdapter
from praetor.cli import app
from praetor.state import init_workspace

runner = CliRunner()


def _assert_invalid_option(result, expected_message: str) -> None:
    assert result.exit_code != 0
    assert expected_message in result.output or "Error" in result.output or "Usage" in result.output


def test_run_rejects_merge_strategy_with_default_max_parallel(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run", "--merge-strategy", "auto"])

    assert result.exit_code != 0
    assert "only applies in parallel mode" in result.output


def test_run_rejects_merge_strategy_with_max_parallel_1(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["run", "--merge-strategy", "auto", "--max-parallel", "1"],
    )

    assert result.exit_code != 0
    assert "only applies in parallel mode" in result.output


def test_run_accepts_merge_strategy_with_max_parallel_greater_than_one(
    tmp_path: Path, monkeypatch
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("praetor.commands.run.get_adapter", lambda adapter, **kwargs: MockAdapter())

    result = runner.invoke(
        app,
        ["run", "--merge-strategy", "auto", "--max-parallel", "2"],
    )

    assert result.exit_code == 0


def test_run_rejects_invalid_max_iterations(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run", "--max-iterations", "0"])

    _assert_invalid_option(result, "--max-iterations must be >= 1")


def test_run_rejects_invalid_max_runtime(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run", "--max-runtime", "0"])

    _assert_invalid_option(result, "--max-runtime must be > 0")


def test_run_rejects_invalid_max_review_retries(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run", "--max-review-retries", "-1"])

    _assert_invalid_option(
        result,
        "--max-review-retries must be >= 0",
    )


def test_run_passes_max_review_retries_to_drain_queue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_drain_queue(repo_root: Path, adapter: object, **kwargs: object) -> None:
        captured["repo_root"] = repo_root
        captured["adapter"] = adapter
        captured.update(kwargs)

    monkeypatch.setattr("praetor.commands.run.get_adapter", lambda adapter, **kwargs: MockAdapter())
    monkeypatch.setattr("praetor.commands.run.drain_queue", fake_drain_queue)

    result = runner.invoke(app, ["run", "--max-review-retries", "0"])

    assert result.exit_code == 0
    assert captured["max_review_retries"] == 0

    result = runner.invoke(app, ["run", "--max-review-retries", "2"])

    assert result.exit_code == 0
    assert captured["max_review_retries"] == 2


def test_run_passes_model_and_effort_to_adapter_factory(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_get_adapter(adapter: str, **kwargs: object) -> MockAdapter:
        captured["adapter"] = adapter
        captured.update(kwargs)
        return MockAdapter()

    monkeypatch.setattr("praetor.commands.run.get_adapter", fake_get_adapter)

    result = runner.invoke(
        app,
        ["run", "--adapter", "claude", "--model", "haiku", "--effort", "low"],
    )

    assert result.exit_code == 0
    assert captured == {"adapter": "claude", "model": "haiku", "effort": "low"}
