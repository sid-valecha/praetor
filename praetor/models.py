from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class TaskStatus(StrEnum):
    pending = "pending"
    running = "running"
    pending_merge = "pending_merge"
    merge_failed = "merge_failed"
    done = "done"
    failed = "failed"
    blocked = "blocked"


class Task(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: str
    status: TaskStatus = TaskStatus.pending
    depends_on: list[str] = Field(default_factory=list)
    parallel_ok: bool = True
    agent: str = "claude"
    verify: str | None = None
    review: str = "off"
    merge_strategy: Literal["auto", "manual"] = "manual"
    created: datetime
    body: str = ""

    @field_validator("created")
    @classmethod
    def created_must_be_timezone_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "created must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)

    @field_serializer("created")
    def serialize_created(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @field_validator("review")
    @classmethod
    def review_must_be_known_value(cls, value: str) -> str:
        if value not in {"off", "lenient", "strict"}:
            msg = "review must be one of: off, lenient, strict"
            raise ValueError(msg)
        return value


class TaskResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    diff: str | None = None


class AgentAdapter(Protocol):
    name: str

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult: ...
