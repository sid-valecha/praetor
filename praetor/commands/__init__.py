from pathlib import Path

import typer
from typer._click.exceptions import UsageError


def require_workspace(repo_root: Path) -> None:
    if not (repo_root / ".praetor").is_dir():
        raise_usage_error(".praetor/ does not exist. Run praetor init first.")


def raise_usage_error(message: str) -> None:
    raise UsageError(message)


def register_commands(app: typer.Typer) -> None:
    from praetor.commands.add import add_command
    from praetor.commands.init import init_command
    from praetor.commands.loop import loop_command
    from praetor.commands.logs import logs_command
    from praetor.commands.maintain import maintain_command
    from praetor.commands.merge import merge_command
    from praetor.commands.mcp import mcp_command
    from praetor.commands.reset import reset_command
    from praetor.commands.run import run_command
    from praetor.commands.status import status_command

    app.command("init", help="Initialize .praetor workspace state in the current repository.")(
        init_command,
    )
    app.command(
        "add",
        help="Create a task from a title, dependencies, verify command, and schema fields.",
    )(add_command)
    app.command("status", help="Show queued tasks, dependency readiness, and run state.")(
        status_command,
    )
    app.command(
        "run",
        help="Run one or more ready tasks through adapter execution and verifier/reviewer gates.",
    )(run_command)
    app.command(
        "loop",
        help="Continuously watch .praetor/tasks/ and drain in bounded batches.",
    )(loop_command)
    app.command("merge", help="Merge accepted parallel branches after verify/review.")(
        merge_command,
    )
    app.command(
        "reset",
        help="Move failed/blocked/merge-failed tasks back to pending for another attempt.",
    )(reset_command)
    app.command("logs", help="Show saved stdout/stderr for one task id.")(logs_command)
    app.command(
        "maintain",
        help="Run one-shot local maintainer triage (experimental).",
    )(maintain_command)
    app.command("mcp", help="Run the Praetor MCP server against this repository.")(
        mcp_command,
    )
