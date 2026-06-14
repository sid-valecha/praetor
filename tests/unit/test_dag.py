from datetime import UTC, datetime

from praetor.dag import compute_ready_set, detect_cycles, propagate_blocked
from praetor.models import Task, TaskStatus


def make_task(
    task_id: str,
    *,
    status: TaskStatus = TaskStatus.pending,
    depends_on: list[str] | None = None,
) -> Task:
    return Task(
        id=task_id,
        status=status,
        depends_on=depends_on or [],
        created=datetime(2026, 6, 6, 12, 0, tzinfo=UTC),
    )


def test_linear_chain_ready_set_advances_as_dependencies_finish() -> None:
    tasks = [
        make_task("task-a"),
        make_task("task-b", depends_on=["task-a"]),
        make_task("task-c", depends_on=["task-b"]),
    ]

    assert [task.id for task in compute_ready_set(tasks)] == ["task-a"]

    tasks = [
        make_task("task-a", status=TaskStatus.done),
        make_task("task-b", depends_on=["task-a"]),
        make_task("task-c", depends_on=["task-b"]),
    ]

    assert [task.id for task in compute_ready_set(tasks)] == ["task-b"]

    tasks = [
        make_task("task-a", status=TaskStatus.done),
        make_task("task-b", status=TaskStatus.done, depends_on=["task-a"]),
        make_task("task-c", depends_on=["task-b"]),
    ]

    assert [task.id for task in compute_ready_set(tasks)] == ["task-c"]


def test_diamond_ready_set_exposes_siblings_then_join() -> None:
    tasks = [
        make_task("task-a", status=TaskStatus.done),
        make_task("task-b", depends_on=["task-a"]),
        make_task("task-c", depends_on=["task-a"]),
        make_task("task-d", depends_on=["task-b", "task-c"]),
    ]

    assert [task.id for task in compute_ready_set(tasks)] == ["task-b", "task-c"]

    tasks = [
        make_task("task-a", status=TaskStatus.done),
        make_task("task-b", status=TaskStatus.done, depends_on=["task-a"]),
        make_task("task-c", status=TaskStatus.done, depends_on=["task-a"]),
        make_task("task-d", depends_on=["task-b", "task-c"]),
    ]

    assert [task.id for task in compute_ready_set(tasks)] == ["task-d"]


def test_cycle_detection_finds_cycle_and_acyclic_graph_returns_empty() -> None:
    cyclic = [
        make_task("task-a", depends_on=["task-b"]),
        make_task("task-b", depends_on=["task-a"]),
    ]
    acyclic = [
        make_task("task-a"),
        make_task("task-b", depends_on=["task-a"]),
    ]

    assert detect_cycles(cyclic)
    assert set(detect_cycles(cyclic)[0]) == {"task-a", "task-b"}
    assert detect_cycles(acyclic) == []


def test_blocked_cascade_propagates_through_pending_dependents() -> None:
    tasks = [
        make_task("task-a", status=TaskStatus.failed),
        make_task("task-b", depends_on=["task-a"]),
        make_task("task-c", depends_on=["task-b"]),
    ]

    assert propagate_blocked(tasks) == ["task-b", "task-c"]


def test_pending_merge_does_not_satisfy_dependency() -> None:
    tasks = [
        make_task("task-a", status=TaskStatus.pending_merge),
        make_task("task-b", depends_on=["task-a"]),
    ]

    assert compute_ready_set(tasks) == []


def test_merge_failed_does_not_cascade() -> None:
    tasks = [
        make_task("task-a", status=TaskStatus.merge_failed),
        make_task("task-b", depends_on=["task-a"]),
    ]

    assert propagate_blocked(tasks) == []


def test_pending_merge_does_not_cascade() -> None:
    tasks = [
        make_task("task-a", status=TaskStatus.pending_merge),
        make_task("task-b", depends_on=["task-a"]),
    ]

    assert propagate_blocked(tasks) == []


def test_already_blocked_task_is_not_returned_by_blocked_propagation() -> None:
    tasks = [
        make_task("task-a", status=TaskStatus.failed),
        make_task("task-b", status=TaskStatus.blocked, depends_on=["task-a"]),
        make_task("task-c", depends_on=["task-b"]),
    ]

    assert propagate_blocked(tasks) == ["task-c"]


def test_empty_input_returns_empty_results() -> None:
    assert compute_ready_set([]) == []
    assert detect_cycles([]) == []
    assert propagate_blocked([]) == []


def test_mixed_statuses_are_absent_from_ready_set() -> None:
    tasks = [
        make_task("pending"),
        make_task("running", status=TaskStatus.running),
        make_task("pending-merge", status=TaskStatus.pending_merge),
        make_task("merge-failed", status=TaskStatus.merge_failed),
        make_task("done", status=TaskStatus.done),
        make_task("failed", status=TaskStatus.failed),
        make_task("blocked", status=TaskStatus.blocked),
    ]

    assert [task.id for task in compute_ready_set(tasks)] == ["pending"]
