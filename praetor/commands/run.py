from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from typer._click.exceptions import ClickException

from praetor.adapters import get_adapter
from praetor.commands import raise_usage_error, require_workspace
from praetor.dag import compute_ready_set
from praetor.models import TaskStatus
from praetor.runner import run_once
from praetor.state import get_task, list_tasks

console = Console()


def run_command(
    adapter: Annotated[str, typer.Option("--adapter", help="Agent adapter name.")] = "claude",
) -> None:
    repo_root = Path.cwd()
    require_workspace(repo_root)

    try:
        agent_adapter = get_adapter(adapter)
    except ValueError as exc:
        raise_usage_error(str(exc))

    while True:
        ready_tasks = compute_ready_set(list_tasks(repo_root))
        if not ready_tasks:
            return

        task_id = ready_tasks[0].id
        console.print(f"Running {task_id}...")
        try:
            processed = run_once(repo_root, agent_adapter)
        except Exception as exc:
            console.print(f"Failed {task_id}")
            raise ClickException(str(exc)) from exc

        if not processed:
            return

        task = get_task(repo_root, task_id)
        if task.status is TaskStatus.done:
            console.print(f"Done {task_id}")
        else:
            console.print(f"Failed {task_id}")
