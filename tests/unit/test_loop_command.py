import os
import signal
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from praetor.adapters.mock import MockAdapter
from praetor.cli import app
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskStatus
from praetor.state import get_task, init_workspace

runner = CliRunner()


def _assert_invalid_option(result, expected_message: str) -> None:
    assert result.exit_code != 0
    assert expected_message in result.output or "Error" in result.output or "Usage" in result.output


def test_loop_once_drains_and_exits(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "praetor.commands.loop.get_adapter", lambda adapter, **kwargs: MockAdapter()
    )

    result = runner.invoke(app, ["loop", "--once"])

    assert result.exit_code == 0
    assert get_task(tmp_path, "task-a").status is TaskStatus.done
    assert "[drained] 1 tasks completed (sequential)" in result.output


def test_loop_handles_empty_queue_with_once(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "praetor.commands.loop.get_adapter", lambda adapter, **kwargs: MockAdapter()
    )

    result = runner.invoke(app, ["loop", "--once"])

    assert result.exit_code == 0
    assert "[drained] 0 tasks completed (sequential)" in result.output


def test_loop_rejects_merge_strategy_with_default_max_parallel(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["loop", "--merge-strategy", "auto"])

    assert result.exit_code != 0
    assert "only applies in parallel mode" in result.output


def test_loop_picks_up_new_task_during_wait(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "praetor.commands.loop.get_adapter", lambda adapter, **kwargs: MockAdapter()
    )
    monkeypatch.setattr("praetor.loop.Observer", None)

    worker = threading.Thread(target=_write_task_then_stop_loop, args=(tmp_path,), daemon=True)
    worker.start()

    result = runner.invoke(app, ["loop", "--poll-interval", "0.1"])

    worker.join(timeout=1)
    assert result.exit_code == 0
    assert get_task(tmp_path, "task-a").status is TaskStatus.done
    assert "[wake]" in result.output
    assert "[stopping] received SIGINT, will exit after current pass" in result.output


def test_loop_passes_model_and_effort_to_adapter_factory(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_get_adapter(adapter: str, **kwargs: object) -> MockAdapter:
        captured["adapter"] = adapter
        captured.update(kwargs)
        return MockAdapter()

    monkeypatch.setattr("praetor.commands.loop.get_adapter", fake_get_adapter)

    result = runner.invoke(
        app,
        ["loop", "--once", "--adapter", "claude", "--model", "haiku", "--effort", "low"],
    )

    assert result.exit_code == 0
    assert captured == {"adapter": "claude", "model": "haiku", "effort": "low"}


def test_loop_passes_reviewer_adapter_through_options(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_loop_queue(
        repo_root: Path, adapter: object, options: object, **kwargs: object
    ) -> None:
        captured["repo_root"] = repo_root
        captured["adapter"] = adapter
        captured["options"] = options
        captured.update(kwargs)

    monkeypatch.setattr("praetor.commands.loop.loop_queue", fake_loop_queue)

    result = runner.invoke(
        app,
        [
            "loop",
            "--once",
            "--adapter",
            "claude",
            "--model",
            "haiku",
            "--effort",
            "low",
            "--reviewer-model",
            "opus",
            "--reviewer-effort",
            "high",
        ],
    )

    assert result.exit_code == 0
    assert getattr(captured["adapter"], "model") == "haiku"
    assert getattr(captured["adapter"], "effort") == "low"
    reviewer = captured["options"].reviewer_adapter
    assert getattr(reviewer, "model") == "opus"
    assert getattr(reviewer, "effort") == "high"


def test_loop_routes_executor_and_reviewer_as_roles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_loop_queue(
        repo_root: Path, adapter: object, options: object, **kwargs: object
    ) -> None:
        captured["repo_root"] = repo_root
        captured["adapter"] = adapter
        captured["options"] = options
        captured.update(kwargs)

    monkeypatch.setattr("praetor.commands.loop.loop_queue", fake_loop_queue)

    result = runner.invoke(
        app,
        [
            "loop",
            "--once",
            "--adapter",
            "codex",
            "--model",
            "spark",
            "--effort",
            "medium",
            "--reviewer-adapter",
            "claude",
            "--reviewer-model",
            "opus",
        ],
    )

    assert result.exit_code == 0
    assert getattr(captured["adapter"], "name") == "codex"
    assert getattr(captured["adapter"], "model") == "gpt-5.3-codex-spark"
    assert getattr(captured["adapter"], "effort") == "medium"
    reviewer = captured["options"].reviewer_adapter
    assert getattr(reviewer, "name") == "claude"
    assert getattr(reviewer, "model") == "opus"


def test_loop_rejects_invalid_max_review_retries(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["loop", "--max-review-retries", "-1", "--once"])

    _assert_invalid_option(
        result,
        "--max-review-retries must be >= 0",
    )


def test_loop_passes_max_review_retries_through_options(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_loop_queue(
        repo_root: Path, adapter: object, options: object, **kwargs: object
    ) -> None:
        captured["repo_root"] = repo_root
        captured["adapter"] = adapter
        captured["options"] = options
        captured.update(kwargs)

    monkeypatch.setattr(
        "praetor.commands.loop.get_adapter", lambda adapter, **kwargs: MockAdapter()
    )
    monkeypatch.setattr("praetor.commands.loop.loop_queue", fake_loop_queue)

    result = runner.invoke(app, ["loop", "--once", "--max-review-retries", "0"])

    assert result.exit_code == 0
    assert captured["options"].max_review_retries == 0

    result = runner.invoke(app, ["loop", "--once", "--max-review-retries", "2"])

    assert result.exit_code == 0
    assert captured["options"].max_review_retries == 2


def _write_task_then_stop_loop(repo_root: Path) -> None:
    time.sleep(0.2)
    _write_task(repo_root, _make_task("task-a"))
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if get_task(repo_root, "task-a").status is TaskStatus.done:
            os.kill(os.getpid(), signal.SIGINT)
            return
        time.sleep(0.02)
    os.kill(os.getpid(), signal.SIGINT)


def _make_task(task_id: str, *, offset: int = 0) -> Task:
    return Task(
        id=task_id,
        status=TaskStatus.pending,
        verify="true",
        created=datetime(2026, 6, 10, 12, 0, tzinfo=UTC) + timedelta(minutes=offset),
        body=f"# {task_id}\n",
    )


def _write_task(repo_root: Path, task: Task) -> None:
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")
