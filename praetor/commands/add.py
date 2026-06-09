import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
from rich.console import Console

from praetor.commands import require_workspace
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskStatus

console = Console()


def add_command(
    title: Annotated[str, typer.Option("--title", help="The task heading, used as body h1.")],
    depends_on: Annotated[
        str,
        typer.Option("--depends-on", help="Comma-separated list of task IDs."),
    ] = "",
    verify: Annotated[
        str | None,
        typer.Option("--verify", help="Verify shell command."),
    ] = None,
    parallel_ok: Annotated[
        bool,
        typer.Option(
            "--parallel-ok/--no-parallel-ok",
            help="Whether this task may run concurrently with ready sibling tasks.",
        ),
    ] = True,
    merge_strategy: Annotated[
        str,
        typer.Option(
            "--merge-strategy",
            help="Merge strategy for this task: auto or manual.",
        ),
    ] = "manual",
    agent: Annotated[str, typer.Option("--agent", help="Agent adapter name.")] = "claude",
) -> None:
    repo_root = Path.cwd()
    require_workspace(repo_root)
    if merge_strategy not in {"auto", "manual"}:
        raise typer.BadParameter("merge strategy must be one of: auto, manual")

    task_id = _task_id_from_title(title)
    dependencies = [
        dependency.strip() for dependency in depends_on.split(",") if dependency.strip()
    ]
    task = Task(
        id=task_id,
        status=TaskStatus.pending,
        depends_on=dependencies,
        parallel_ok=parallel_ok,
        agent=agent,
        verify=verify,
        merge_strategy=merge_strategy,
        created=datetime.now(UTC),
        body=f"# {title}\n",
    )

    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task_id}.md")
    console.print(f"Created task {task_id}")


def _task_id_from_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not slug:
        slug = "task"
    return f"{slug}-{str(uuid4())[:8]}"
