from datetime import UTC, datetime
import json
import subprocess
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from praetor.models import AgentAdapter, ReviewFinding, ReviewResult, Task, TaskResult

REVIEW_OUTPUT_INSTRUCTIONS = """
Return only JSON with this exact shape:
{
  "verdict": "pass" | "needs_revision" | "blocked",
  "severity": "info" | "warning" | "error",
  "summary": "one concise sentence",
  "findings": [
    {
      "severity": "info" | "warning" | "error",
      "file": "optional/path.py",
      "line": 123,
      "message": "specific finding",
      "recommendation": "optional concrete fix"
    }
  ]
}
"""


def run_task_review(
    task: Task,
    adapter: AgentAdapter,
    *,
    cwd: Path,
    agent_result: TaskResult,
    verify_output: str,
) -> ReviewResult:
    started_at = datetime.now(UTC)
    start = perf_counter()
    prompt = render_review_prompt(
        task,
        diff=_worktree_diff(cwd),
        agent_output=f"{agent_result.stdout}{agent_result.stderr}",
        verify_output=verify_output,
        review_mode=task.review,
    )
    result = adapter.exec(prompt, cwd=cwd)
    finished_at = datetime.now(UTC)
    duration_ms = int((perf_counter() - start) * 1000)

    if result.exit_code != 0:
        return ReviewResult(
            verdict="needs_revision",
            severity="error",
            summary="Reviewer adapter exited nonzero.",
            findings=[
                ReviewFinding(
                    severity="error",
                    message=(result.stderr or result.stdout or "reviewer exited nonzero").strip(),
                    recommendation="Inspect reviewer output and rerun the task review.",
                )
            ],
            reviewer_adapter=adapter.name,
            reviewer_model=getattr(adapter, "model", None),
            reviewer_effort=getattr(adapter, "effort", None),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )

    try:
        payload = _extract_json_object(result.stdout)
        parsed = ReviewResult.model_validate(
            {
                **payload,
                "reviewer_adapter": adapter.name,
                "reviewer_model": getattr(adapter, "model", None),
                "reviewer_effort": getattr(adapter, "effort", None),
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_ms": duration_ms,
            }
        )
    except (ValueError, TypeError, ValidationError) as exc:
        parsed = ReviewResult(
            verdict="needs_revision",
            severity="error",
            summary="Reviewer output was not valid structured JSON.",
            findings=[
                ReviewFinding(
                    severity="error",
                    message=str(exc),
                    recommendation="Rerun with a reviewer that emits the required JSON schema.",
                )
            ],
            reviewer_adapter=adapter.name,
            reviewer_model=getattr(adapter, "model", None),
            reviewer_effort=getattr(adapter, "effort", None),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )

    return parsed


def render_review_prompt(
    task: Task,
    *,
    diff: str,
    agent_output: str,
    verify_output: str,
    review_mode: str,
) -> str:
    return "\n\n".join(
        [
            "You are the Praetor task reviewer.",
            f"Review mode: {review_mode}",
            "Your job is to find problems, not validate the implementer.",
            "Pass only when the diff satisfies the task and the verification is meaningful.",
            REVIEW_OUTPUT_INSTRUCTIONS.strip(),
            f"Task id: {task.id}",
            "Task body:",
            task.body.strip() or "(empty)",
            f"Verify command: {task.verify or '(none)'}",
            "Agent output:",
            agent_output.strip() or "(empty)",
            "Verify output:",
            verify_output.strip() or "(empty)",
            "Diff and untracked files:",
            diff.strip() or "(no diff)",
        ]
    )


def format_review_for_log(review: ReviewResult) -> str:
    lines = [
        "",
        "Praetor review:",
        f"verdict: {review.verdict}",
        f"severity: {review.severity}",
        f"summary: {review.summary}",
    ]
    for finding in review.findings:
        location = ""
        if finding.file is not None:
            location = finding.file
            if finding.line is not None:
                location = f"{location}:{finding.line}"
        prefix = f"- {finding.severity}"
        if location:
            prefix = f"{prefix} {location}"
        lines.append(f"{prefix}: {finding.message}")
        if finding.recommendation:
            lines.append(f"  recommendation: {finding.recommendation}")
    return "\n".join(lines) + "\n"


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped.removeprefix("json").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        msg = "reviewer output did not contain a JSON object"
        raise ValueError(msg)
    value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        msg = "reviewer JSON output must be an object"
        raise TypeError(msg)
    return value


def _worktree_diff(cwd: Path) -> str:
    diff = _git(["diff", "--no-ext-diff", "--"], cwd)
    untracked = _git(["ls-files", "--others", "--exclude-standard"], cwd)
    if not untracked:
        return diff

    sections = [diff] if diff else []
    for relative_path in untracked.splitlines():
        path = cwd / relative_path
        if not path.is_file():
            continue
        try:
            content = path.read_text()
        except UnicodeDecodeError:
            content = "(binary or non-UTF-8 file)"
        sections.append(f"Untracked file: {relative_path}\n{content}")
    return "\n\n".join(sections)


def _git(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout
