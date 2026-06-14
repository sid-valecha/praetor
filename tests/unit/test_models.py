from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from praetor.models import Task, TaskResult, TaskStatus


def test_task_status_values() -> None:
    assert [status.value for status in TaskStatus] == [
        "pending",
        "running",
        "pending_merge",
        "merge_failed",
        "review_failed",
        "cancelled",
        "done",
        "failed",
        "blocked",
    ]
    assert TaskStatus.cancelled.value == "cancelled"


def test_task_accepts_minimal_valid_input() -> None:
    created = datetime(2026, 5, 23, 14, 22, tzinfo=UTC)

    task = Task.model_validate({"id": "001-core-schema", "created": created})

    assert task.id == "001-core-schema"
    assert task.status is TaskStatus.pending
    assert task.depends_on == []
    assert task.parallel_ok is True
    assert task.agent == "claude"
    assert task.verify is None
    assert task.review == "off"
    assert task.merge_strategy == "manual"
    assert task.retry == 0
    assert task.priority == "normal"
    assert task.env == {}
    assert task.context_files == []
    assert task.created == created
    assert task.body == ""


def test_task_parallel_ok_defaults_to_true() -> None:
    created = datetime(2026, 5, 23, 14, 22, tzinfo=UTC)

    task = Task.model_validate({"id": "parallel-default", "created": created})

    assert task.parallel_ok is True


def test_task_merge_strategy_defaults_to_manual() -> None:
    created = datetime(2026, 5, 23, 14, 22, tzinfo=UTC)

    task = Task.model_validate({"id": "merge-default", "created": created})

    assert task.merge_strategy == "manual"


def test_task_accepts_forward_compat_runtime_fields() -> None:
    created = datetime(2026, 5, 23, 14, 22, tzinfo=UTC)
    task = Task(
        id="forward-compat-fields",
        created=created,
        retry=3,
        priority="high",
        env={"FOO": "bar"},
        context_files=["a.py"],
    )

    dumped = task.model_dump()

    assert dumped["retry"] == 3
    assert dumped["priority"] == "high"
    assert dumped["env"] == {"FOO": "bar"}
    assert dumped["context_files"] == ["a.py"]
    assert Task.model_validate(dumped) == task


def test_task_rejects_invalid_priority() -> None:
    with pytest.raises(ValidationError):
        Task(
            id="invalid-priority",
            created=datetime(2026, 5, 23, 14, 22, tzinfo=UTC),
            priority="invalid",
        )


@pytest.mark.parametrize(
    "task_id",
    [
        "task/a",
        "task\\a",
        "../task-a",
        "/task-a",
        "-task-a",
        "task..a",
        "a" * 101,
    ],
)
def test_task_rejects_unsafe_ids(task_id: str) -> None:
    with pytest.raises(ValidationError):
        Task(id=task_id, created=datetime(2026, 5, 23, 14, 22, tzinfo=UTC))


def test_task_accepts_legacy_uppercase_ids() -> None:
    task = Task(id="Task-A", created=datetime(2026, 5, 23, 14, 22, tzinfo=UTC))

    assert task.id == "Task-A"


def test_task_mutable_defaults_are_not_shared() -> None:
    created = datetime(2026, 5, 23, 14, 22, tzinfo=UTC)
    first = Task(id="first", created=created)
    second = Task(id="second", created=created)

    first.env["FOO"] = "bar"
    first.context_files.append("a.py")

    assert second.env == {}
    assert second.context_files == []
    assert first.env is not second.env
    assert first.context_files is not second.context_files


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

    assert result.tokens_used is None
    assert result.cost_usd is None
    assert TaskResult.model_validate(result.model_dump()) == result
