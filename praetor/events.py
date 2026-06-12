from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Literal

EventType = Literal[
    "drain_started",
    "task_dispatched",
    "task_completed",
    "task_failed",
    "task_review_started",
    "task_review_succeeded",
    "task_review_failed",
    "merge_started",
    "merge_succeeded",
    "merge_failed",
    "task_pending_merge",
    "drain_finished",
]


@dataclass(frozen=True)
class RunnerEvent:
    type: EventType
    task_id: str | None = None
    detail: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


EventCallback = Callable[[RunnerEvent], None]
