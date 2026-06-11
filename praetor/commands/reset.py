from pathlib import Path
from typing import Annotated

import typer

from praetor.commands import raise_usage_error, require_workspace
from praetor.models import Task, TaskStatus
from praetor.state import get_task, list_tasks, update_task_status
from praetor.worktree import remove_worktree


def reset_command(
    task_ids: Annotated[
        list[str] | None,
        typer.Argument(help="Task IDs to reset."),
    ] = None,
    clean_worktree: Annotated[
        bool,
        typer.Option("--clean-worktree", help="Remove the task worktree and branch."),
    ] = False,
    all_stale: Annotated[
        bool,
        typer.Option("--all-stale", help="Reset every task currently in running state."),
    ] = False,
) -> None:
    repo_root = Path.cwd()
    require_workspace(repo_root)

    task_ids = task_ids or []
    if all_stale and task_ids:
        raise_usage_error("Use either TASK_ID... or --all-stale, not both.")
    if not all_stale and not task_ids:
        raise_usage_error("Provide TASK_ID... or --all-stale.")

    tasks = _stale_tasks(repo_root) if all_stale else _tasks_by_id(repo_root, task_ids)
    for task in tasks:
        previous_status = task.status
        update_task_status(repo_root, task.id, TaskStatus.pending)
        if clean_worktree and (repo_root / ".praetor" / "worktrees" / task.id).exists():
            remove_worktree(task.id, repo_root, force=True)
        print(f"Reset {task.id} (was: {previous_status.value})")


def _tasks_by_id(repo_root: Path, task_ids: list[str]) -> list[Task]:
    tasks = []
    for task_id in task_ids:
        try:
            tasks.append(get_task(repo_root, task_id))
        except KeyError:
            print(f"Error: task not found: {task_id}")
    return tasks


def _stale_tasks(repo_root: Path) -> list[Task]:
    return [task for task in list_tasks(repo_root) if task.status is TaskStatus.running]
