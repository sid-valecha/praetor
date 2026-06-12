from pathlib import Path

from praetor.config import DEFAULT_MAX_REVIEW_RETRIES
from praetor.run_history import RunRecorder, latest_run, load_run
from praetor.state import init_workspace


def test_run_recorder_writes_and_loads_run(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    recorder = RunRecorder(
        tmp_path,
        max_parallel=2,
        base_branch="main",
        merge_strategy="manual",
        max_review_retries=1,
    )

    recorder.start_task("task-a", adapter="mock", verify_command="true")
    recorder.finish_task(
        "task-a",
        status="done",
        agent_exit_code=0,
        verify_exit_code=0,
    )
    recorder.finish_run("completed")

    loaded = load_run(recorder.path)

    assert loaded.id == recorder.run_id
    assert loaded.status == "completed"
    assert loaded.task_runs[0].task_id == "task-a"
    assert loaded.task_runs[0].status == "done"
    assert loaded.task_runs[0].agent_exit_code == 0
    assert loaded.task_runs[0].verify_exit_code == 0
    assert loaded.max_review_retries == 1


def test_run_recorder_writes_executor_model_metadata(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    recorder = RunRecorder(
        tmp_path,
        max_parallel=1,
        base_branch="main",
        merge_strategy=None,
        max_review_retries=1,
    )

    recorder.start_task(
        "task-a",
        adapter="claude",
        verify_command="true",
        executor_model="haiku",
        executor_effort="low",
    )
    recorder.finish_task("task-a", status="done")

    loaded = load_run(recorder.path)

    assert loaded.task_runs[0].executor_model == "haiku"
    assert loaded.task_runs[0].executor_effort == "low"


def test_latest_run_returns_newest_run(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    RunRecorder(
        tmp_path,
        max_parallel=1,
        base_branch="main",
        merge_strategy=None,
        max_review_retries=1,
    )
    second = RunRecorder(
        tmp_path,
        max_parallel=1,
        base_branch="main",
        merge_strategy=None,
        max_review_retries=1,
    )

    assert latest_run(tmp_path).id == second.run_id


def test_load_run_defaults_max_review_retries_for_older_records(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    path.write_text(
        """
{
  "id": "older-run",
  "status": "completed",
  "started_at": "2026-06-12T12:00:00Z",
  "finished_at": "2026-06-12T12:01:00Z",
  "max_parallel": 1,
  "base_branch": "main",
  "merge_strategy": null,
  "task_runs": []
}
""".strip()
    )

    loaded = load_run(path)

    assert loaded.max_review_retries == DEFAULT_MAX_REVIEW_RETRIES
    assert loaded.task_runs == []


def test_load_run_defaults_executor_metadata_for_older_task_records(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    path.write_text(
        """
{
  "id": "older-run",
  "status": "completed",
  "started_at": "2026-06-12T12:00:00Z",
  "finished_at": "2026-06-12T12:01:00Z",
  "max_parallel": 1,
  "base_branch": "main",
  "merge_strategy": null,
  "task_runs": [
    {
      "task_id": "task-a",
      "status": "done",
      "started_at": "2026-06-12T12:00:10Z",
      "finished_at": "2026-06-12T12:00:20Z",
      "adapter": "mock",
      "verify_command": "true"
    }
  ]
}
""".strip()
    )

    loaded = load_run(path)

    assert loaded.task_runs[0].executor_model is None
    assert loaded.task_runs[0].executor_effort is None
