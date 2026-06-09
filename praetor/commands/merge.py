from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from praetor.commands import raise_usage_error, require_workspace
from praetor.merge import MergeResult, merge_task
from praetor.models import Task, TaskStatus
from praetor.state import list_tasks, update_task_status

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
        result = merge_task(task.id, repo_root, base_branch)
        _apply_merge_result(repo_root, result)


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


def _apply_merge_result(repo_root: Path, result: MergeResult) -> None:
    if result.success:
        update_task_status(repo_root, result.task_id, TaskStatus.done)
        suffix = f" ({result.merge_commit_sha})" if result.merge_commit_sha else ""
        console.print(f"[green]Merged {result.task_id}{suffix}[/green]")
        return

    _append_task_log(repo_root, result.task_id, _format_merge_failure(result))
    update_task_status(repo_root, result.task_id, TaskStatus.merge_failed)
    console.print(f"[red]Merge failed for {result.task_id}: {result.message}[/red]")


def _format_merge_failure(result: MergeResult) -> str:
    content = f"{result.message}\n"
    if result.conflict_files:
        content += "Conflict files:\n"
        content += "".join(f"- {path}\n" for path in result.conflict_files)
    return content


def _append_task_log(repo_root: Path, task_id: str, content: str) -> None:
    log_path = repo_root / ".praetor" / "logs" / f"{task_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log_file:
        log_file.write(content)
