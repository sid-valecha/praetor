from pathlib import Path

from rich.console import Console

from praetor.commands import require_workspace
from praetor.models import validate_task_id
from praetor.recovery import review_failure_for_task
from praetor.state import get_task

console = Console()


def logs_command(task_id: str) -> None:
    repo_root = Path.cwd()
    require_workspace(repo_root)

    try:
        safe_task_id = validate_task_id(task_id)
        task = get_task(repo_root, safe_task_id)
    except (KeyError, ValueError):
        console.print(f"No log found for {task_id}")
        return

    log_path = _task_log_path(repo_root, safe_task_id)

    review_failure = review_failure_for_task(repo_root, task)
    if review_failure is not None:
        console.print(_format_review_failure(review_failure), end="")

    if not log_path.exists():
        console.print(f"No log found for {task_id}")
        return

    console.print(log_path.read_text(), end="")


def _task_log_path(repo_root: Path, task_id: str) -> Path:
    logs_dir = (repo_root / ".praetor" / "logs").resolve()
    log_path = (logs_dir / f"{validate_task_id(task_id)}.log").resolve()
    if log_path.parent != logs_dir:
        msg = f"Invalid task log path for {task_id}"
        raise ValueError(msg)
    return log_path


def _format_review_failure(review_failure: dict[str, object]) -> str:
    lines = [
        "Review failure:",
        f"run_id: {review_failure['run_id']}",
        f"severity: {review_failure['severity']}",
        f"summary: {review_failure['summary']}",
    ]
    findings = review_failure.get("findings")
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = finding.get("severity")
            message = finding.get("message")
            file = finding.get("file")
            line = finding.get("line")
            location = ""
            if isinstance(file, str):
                location = file
                if isinstance(line, int):
                    location = f"{location}:{line}"
            prefix = f"- {severity}"
            if location:
                prefix = f"{prefix} {location}"
            lines.append(f"{prefix}: {message}")
            recommendation = finding.get("recommendation")
            if isinstance(recommendation, str) and recommendation:
                lines.append(f"  recommendation: {recommendation}")
    return "\n".join(lines) + "\n\n"
