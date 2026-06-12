import json
from pathlib import Path
from typing import Annotated

from rich.console import Console
from rich.table import Table
from rich.text import Text
import typer

from praetor.commands import require_workspace
from praetor.dag import compute_ready_set
from praetor.models import TaskStatus
from praetor.serialize import task_to_dict
from praetor.state import list_tasks

console = Console()

STATUS_STYLES = {
    "pending": "yellow",
    "ready": "cyan",
    "running": "blue",
    "pending_merge": "cyan",
    "merge_failed": "orange3",
    "review_failed": "orange3",
    "cancelled": "grey50",
    "done": "green",
    "failed": "red",
    "blocked": "magenta",
}


def status_command(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a JSON list of tasks instead of the Rich table."),
    ] = False,
) -> None:
    repo_root = Path.cwd()
    require_workspace(repo_root)

    tasks = list_tasks(repo_root)
    ready_ids = {task.id for task in compute_ready_set(tasks)}

    if json_output:
        payload = [task_to_dict(task, ready_ids) for task in tasks]
        print(json.dumps(payload))
        return

    if not tasks:
        console.print("No tasks found. Run praetor add to create one.")
        return

    table = Table()
    table.add_column("ID")
    table.add_column("Status")
    table.add_column("Depends On")
    table.add_column("Verify")

    for task in tasks:
        status = (
            "ready"
            if task.status is TaskStatus.pending and task.id in ready_ids
            else task.status.value
        )
        table.add_row(
            task.id,
            Text(status, style=STATUS_STYLES[status]),
            ", ".join(task.depends_on),
            task.verify or "",
        )

    console.print(table)
