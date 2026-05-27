from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from praetor.models import Task, TaskResult, TaskStatus


def test_task_status_values() -> None:
    assert [status.value for status in TaskStatus] == [
        "pending",
        "ready",
        "running",
        "done",
        "failed",
        "blocked",
    ]


def test_task_accepts_minimal_valid_input() -> None:
    created = datetime(2026, 5, 23, 14, 22, tzinfo=UTC)

    task = Task.model_validate({"id": "001-core-schema", "created": created})

    assert task.id == "001-core-schema"
    assert task.status is TaskStatus.pending
    assert task.depends_on == []
    assert task.parallel_ok is False
    assert task.agent == "claude"
    assert task.verify is None
    assert task.review == "off"
    assert task.created == created
    assert task.body == ""


def test_task_rejects_invalid_status() -> None:
    with pytest.raises(ValidationError):
        Task.model_validate(
            {
                "id": "001-core-schema",
                "status": "nonsense",
                "created": "2026-05-23T14:22:00Z",
            }
        )


def test_task_result_round_trips_through_model_dump() -> None:
    result = TaskResult(
        exit_code=1,
        stdout="out",
        stderr="err",
        duration_ms=123,
        diff="diff --git a/file b/file",
    )

    assert TaskResult.model_validate(result.model_dump()) == result
