import json
from pathlib import Path
from typing import Annotated

from rich.console import Console
import typer

from praetor.commands import raise_usage_error, require_workspace
from praetor.maintain import MaintainItem, MaintainScan, scan

console = Console()

_CLASSIFICATION_HEADINGS: dict[str, str] = {
    "autonomous": "Autonomous",
    "needs_owner": "Needs owner",
    "defer": "Defer",
}

_CLASSIFICATION_ORDER: list[str] = ["autonomous", "needs_owner", "defer"]


def maintain_command(
    once: Annotated[
        bool,
        typer.Option("--once", help="Run a single read-only maintainer scan."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the scan result as JSON."),
    ] = False,
) -> None:
    repo_root = Path.cwd()
    require_workspace(repo_root)

    if not once:
        raise_usage_error("praetor maintain currently requires --once.")

    result = scan(repo_root)

    if json_output:
        print(json.dumps(result.model_dump(mode="json")))
        return

    _print_text(result)


def _print_text(result: MaintainScan) -> None:
    if not result.items:
        console.print("No maintainer items found.")
        if result.latest_run is not None:
            console.print(f"Latest run: {result.latest_run.id} ({result.latest_run.status})")
        return

    grouped: dict[str, list[MaintainItem]] = {key: [] for key in _CLASSIFICATION_ORDER}
    for item in result.items:
        grouped.setdefault(item.classification, []).append(item)

    for classification in _CLASSIFICATION_ORDER:
        items = grouped.get(classification) or []
        if not items:
            continue
        heading = _CLASSIFICATION_HEADINGS[classification]
        console.print(f"[bold]{heading}[/bold] ({len(items)})")
        for item in items:
            _print_item(item)
        console.print("")

    if result.latest_run is not None:
        console.print(f"Latest run: {result.latest_run.id} ({result.latest_run.status})")


def _print_item(item: MaintainItem) -> None:
    location = item.url or item.source
    console.print(f"- {item.source} ({location})")
    console.print(f"  fit: {item.fit}")
    console.print(f"  risk: {item.risk}")
    for line in item.proof.splitlines():
        console.print(f"  proof: {line}")
    if item.blocker:
        console.print(f"  blocker: {item.blocker}")
    console.print(f"  next: {item.next_action}")
