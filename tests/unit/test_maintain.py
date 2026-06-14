import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from praetor.frontmatter import dump_task
from praetor.maintain import MaintainScan, scan
from praetor.models import Task, TaskStatus
from praetor.state import init_workspace


def test_scan_returns_empty_items_when_no_tasks(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    result = scan(tmp_path)

    assert isinstance(result, MaintainScan)
    assert result.items == []
    assert result.latest_run is None
    assert result.repo_root == str(tmp_path)


def test_ready_pending_task_with_verify_is_autonomous(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("ready-a", TaskStatus.pending, verify="pytest -q"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.source == "task:ready-a"
    assert item.url is None
    assert item.classification == "autonomous"
    assert "pytest -q" in item.proof
    assert item.blocker is None
    assert "praetor run" in item.next_action


def test_ready_pending_task_without_verify_is_needs_owner(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("ready-no-verify", TaskStatus.pending, verify=None))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "needs_owner"
    assert item.blocker is not None
    assert "verify" in item.blocker.lower()


def test_pending_task_with_unresolved_deps_is_defer(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("dep", TaskStatus.pending, verify="pytest"))
    _write_task(
        tmp_path,
        _make_task(
            "waiter",
            TaskStatus.pending,
            verify="pytest",
            offset=1,
            depends_on=["dep"],
        ),
    )

    result = scan(tmp_path)

    items = {item.source: item for item in result.items}
    waiter = items["task:waiter"]
    assert waiter.classification == "defer"
    assert waiter.blocker is not None
    assert "dep" in waiter.blocker


def test_review_failed_is_needs_owner_with_review_summary(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("rev-a", TaskStatus.review_failed, verify="pytest"))
    _write_run_with_review(tmp_path, "run-rev", "rev-a")

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "needs_owner"
    assert item.blocker is not None
    assert "Unsafe validation change." in item.blocker
    assert "praetor reset" in item.next_action


def test_merge_failed_is_needs_owner_with_merge_next_action(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("mf", TaskStatus.merge_failed, verify="pytest"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "needs_owner"
    assert "praetor merge" in item.next_action


def test_failed_is_needs_owner_with_reset_next_action(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("boom", TaskStatus.failed, verify="pytest"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "needs_owner"
    assert "praetor reset" in item.next_action


def test_blocked_is_defer(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("blk", TaskStatus.blocked, verify="pytest"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "defer"


def test_cancelled_is_defer(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("cx", TaskStatus.cancelled, verify="pytest"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "defer"


def test_pending_merge_is_needs_owner_with_merge_next_action(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("pm", TaskStatus.pending_merge, verify="pytest"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "needs_owner"
    assert "praetor merge" in item.next_action


def test_running_is_needs_owner_with_cautious_reset_guidance(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("stuck", TaskStatus.running, verify="pytest"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "needs_owner"
    assert "praetor reset" in item.next_action
    assert "only if no runner is active" in item.next_action
    assert "no liveness check" in item.blocker.lower()


def test_done_tasks_are_excluded(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("d1", TaskStatus.done, verify="pytest"))
    _write_task(
        tmp_path,
        _make_task("p1", TaskStatus.pending, verify="pytest", offset=1),
    )

    result = scan(tmp_path)

    sources = {item.source for item in result.items}
    assert sources == {"task:p1"}


def test_latest_run_metadata_is_populated(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("rev-a", TaskStatus.review_failed, verify="pytest"))
    _write_run_with_review(tmp_path, "run-rev", "rev-a")

    result = scan(tmp_path)

    assert result.latest_run is not None
    assert result.latest_run.id == "run-rev"
    assert result.latest_run.status == "completed"


def test_scan_is_read_only(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("ready-a", TaskStatus.pending, verify="pytest"))
    _write_task(tmp_path, _make_task("rev-a", TaskStatus.review_failed, verify="pytest", offset=1))
    _write_run_with_review(tmp_path, "run-rev", "rev-a")

    praetor_dir = tmp_path / ".praetor"
    before = _snapshot_tree(praetor_dir)

    scan(tmp_path)

    after = _snapshot_tree(praetor_dir)
    assert before == after


def _snapshot_tree(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            snapshot[str(path.relative_to(root))] = path.read_text()
    return snapshot


def _make_task(
    task_id: str,
    status: TaskStatus,
    *,
    verify: str | None = "pytest",
    offset: int = 0,
    depends_on: list[str] | None = None,
) -> Task:
    return Task(
        id=task_id,
        status=status,
        depends_on=depends_on or [],
        verify=verify,
        created=datetime(2026, 6, 14, 12, 0, tzinfo=UTC) + timedelta(minutes=offset),
        body=f"# {task_id}\n",
    )


def _write_task(repo_root: Path, task: Task) -> None:
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")


def _write_run_with_review(repo_root: Path, run_id: str, task_id: str) -> None:
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
