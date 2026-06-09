from pathlib import Path

import pytest
from pydantic import ValidationError

from praetor.frontmatter import dump_task, parse_task
from praetor.models import Task, TaskStatus


def write_sample_task(path: Path) -> None:
    path.write_text(
        """---
id: 003-stripe-webhook
status: pending
depends_on: [002-stripe-keys]
parallel_ok: true
agent: claude
verify: pytest tests/billing/test_webhook.py
review: off
created: 2026-05-23T14:22:00Z
---

# Implement Stripe webhook handler

## What to do
[prompt body - the actual task description for the agent]

## How to verify
[explicit success criteria - checked by `verify` command]

## Proof when complete
[what artifacts/output prove this is done]
"""
    )


def test_parse_dump_parse_round_trip_preserves_fields(tmp_path: Path) -> None:
    path = tmp_path / "003-stripe-webhook.md"
    write_sample_task(path)

    task = parse_task(path)
    dump_task(task, path)
    reparsed = parse_task(path)

    assert reparsed == task
    assert reparsed.id == "003-stripe-webhook"
    assert reparsed.status is TaskStatus.pending
    assert reparsed.depends_on == ["002-stripe-keys"]
    assert reparsed.parallel_ok is True
    assert reparsed.agent == "claude"
    assert reparsed.verify == "pytest tests/billing/test_webhook.py"
    assert reparsed.review == "off"
    assert reparsed.body.startswith("# Implement Stripe webhook handler")


def test_dumped_task_is_byte_identical_after_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "003-stripe-webhook.md"
    task = Task.model_validate(
        {
            "id": "003-stripe-webhook",
            "status": "pending",
            "depends_on": ["002-stripe-keys"],
            "parallel_ok": True,
            "agent": "claude",
            "verify": "pytest tests/billing/test_webhook.py",
            "review": "off",
            "created": "2026-05-23T14:22:00Z",
            "body": "# Implement Stripe webhook handler\n",
        }
    )

    dump_task(task, path)
    first_dump = path.read_bytes()
    dump_task(parse_task(path), path)

    assert path.read_bytes() == first_dump


@pytest.mark.parametrize(
    ("parallel_ok", "expected_line"),
    [(True, "parallel_ok: true"), (False, "parallel_ok: false")],
)
def test_parallel_ok_round_trips_and_serializes_as_yaml_bool(
    tmp_path: Path, parallel_ok: bool, expected_line: str
) -> None:
    path = tmp_path / "parallel-ok.md"
    task = Task.model_validate(
        {
            "id": "parallel-ok",
            "status": "pending",
            "depends_on": [],
            "parallel_ok": parallel_ok,
            "agent": "claude",
            "verify": None,
            "review": "off",
            "created": "2026-05-23T14:22:00Z",
            "body": "# Parallel flag\n",
        }
    )

    dump_task(task, path)

    assert expected_line in path.read_text()
    assert parse_task(path).parallel_ok is parallel_ok


def test_invalid_frontmatter_status_raises_validation_error(tmp_path: Path) -> None:
    path = tmp_path / "bad-status.md"
    path.write_text(
        """---
id: bad-status
status: nonsense
created: 2026-05-23T14:22:00Z
---

Body
"""
    )

    with pytest.raises(ValidationError):
        parse_task(path)


def test_missing_required_id_raises_validation_error(tmp_path: Path) -> None:
    path = tmp_path / "missing-id.md"
    path.write_text(
        """---
status: pending
created: 2026-05-23T14:22:00Z
---

Body
"""
    )

    with pytest.raises(ValidationError):
        parse_task(path)
