from pathlib import Path

from rich.console import Console

from praetor.state import init_workspace

console = Console()


def init_command() -> None:
    repo_root = Path.cwd()
    init_workspace(repo_root)
    console.print(f"Initialized .praetor/ in {repo_root}")
