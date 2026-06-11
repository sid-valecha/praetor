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
    from praetor.commands.merge import merge_command
    from praetor.commands.mcp import mcp_command
    from praetor.commands.reset import reset_command
    from praetor.commands.run import run_command
    from praetor.commands.status import status_command

    app.command("init")(init_command)
    app.command("add")(add_command)
    app.command("status")(status_command)
    app.command("run")(run_command)
    app.command("loop")(loop_command)
    app.command("merge")(merge_command)
    app.command("reset")(reset_command)
    app.command("logs")(logs_command)
    app.command("mcp")(mcp_command)
