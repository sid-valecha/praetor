import json
import os
from datetime import UTC, datetime
from pathlib import Path

from praetor.models import Task, TaskStatus
from praetor.recovery import latest_review_failure, review_failure_for_task, waiting_on
from praetor.state import init_workspace


def test_latest_review_failure_scans_past_later_unrelated_runs(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_run(tmp_path, "older-run", "task-a", mtime=100)
    _write_run(tmp_path, "newer-run", "task-b", summary="Different failure", mtime=200)

    failure = latest_review_failure(tmp_path, "task-a")

    assert failure is not None
    assert failure["run_id"] == "older-run"
    assert failure["detail"] == "review needs revision"
    assert failure["verdict"] == "needs_revision"
    assert failure["summary"] == "Unsafe validation change."
    assert failure["findings"] == [
        {
            "severity": "error",
            "file": "src/app.py",
            "line": 12,
            "message": "Input validation can be bypassed.",
            "recommendation": "Validate before writing.",
        }
    ]


def test_latest_review_failure_uses_run_timestamps_not_file_mtime(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    _write_run(
        tmp_path,
        "older-logical-run",
        "task-a",
        summary="Older logical failure.",
        started_at="2026-06-12T12:00:00Z",
        finished_at="2026-06-12T12:01:00Z",
        mtime=200,
    )
    _write_run(
        tmp_path,
        "newer-logical-run",
        "task-a",
        summary="Newer logical failure.",
        started_at="2026-06-12T13:00:00Z",
        finished_at="2026-06-12T13:01:00Z",
        mtime=100,
    )

    failure = latest_review_failure(tmp_path, "task-a")

    assert failure is not None
    assert failure["run_id"] == "newer-logical-run"
    assert failure["summary"] == "Newer logical failure."


def test_latest_review_failure_returns_none_when_newer_review_pass_exists(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    _write_run(
        tmp_path,
        "older-rejection",
        "task-a",
        summary="Resolved criticism.",
        started_at="2026-06-12T12:00:00Z",
        finished_at="2026-06-12T12:01:00Z",
        mtime=100,
    )
    _write_run(
        tmp_path,
        "newer-pass",
        "task-a",
        verdict="pass",
        status="done",
        summary="Review passed.",
        detail=None,
        started_at="2026-06-12T13:00:00Z",
        finished_at="2026-06-12T13:01:00Z",
        mtime=200,
    )

    assert latest_review_failure(tmp_path, "task-a") is None


def test_latest_review_failure_skips_invalid_run_records(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_run(tmp_path, "valid-run", "task-a", mtime=100)
    invalid_path = tmp_path / ".praetor" / "runs" / "invalid-run.json"
    invalid_path.write_text("{not json")
    os.utime(invalid_path, (200, 200))

    failure = latest_review_failure(tmp_path, "task-a")

    assert failure is not None
    assert failure["run_id"] == "valid-run"


def test_review_failure_only_attaches_to_current_review_failed_task(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_run(tmp_path, "older-run", "task-a", mtime=100)
    task = _make_task("task-a", TaskStatus.done)

    assert latest_review_failure(tmp_path, "task-a") is not None
    assert review_failure_for_task(tmp_path, task) is None


def test_waiting_on_describes_review_failed_dependency(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_run(tmp_path, "older-run", "task-a", mtime=100)
    parent = _make_task("task-a", TaskStatus.review_failed)
    child = _make_task("task-b", TaskStatus.pending, depends_on=["task-a"])

    result = waiting_on(tmp_path, child, [parent, child])

    assert result == [
        {
            "task_id": "task-a",
            "status": "review_failed",
            "reason": "dependency_review_failed",
            "review_summary": "Unsafe validation change.",
        }
    ]


def _make_task(
    task_id: str,
    status: TaskStatus,
    *,
    depends_on: list[str] | None = None,
) -> Task:
    return Task(
        id=task_id,
        status=status,
        depends_on=depends_on or [],
        created=datetime(2026, 6, 10, 17, 30, tzinfo=UTC),
        body=f"# {task_id}\n",
    )


def _write_run(
    repo_root: Path,
    run_id: str,
    task_id: str,
    *,
    verdict: str = "needs_revision",
    status: str = "review_failed",
    summary: str = "Unsafe validation change.",
    detail: str | None = "review needs revision",
    started_at: str = "2026-06-12T12:00:00Z",
    finished_at: str = "2026-06-12T12:01:00Z",
    mtime: int,
) -> None:
    path = repo_root / ".praetor" / "runs" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": run_id,
                "status": "completed",
                "started_at": started_at,
                "finished_at": finished_at,
                "max_parallel": 1,
                "base_branch": "main",
                "merge_strategy": None,
                "max_review_retries": 1,
                "task_runs": [
                    {
                        "task_id": task_id,
                        "status": status,
                        "started_at": "2026-06-12T12:00:10Z",
                        "finished_at": "2026-06-12T12:00:20Z",
                        "adapter": "mock",
                        "verify_command": "pytest",
                        "agent_exit_code": 0,
                        "verify_exit_code": 0,
                        "merge_status": None,
                        "detail": detail,
                        "review": {
                            "verdict": verdict,
                            "severity": "error",
                            "summary": summary,
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
    os.utime(path, (mtime, mtime))
