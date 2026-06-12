from pathlib import Path
import sys
from typing import Annotated

import typer
from typer._click.exceptions import ClickException

from praetor.adapters import get_adapter
from praetor.commands import raise_usage_error, require_workspace
from praetor.events import RunnerEvent
from praetor.loop import LoopOptions, loop_queue


def _print_event(event: RunnerEvent) -> None:
    prefix = f"[{event.type}]"
    if event.task_id:
        print(f"{prefix} {event.task_id}", file=sys.stderr)
    else:
        print(prefix, file=sys.stderr)


def loop_command(
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
            help="Override merge strategy for all tasks this loop: auto or manual.",
        ),
    ] = None,
    adapter: Annotated[str, typer.Option("--adapter", help="Agent adapter name.")] = "claude",
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help="Claude model name to pass through to the claude adapter.",
        ),
    ] = None,
    effort: Annotated[
        str | None,
        typer.Option(
            "--effort",
            help="Claude thinking/effort level to pass through to the claude adapter.",
        ),
    ] = None,
    once: Annotated[
        bool,
        typer.Option(
            "--once",
            help="Run one initial drain pass and exit without watching for new tasks.",
        ),
    ] = False,
    poll_interval: Annotated[
        float,
        typer.Option(
            "--poll-interval",
            help="Seconds to wait between polling checks when no filesystem event arrives.",
        ),
    ] = 5.0,
    max_iterations: Annotated[
        int | None,
        typer.Option(
            "--max-iterations",
            help="Stop each drain pass after this many task attempts.",
        ),
    ] = None,
    max_runtime: Annotated[
        float | None,
        typer.Option(
            "--max-runtime",
            help="Stop each drain pass after this many seconds.",
        ),
    ] = None,
    max_review_retries: Annotated[
        int | None,
        typer.Option(
            "--max-review-retries",
            help="Maximum automatic retries after reviewer rejection.",
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
    if poll_interval <= 0:
        raise_usage_error("--poll-interval must be > 0")
    if max_iterations is not None and max_iterations < 1:
        raise_usage_error("--max-iterations must be >= 1")
    if max_runtime is not None and max_runtime <= 0:
        raise_usage_error("--max-runtime must be > 0")
    if max_review_retries is not None and max_review_retries < 0:
        raise_usage_error("--max-review-retries must be >= 0")

    try:
        agent_adapter = get_adapter(adapter, model=model, effort=effort)
    except ValueError as exc:
        raise_usage_error(str(exc))

    try:
        loop_queue(
            repo_root,
            agent_adapter,
            LoopOptions(
                max_parallel=max_parallel,
                base_branch=base_branch,
                merge_strategy=merge_strategy,
                poll_interval=poll_interval,
                once=once,
                max_iterations=max_iterations,
                max_runtime_s=max_runtime,
                max_review_retries=max_review_retries,
            ),
            on_event=_print_event,
        )
    except Exception as exc:
        raise ClickException(str(exc)) from exc
