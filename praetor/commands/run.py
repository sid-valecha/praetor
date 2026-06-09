from pathlib import Path
from typing import Annotated

import typer
from typer._click.exceptions import ClickException

from praetor.adapters import get_adapter
from praetor.commands import raise_usage_error, require_workspace
from praetor.runner import drain_queue


def run_command(
    adapter: Annotated[str, typer.Option("--adapter", help="Agent adapter name.")] = "claude",
    max_parallel: Annotated[
        int,
        typer.Option(
            "--max-parallel",
            help=(
                "Maximum tasks to run concurrently (default 1; >1 enables "
                "parallel mode with per-task worktrees)."
            ),
        ),
    ] = 1,
    base_branch: Annotated[
        str,
        typer.Option(
            "--base-branch",
            help="Base branch to fork worktrees from in parallel mode (default 'main').",
        ),
    ] = "main",
    merge_strategy: Annotated[
        str | None,
        typer.Option(
            "--merge-strategy",
            help="Override merge strategy for all tasks this run: auto or manual.",
        ),
    ] = None,
) -> None:
    repo_root = Path.cwd()
    require_workspace(repo_root)
    if max_parallel < 1:
        raise_usage_error("--max-parallel must be >= 1")
    if merge_strategy not in {None, "auto", "manual"}:
        raise_usage_error("--merge-strategy must be one of: auto, manual")
    if merge_strategy is not None and max_parallel == 1:
        raise typer.BadParameter(
            "--merge-strategy only applies in parallel mode "
            "(--max-parallel > 1). Sequential mode (the default "
            "--max-parallel 1) runs tasks in your current checkout "
            "without worktrees or merging. To use auto-merge, pass "
            "--max-parallel N with N > 1."
        )

    try:
        agent_adapter = get_adapter(adapter)
    except ValueError as exc:
        raise_usage_error(str(exc))

    try:
        drain_queue(
            repo_root,
            agent_adapter,
            max_parallel=max_parallel,
            base_branch=base_branch,
            merge_strategy=merge_strategy,
        )
    except Exception as exc:
        raise ClickException(str(exc)) from exc
