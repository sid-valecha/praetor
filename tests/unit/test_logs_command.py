import json
import os
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from praetor.cli import app
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskStatus
from praetor.state import init_workspace

runner = CliRunner()


def test_logs_show_review_failure_before_raw_log(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.review_failed))
    _write_run(tmp_path, "older-run", "task-a")
    (tmp_path / ".praetor" / "logs" / "task-a.log").write_text("raw executor log\n")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["logs", "task-a"])

    assert result.exit_code == 0
    assert "Review failure" in result.output
    assert "Unsafe validation change." in result.output
    assert "src/app.py:12" in result.output
    assert "Input validation can be bypassed." in result.output
    assert "recommendation: Validate before writing." in result.output
    assert result.output.index("Review failure") < result.output.index("raw executor log")


def test_logs_do_not_show_stale_review_failure_after_task_done(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("task-a", TaskStatus.done))
    _write_run(tmp_path, "older-run", "task-a")
    (tmp_path / ".praetor" / "logs" / "task-a.log").write_text("raw executor log\n")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["logs", "task-a"])

    assert result.exit_code == 0
    assert "Review failure" not in result.output
    assert result.output == "raw executor log\n"


def test_logs_reject_unknown_task_even_if_orphan_log_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    (tmp_path / ".praetor" / "logs" / "missing.log").write_text("orphan log\n")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["logs", "missing"])

    assert result.exit_code == 0
    assert "orphan log" not in result.output
    assert "No log found for missing" in result.output


def test_logs_reject_path_escape_task_id(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    (tmp_path / ".praetor" / "evil.log").write_text("secret\n")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["logs", "../evil"])

    assert "secret" not in result.output
    assert "No log found for ../evil" in result.output


def _make_task(task_id: str, status: TaskStatus) -> Task:
    return Task(
        id=task_id,
        status=status,
        created=datetime(2026, 6, 10, 17, 30, tzinfo=UTC),
        body=f"# {task_id}\n",
    )


def _write_task(repo_root: Path, task: Task) -> None:
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")


def _write_run(repo_root: Path, run_id: str, task_id: str) -> None:
    path = repo_root / ".praetor" / "runs" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": run_id,
                "status": "completed",
                "started_at": "2026-06-12T12:00:00Z",
                "finished_at": "2026-06-12T12:01:00Z",
                "max_parallel": 1,
                "base_branch": "main",
                "merge_strategy": None,
                "max_review_retries": 1,
                "task_runs": [
                    {
                        "task_id": task_id,
                        "status": "review_failed",
                        "started_at": "2026-06-12T12:00:10Z",
                        "finished_at": "2026-06-12T12:00:20Z",
                        "adapter": "mock",
                        "verify_command": "pytest",
                        "agent_exit_code": 0,
                        "verify_exit_code": 0,
                        "merge_status": None,
                        "detail": "review needs revision",
                        "review": {
                            "verdict": "needs_revision",
                            "severity": "error",
                            "summary": "Unsafe validation change.",
                            "findings": [
                                {
                                    "severity": "error",
                                    "file": "src/app.py",
                                    "line": 12,
                                    "message": "Input validation can be bypassed.",
                                    "recommendation": "Validate before writing.",
                                }
                            ],
                            "reviewer_adapter": "mock-reviewer",
                            "started_at": "2026-06-12T12:00:15Z",
                            "finished_at": "2026-06-12T12:00:18Z",
                            "duration_ms": 3000,
                        },
                    }
                ],
            }
        )
    )
    os.utime(path, (100, 100))
