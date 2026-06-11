from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from praetor.events import RunnerEvent


def test_runner_event_default_timestamp() -> None:
    before = datetime.now(UTC) - timedelta(seconds=1)
    event = RunnerEvent(type="drain_started")
    after = datetime.now(UTC) + timedelta(seconds=1)

    assert before <= event.timestamp <= after


def test_runner_event_frozen() -> None:
    event = RunnerEvent(type="drain_started")

    with pytest.raises(FrozenInstanceError):
        event.type = "drain_finished"  # type: ignore[misc]
