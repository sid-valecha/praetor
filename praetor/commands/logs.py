from pathlib import Path

from rich.console import Console

from praetor.commands import require_workspace

console = Console()


def logs_command(task_id: str) -> None:
    repo_root = Path.cwd()
    require_workspace(repo_root)

    log_path = repo_root / ".praetor" / "logs" / f"{task_id}.log"
    if not log_path.exists():
        console.print(f"No log found for {task_id}")
        return

    console.print(log_path.read_text(), end="")
