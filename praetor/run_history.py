from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from praetor.config import DEFAULT_MAX_REVIEW_RETRIES
from praetor.models import ReviewResult


class TaskRunRecord(BaseModel):
    task_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    adapter: str | None = None
    verify_command: str | None = None
    agent_exit_code: int | None = None
    verify_exit_code: int | None = None
    review: ReviewResult | None = None
    merge_status: str | None = None
    detail: str | None = None


class RunRecord(BaseModel):
    id: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    max_parallel: int
    base_branch: str
    merge_strategy: str | None = None
    max_review_retries: int = DEFAULT_MAX_REVIEW_RETRIES
    task_runs: list[TaskRunRecord] = Field(default_factory=list)


class RunRecorder:
    def __init__(
        self,
        repo_root: Path,
        *,
        max_parallel: int,
        base_branch: str,
        merge_strategy: str | None,
        max_review_retries: int,
    ) -> None:
        started_at = _now()
        run_id = f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        self.path = repo_root / ".praetor" / "runs" / f"{run_id}.json"
        self.record = RunRecord(
            id=run_id,
            status="running",
            started_at=started_at,
            max_parallel=max_parallel,
            base_branch=base_branch,
            merge_strategy=merge_strategy,
            max_review_retries=max_review_retries,
        )
        self._write()

    @property
    def run_id(self) -> str:
        return self.record.id

    def start_task(
        self,
        task_id: str,
        *,
        adapter: str,
        verify_command: str | None,
    ) -> None:
        self.record.task_runs.append(
            TaskRunRecord(
                task_id=task_id,
                status="running",
                started_at=_now(),
                adapter=adapter,
                verify_command=verify_command,
            )
        )
        self._write()

    def finish_task(
        self,
        task_id: str,
        *,
        status: str,
        detail: str | None = None,
        agent_exit_code: int | None = None,
        verify_exit_code: int | None = None,
        review: ReviewResult | None = None,
        merge_status: str | None = None,
    ) -> None:
        task_run = self._latest_task(task_id)
        task_run.status = status
        task_run.finished_at = _now()
        task_run.detail = detail
        if agent_exit_code is not None:
            task_run.agent_exit_code = agent_exit_code
        if verify_exit_code is not None:
            task_run.verify_exit_code = verify_exit_code
        if review is not None:
            task_run.review = review
        if merge_status is not None:
            task_run.merge_status = merge_status
        self._write()

    def record_review(self, task_id: str, review: ReviewResult) -> None:
        task_run = self._latest_task(task_id)
        task_run.review = review
        self._write()

    def finish_run(self, status: str) -> None:
        self.record.status = status
        self.record.finished_at = _now()
        self._write()

    def _latest_task(self, task_id: str) -> TaskRunRecord:
        for task_run in reversed(self.record.task_runs):
            if task_run.task_id == task_id:
                return task_run
        msg = f"task run not found: {task_id}"
        raise KeyError(msg)

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = self.record.model_dump_json(indent=2)
        tmp_path = self.path.with_name(f".{self.path.name}.tmp")
        tmp_path.write_text(f"{content}\n")
        tmp_path.replace(self.path)


def load_run(path: Path) -> RunRecord:
    return RunRecord.model_validate_json(path.read_text())


def latest_run(repo_root: Path) -> RunRecord | None:
    runs_dir = repo_root / ".praetor" / "runs"
    if not runs_dir.exists():
        return None
    paths = sorted(runs_dir.glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
    if not paths:
        return None
    return load_run(paths[-1])


def _now() -> datetime:
    return datetime.now(UTC)
