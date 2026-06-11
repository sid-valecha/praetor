from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from praetor.commands import raise_usage_error, require_workspace
from praetor.merge import MergeResult
from praetor.merge_queue import merge_one_task
from praetor.models import Task, TaskStatus
from praetor.state import list_tasks

console = Console()


def merge_command(
    task_ids: Annotated[
        list[str] | None,
        typer.Argument(help="Task IDs to merge."),
    ] = None,
    all_tasks: Annotated[
        bool,
        typer.Option("--all", help="Merge all pending merge tasks."),
    ] = False,
    retry: Annotated[
        bool,
        typer.Option("--retry", help="Retry tasks currently in merge_failed state."),
    ] = False,
    base_branch: Annotated[
        str,
        typer.Option("--base-branch", help="Base branch to merge into."),
    ] = "main",
) -> None:
    repo_root = Path.cwd()
    require_workspace(repo_root)
    task_ids = task_ids or []
    if not all_tasks and not task_ids:
        raise_usage_error("Provide TASK_ID... or --all.")

    tasks = list_tasks(repo_root)
    selected = _select_tasks(tasks, task_ids, all_tasks, retry)
    for task in selected:
        result = merge_one_task(repo_root, task.id, base_branch)
        _print_merge_result(result)


def _select_tasks(
    tasks: list[Task],
    task_ids: list[str],
    all_tasks: bool,
    retry: bool,
) -> list[Task]:
    allowed_statuses = {TaskStatus.pending_merge}
    if retry:
        allowed_statuses.add(TaskStatus.merge_failed)

    by_id = {task.id: task for task in tasks}
    if all_tasks:
        return _topological_order([task for task in tasks if task.status in allowed_statuses])

    selected = []
    for task_id in task_ids:
        task = by_id.get(task_id)
        if task is None:
            console.print(f"[yellow]Skipping unknown task {task_id}[/yellow]")
            continue
        if task.status not in allowed_statuses:
            expected = "pending_merge or merge_failed" if retry else "pending_merge"
            console.print(
                f"[yellow]Skipping {task.id}: status is {task.status.value}, "
                f"expected {expected}[/yellow]"
            )
            continue
        selected.append(task)
    return selected


def _topological_order(tasks: list[Task]) -> list[Task]:
    remaining = {task.id: task for task in tasks}
    ordered: list[Task] = []
    while remaining:
        progressed = False
        for task in list(remaining.values()):
            if all(dependency not in remaining for dependency in task.depends_on):
                ordered.append(task)
                del remaining[task.id]
                progressed = True
        if not progressed:
            ordered.extend(remaining.values())
            break
    return ordered


def _print_merge_result(result: MergeResult) -> None:
    if result.success:
        suffix = f" ({result.merge_commit_sha})" if result.merge_commit_sha else ""
        console.print(f"[green]Merged {result.task_id}{suffix}[/green]")
        return

    console.print(f"[red]Merge failed for {result.task_id}: {result.message}[/red]")
