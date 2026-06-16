import json
from pathlib import Path
from typing import Annotated

from rich.console import Console
import typer
from typer._click.exceptions import ClickException

from praetor.adapters import get_adapter, resolve_reviewer_adapter
from praetor.commands import raise_usage_error, require_workspace
from praetor.maintain import (
    MaintainItem,
    MaintainScan,
    proposals_from_scan,
    respond_to_review_scan,
    scan,
    write_proposals_to_tasks,
)
from praetor.runner import drain_queue
from praetor.state import list_tasks

console = Console()

_CLASSIFICATION_HEADINGS: dict[str, str] = {
    "autonomous": "Autonomous",
    "needs_owner": "Needs owner",
    "defer": "Defer",
}

_CLASSIFICATION_ORDER: list[str] = ["autonomous", "needs_owner", "defer"]


def maintain_command(
    once: Annotated[
        bool,
        typer.Option("--once", help="Run a single read-only maintainer scan."),
    ] = False,
    github: Annotated[
        bool,
        typer.Option("--github", help="Include read-only GitHub issue, PR, and CI intake."),
    ] = False,
    github_pr: Annotated[
        int | None,
        typer.Option("--github-pr", help="Inspect one GitHub pull request number."),
    ] = None,
    github_issue: Annotated[
        int | None,
        typer.Option("--github-issue", help="Inspect one GitHub issue number."),
    ] = None,
    propose_tasks: Annotated[
        bool,
        typer.Option(
            "--propose-tasks",
            help="Convert applicable GitHub findings into task-shaped proposals.",
        ),
    ] = False,
    respond_to_review: Annotated[
        bool,
        typer.Option(
            "--respond-to-review",
            help="Run one bounded focused PR review-response planning cycle.",
        ),
    ] = False,
    max_cycles: Annotated[
        int,
        typer.Option(
            "--max-cycles",
            help="Maximum review-response cycles for future authorized drains.",
        ),
    ] = 3,
    run_repairs: Annotated[
        bool,
        typer.Option(
            "--run-repairs",
            help="Run generated repair tasks through the local executor, verify, and reviewer gates.",
        ),
    ] = False,
    adapter: Annotated[
        str,
        typer.Option("--adapter", help="Agent adapter name for --run-repairs."),
    ] = "claude",
    model: Annotated[
        str | None,
        typer.Option("--model", help="Model name for the repair executor adapter."),
    ] = None,
    effort: Annotated[
        str | None,
        typer.Option("--effort", help="Effort level for the repair executor adapter."),
    ] = None,
    max_review_retries: Annotated[
        int | None,
        typer.Option(
            "--max-review-retries",
            help="Maximum automatic reviewer-rejection retries for repair tasks.",
        ),
    ] = None,
    reviewer_adapter: Annotated[
        str | None,
        typer.Option("--reviewer-adapter", help="Reviewer adapter for repair tasks."),
    ] = None,
    reviewer_model: Annotated[
        str | None,
        typer.Option("--reviewer-model", help="Reviewer model for repair tasks."),
    ] = None,
    reviewer_effort: Annotated[
        str | None,
        typer.Option("--reviewer-effort", help="Reviewer effort for repair tasks."),
    ] = None,
    write_tasks: Annotated[
        bool,
        typer.Option(
            "--write-tasks",
            help="Create .praetor task files for deterministic proposals.",
        ),
    ] = False,
    task_verify: Annotated[
        str | None,
        typer.Option(
            "--task-verify",
            help="Explicit verify command used when writing proposal tasks.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the scan result as JSON."),
    ] = False,
) -> None:
    repo_root = Path.cwd()
    require_workspace(repo_root)

    if not once and not respond_to_review:
        raise_usage_error("praetor maintain currently requires --once.")
    if github_pr is not None and github_issue is not None:
        raise_usage_error("Choose only one focused GitHub target: --github-pr or --github-issue.")
    if respond_to_review and github_pr is None:
        raise_usage_error("--respond-to-review requires --github-pr.")
    if max_cycles < 1:
        raise_usage_error("--max-cycles must be at least 1.")
    if run_repairs and not respond_to_review:
        raise_usage_error("--run-repairs requires --respond-to-review.")
    if run_repairs and not write_tasks:
        raise_usage_error("--run-repairs requires --write-tasks.")
    if run_repairs and task_verify is None:
        raise_usage_error("--run-repairs requires --task-verify.")
    if max_review_retries is not None and max_review_retries < 0:
        raise_usage_error("--max-review-retries must be >= 0.")
    if write_tasks and not (propose_tasks or respond_to_review):
        raise_usage_error("--write-tasks requires --propose-tasks or --respond-to-review.")
    if task_verify is not None and not write_tasks:
        raise_usage_error("--task-verify requires --write-tasks.")

    include_github = github or github_pr is not None or github_issue is not None
    result = scan(
        repo_root,
        include_github=include_github,
        github_pr=github_pr,
        github_issue=github_issue,
    )
    written_task_ids: list[str] = []
    skipped_task_ids: list[str] = []
    repair_task_ids: list[str] = []
    drain_started = False

    if respond_to_review:
        result = respond_to_review_scan(result)
        if write_tasks:
            written_task_ids, skipped_task_ids = write_proposals_to_tasks(
                repo_root,
                result.items,
                task_verify=task_verify,
            )
            repair_task_ids = _dedupe_task_ids(written_task_ids + skipped_task_ids)
            if run_repairs:
                repair_task_ids = _repair_task_ids_for_drain(
                    repo_root,
                    written_task_ids=written_task_ids,
                    skipped_task_ids=skipped_task_ids,
                    task_verify=task_verify,
                )
        if run_repairs and repair_task_ids:
            try:
                agent_adapter = get_adapter(adapter, model=model, effort=effort)
                review_adapter = resolve_reviewer_adapter(
                    executor_adapter=adapter,
                    executor_model=model,
                    executor_effort=effort,
                    reviewer_adapter=reviewer_adapter,
                    reviewer_model=reviewer_model,
                    reviewer_effort=reviewer_effort,
                )
                drain_queue(
                    repo_root,
                    agent_adapter,
                    max_iterations=max_cycles,
                    max_review_retries=max_review_retries,
                    reviewer_adapter=review_adapter,
                    task_ids=set(repair_task_ids),
                )
            except Exception as exc:
                raise ClickException(str(exc)) from exc
            drain_started = True
    elif propose_tasks:
        result = result.model_copy(update={"items": proposals_from_scan(result)})
        if write_tasks:
            written_task_ids, skipped_task_ids = write_proposals_to_tasks(
                repo_root,
                result.items,
                task_verify=task_verify,
            )

    if json_output:
        payload = result.model_dump(mode="json")
        if respond_to_review or propose_tasks:
            payload["respond_to_review"] = respond_to_review
            if respond_to_review:
                payload["max_cycles"] = max_cycles
                payload["run_repairs"] = run_repairs
                payload["repair_task_ids"] = repair_task_ids
                payload["drain_started"] = drain_started
            payload["write_tasks"] = write_tasks
            payload["written_task_ids"] = written_task_ids
            payload["skipped_task_ids"] = skipped_task_ids
            payload["written_count"] = len(written_task_ids)
            payload["skipped_count"] = len(skipped_task_ids)
        print(json.dumps(payload))
        return

    _print_text(
        result,
        propose_tasks=propose_tasks or respond_to_review,
        write_task_ids=written_task_ids if write_tasks else None,
        skipped_task_ids=skipped_task_ids if write_tasks else None,
    )


def _dedupe_task_ids(task_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(task_ids))


def _repair_task_ids_for_drain(
    repo_root: Path,
    *,
    written_task_ids: list[str],
    skipped_task_ids: list[str],
    task_verify: str | None,
) -> list[str]:
    if task_verify is None:
        return _dedupe_task_ids(written_task_ids + skipped_task_ids)

    tasks_by_id = {task.id: task for task in list_tasks(repo_root)}
    skipped_with_requested_verify = [
        task_id
        for task_id in skipped_task_ids
        if tasks_by_id.get(task_id) is not None and tasks_by_id[task_id].verify == task_verify
    ]
    return _dedupe_task_ids(written_task_ids + skipped_with_requested_verify)


def _print_text(
    result: MaintainScan,
    propose_tasks: bool = False,
    write_task_ids: list[str] | None = None,
    skipped_task_ids: list[str] | None = None,
) -> None:
    if not result.items:
        if propose_tasks:
            console.print("No maintainer proposals found.")
        else:
            console.print("No maintainer items found.")
        _print_pr_loop_state(result)
        if write_task_ids is not None:
            if write_task_ids:
                console.print(f"Written {len(write_task_ids)} maintainer task(s):")
                for task_id in write_task_ids:
                    console.print(f"  - {task_id}")
            elif skipped_task_ids:
                console.print("No new maintainer tasks were written.")
        if result.latest_run is not None:
            console.print(f"Latest run: {result.latest_run.id} ({result.latest_run.status})")
        return

    grouped: dict[str, list[MaintainItem]] = {key: [] for key in _CLASSIFICATION_ORDER}
    for item in result.items:
        grouped.setdefault(item.classification, []).append(item)

    for classification in _CLASSIFICATION_ORDER:
        items = grouped.get(classification) or []
        if not items:
            continue
        heading = _CLASSIFICATION_HEADINGS[classification]
        console.print(f"[bold]{heading}[/bold] ({len(items)})")
        for item in items:
            _print_item(item)
        console.print("")

    _print_pr_loop_state(result)

    if result.latest_run is not None:
        console.print(f"Latest run: {result.latest_run.id} ({result.latest_run.status})")

    if write_task_ids is not None:
        if write_task_ids:
            console.print(f"Written {len(write_task_ids)} maintainer task(s):")
            for task_id in write_task_ids:
                console.print(f"  - {task_id}")
        elif skipped_task_ids:
            console.print("No new maintainer tasks were written.")


def _print_item(item: MaintainItem) -> None:
    location = item.url or item.source
    console.print(f"- {item.source} ({location})")
    console.print(f"  fit: {item.fit}")
    console.print(f"  risk: {item.risk}")
    for line in item.proof.splitlines():
        console.print(f"  proof: {line}")
    if item.blocker:
        console.print(f"  blocker: {item.blocker}")
    if item.title:
        console.print(f"  title: {item.title}")
    if item.description:
        description_lines = item.description.splitlines()
        for index, line in enumerate(description_lines):
            prefix = "  description: " if index == 0 else "  "
            console.print(f"{prefix}{line}")
    if item.context_files:
        console.print(f"  context_files: {', '.join(item.context_files)}")
    if item.suggested_verify:
        console.print(f"  suggested_verify: {item.suggested_verify}")
    console.print(f"  next: {item.next_action}")


def _print_pr_loop_state(result: MaintainScan) -> None:
    loop_state = result.github_pr_loop_state
    if loop_state is None:
        return

    console.print(f"PR loop state: {loop_state.state}")
    for reason in loop_state.blocked_reasons:
        console.print(f"  blocked: {reason}")
    for item in loop_state.actionable_review_items:
        console.print(f"  actionable_review: {item}")
    for item in loop_state.failing_checks:
        console.print(f"  failing_check: {item}")
    for item in loop_state.waiting_review_items:
        console.print(f"  waiting_review: {item}")
    for item in loop_state.pending_checks:
        console.print(f"  pending_check: {item}")
