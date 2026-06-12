from pathlib import Path
from typing import Any

from pydantic import ValidationError

from praetor.models import Task, TaskStatus
from praetor.run_history import RunRecord, load_run


def latest_review_failure(repo_root: Path, task_id: str) -> dict[str, Any] | None:
    """Return the newest reviewer rejection for task_id from run history."""
    runs_dir = repo_root / ".praetor" / "runs"
    if not runs_dir.exists():
        return None

    runs = sorted(_valid_runs(runs_dir), key=_run_sort_key, reverse=True)
    for run in runs:
        for task_run in reversed(run.task_runs):
            if task_run.task_id != task_id or task_run.review is None:
                continue
            if task_run.review.verdict != "needs_revision":
                return None
            review = task_run.review.model_dump(mode="json")
            return {
                "run_id": run.id,
                "detail": task_run.detail,
                "verdict": review["verdict"],
                "severity": review["severity"],
                "summary": review["summary"],
                "findings": review["findings"],
                "reviewer_adapter": review["reviewer_adapter"],
                "started_at": review["started_at"],
                "finished_at": review["finished_at"],
                "duration_ms": review["duration_ms"],
            }
    return None


def format_review_failure_for_prompt(review_failure: dict[str, Any]) -> str:
    """Format a reviewer rejection as concise executor retry context."""
    lines = ["Latest reviewer feedback from the previous failed review:"]
    lines.append(f"- verdict: {review_failure['verdict']}")
    lines.append(f"- severity: {review_failure['severity']}")
    lines.append(f"- summary: {review_failure['summary']}")

    findings = review_failure.get("findings") or []
    if findings:
        lines.append("- findings:")
        for index, finding in enumerate(findings, start=1):
            location = _format_finding_location(finding)
            prefix = f"  {index}. [{finding.get('severity', 'info')}]"
            if location:
                prefix = f"{prefix} {location}"
            lines.append(f"{prefix}: {finding.get('message', '')}")
            recommendation = finding.get("recommendation")
            if recommendation:
                lines.append(f"     recommendation: {recommendation}")
    else:
        lines.append("- findings: none")

    return "\n".join(lines)


def _format_finding_location(finding: dict[str, Any]) -> str:
    file_name = finding.get("file")
    line = finding.get("line")
    if file_name and line:
        return f"{file_name}:{line}"
    if file_name:
        return str(file_name)
    return ""


def _valid_runs(runs_dir: Path) -> list[RunRecord]:
    runs: list[RunRecord] = []
    for path in runs_dir.glob("*.json"):
        try:
            runs.append(load_run(path))
        except (OSError, ValidationError, ValueError):
            continue
    return runs


def _run_sort_key(run: RunRecord) -> tuple[str, str, str]:
    timestamp = run.finished_at or run.started_at
    return (timestamp.isoformat(), run.started_at.isoformat(), run.id)


def review_failure_for_task(repo_root: Path, task: Task) -> dict[str, Any] | None:
    if task.status is not TaskStatus.review_failed:
        return None
    return latest_review_failure(repo_root, task.id)


def waiting_on(repo_root: Path, task: Task, tasks: list[Task]) -> list[dict[str, Any]]:
    if task.status is not TaskStatus.pending:
        return []

    tasks_by_id = {candidate.id: candidate for candidate in tasks}
    waits: list[dict[str, Any]] = []
    for dependency_id in task.depends_on:
        dependency = tasks_by_id.get(dependency_id)
        if dependency is None:
            waits.append(
                {
                    "task_id": dependency_id,
                    "status": "missing",
                    "reason": "dependency_missing",
                    "review_summary": None,
                }
            )
            continue
        if dependency.status is TaskStatus.done:
            continue

        review_summary = None
        if dependency.status is TaskStatus.review_failed:
            review_failure = review_failure_for_task(repo_root, dependency)
            if review_failure is not None:
                review_summary = review_failure["summary"]

        waits.append(
            {
                "task_id": dependency.id,
                "status": dependency.status.value,
                "reason": f"dependency_{dependency.status.value}",
                "review_summary": review_summary,
            }
        )
    return waits
