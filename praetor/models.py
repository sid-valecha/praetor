from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
import re
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

MAX_TASK_ID_LENGTH = 100
_TASK_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,99}$")


def validate_task_id(value: str) -> str:
    if not isinstance(value, str):
        msg = "Invalid task id: must be a string"
        raise ValueError(msg)
    if not value:
        msg = "Invalid task id: must not be empty"
        raise ValueError(msg)
    if len(value) > MAX_TASK_ID_LENGTH:
        msg = f"Invalid task id: must be at most {MAX_TASK_ID_LENGTH} characters"
        raise ValueError(msg)
    if (
        value.startswith("-")
        or value.startswith("/")
        or "\\" in value
        or "/" in value
        or ".." in value
        or not _TASK_ID_RE.fullmatch(value)
    ):
        msg = "Invalid task id: use lowercase letters, numbers, hyphens, and underscores only"
        raise ValueError(msg)
    return value


class TaskStatus(StrEnum):
    pending = "pending"
    running = "running"
    pending_merge = "pending_merge"
    merge_failed = "merge_failed"
    review_failed = "review_failed"
    cancelled = "cancelled"
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
    retry: int = 0
    priority: Literal["low", "normal", "high"] = "normal"
    env: dict[str, str] = Field(default_factory=dict)
    context_files: list[str] = Field(default_factory=list)
    created: datetime
    body: str = ""

    @field_validator("id")
    @classmethod
    def id_must_be_safe(cls, value: str) -> str:
        return validate_task_id(value)

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
    tokens_used: int | None = None
    cost_usd: float | None = None


class ReviewFinding(BaseModel):
    severity: Literal["info", "warning", "error"]
    message: str
    file: str | None = None
    line: int | None = None
    recommendation: str | None = None


class ReviewResult(BaseModel):
    verdict: Literal["pass", "needs_revision", "blocked"]
    severity: Literal["info", "warning", "error"]
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)
    reviewer_adapter: str
    reviewer_model: str | None = None
    reviewer_effort: str | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int


class AgentAdapter(Protocol):
    name: str

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult: ...
