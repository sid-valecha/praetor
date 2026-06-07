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
        make_task("A"),
        make_task("B", depends_on=["A"]),
        make_task("C", depends_on=["B"]),
    ]

    assert [task.id for task in compute_ready_set(tasks)] == ["A"]

    tasks = [
        make_task("A", status=TaskStatus.done),
        make_task("B", depends_on=["A"]),
        make_task("C", depends_on=["B"]),
    ]

    assert [task.id for task in compute_ready_set(tasks)] == ["B"]

    tasks = [
        make_task("A", status=TaskStatus.done),
        make_task("B", status=TaskStatus.done, depends_on=["A"]),
        make_task("C", depends_on=["B"]),
    ]

    assert [task.id for task in compute_ready_set(tasks)] == ["C"]


def test_diamond_ready_set_exposes_siblings_then_join() -> None:
    tasks = [
        make_task("A", status=TaskStatus.done),
        make_task("B", depends_on=["A"]),
        make_task("C", depends_on=["A"]),
        make_task("D", depends_on=["B", "C"]),
    ]

    assert [task.id for task in compute_ready_set(tasks)] == ["B", "C"]

    tasks = [
        make_task("A", status=TaskStatus.done),
        make_task("B", status=TaskStatus.done, depends_on=["A"]),
        make_task("C", status=TaskStatus.done, depends_on=["A"]),
        make_task("D", depends_on=["B", "C"]),
    ]

    assert [task.id for task in compute_ready_set(tasks)] == ["D"]


def test_cycle_detection_finds_cycle_and_acyclic_graph_returns_empty() -> None:
    cyclic = [
        make_task("A", depends_on=["B"]),
        make_task("B", depends_on=["A"]),
    ]
    acyclic = [
        make_task("A"),
        make_task("B", depends_on=["A"]),
    ]

    assert detect_cycles(cyclic)
    assert set(detect_cycles(cyclic)[0]) == {"A", "B"}
    assert detect_cycles(acyclic) == []


def test_blocked_cascade_propagates_through_pending_dependents() -> None:
    tasks = [
        make_task("A", status=TaskStatus.failed),
        make_task("B", depends_on=["A"]),
        make_task("C", depends_on=["B"]),
    ]

    assert propagate_blocked(tasks) == ["B", "C"]


def test_already_blocked_task_is_not_returned_by_blocked_propagation() -> None:
    tasks = [
        make_task("A", status=TaskStatus.failed),
        make_task("B", status=TaskStatus.blocked, depends_on=["A"]),
        make_task("C", depends_on=["B"]),
    ]

    assert propagate_blocked(tasks) == ["C"]


def test_empty_input_returns_empty_results() -> None:
    assert compute_ready_set([]) == []
    assert detect_cycles([]) == []
    assert propagate_blocked([]) == []


def test_mixed_statuses_are_absent_from_ready_set() -> None:
    tasks = [
        make_task("pending"),
        make_task("running", status=TaskStatus.running),
        make_task("done", status=TaskStatus.done),
        make_task("failed", status=TaskStatus.failed),
        make_task("blocked", status=TaskStatus.blocked),
    ]

    assert [task.id for task in compute_ready_set(tasks)] == ["pending"]
