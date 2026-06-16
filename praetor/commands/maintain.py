import json
from pathlib import Path
from typing import Annotated

from rich.console import Console
import typer

from praetor.commands import raise_usage_error, require_workspace
from praetor.maintain import (
    MaintainItem,
    MaintainScan,
    write_proposals_to_tasks,
    proposals_from_scan,
    scan,
)

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

    if not once:
        raise_usage_error("praetor maintain currently requires --once.")
    if github_pr is not None and github_issue is not None:
        raise_usage_error("Choose only one focused GitHub target: --github-pr or --github-issue.")
    if write_tasks and not propose_tasks:
        raise_usage_error("--write-tasks requires --propose-tasks.")
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

    if propose_tasks:
        result = result.model_copy(update={"items": proposals_from_scan(result)})
        if write_tasks:
            written_task_ids, skipped_task_ids = write_proposals_to_tasks(
                repo_root,
                result.items,
                task_verify=task_verify,
            )

    if json_output:
        payload = result.model_dump(mode="json")
        if propose_tasks:
            payload["write_tasks"] = write_tasks
            payload["written_task_ids"] = written_task_ids
            payload["skipped_task_ids"] = skipped_task_ids
            payload["written_count"] = len(written_task_ids)
            payload["skipped_count"] = len(skipped_task_ids)
        print(json.dumps(payload))
        return

    _print_text(
        result,
        propose_tasks=propose_tasks,
        write_task_ids=written_task_ids if write_tasks else None,
        skipped_task_ids=skipped_task_ids if write_tasks else None,
    )


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
