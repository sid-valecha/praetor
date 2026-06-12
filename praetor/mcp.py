from pathlib import Path
from typing import Any

from praetor._mcp_sdk import FastMCP
from praetor.adapters import resolve_reviewer_adapter
from praetor.adapters.claude import ClaudeCodeAdapter
from praetor.dag import compute_ready_set
from praetor.merge_queue import merge_all_pending as merge_all_pending_core
from praetor.merge_queue import merge_one_task
from praetor.recovery import review_failure_for_task
from praetor.runner import drain_queue
from praetor.run_history import latest_run
from praetor.serialize import task_to_dict
from praetor.state import get_task as state_get_task
from praetor.state import init_workspace as state_init_workspace
from praetor.state import list_tasks as state_list_tasks
from praetor.task_creation import create_task

server = FastMCP("praetor")


@server.tool()
def init_workspace(repo_root: str) -> dict[str, list[str]]:
    """Initialize Praetor state for a repository."""
    return {"notes": state_init_workspace(Path(repo_root))}


@server.tool()
def list_tasks(repo_root: str) -> list[dict[str, Any]]:
    """List Praetor tasks with derived readiness."""
    root = Path(repo_root)
    tasks = state_list_tasks(root)
    ready_ids = {task.id for task in compute_ready_set(tasks)}
    return [task_to_dict(task, ready_ids, repo_root=root, tasks=tasks) for task in tasks]


@server.tool()
def get_task(repo_root: str, task_id: str) -> dict[str, Any]:
    """Fetch one Praetor task."""
    root = Path(repo_root)
    tasks = state_list_tasks(root)
    return task_to_dict(state_get_task(root, task_id), repo_root=root, tasks=tasks)


@server.tool()
def next_ready(repo_root: str) -> list[str]:
    """Return currently ready task IDs."""
    return [task.id for task in compute_ready_set(state_list_tasks(Path(repo_root)))]


@server.tool()
def add_task(
    repo_root: str,
    title: str,
    depends_on: list[str] = [],
    parallel_ok: bool = True,
    agent: str = "claude",
    verify: str | None = None,
    review: str = "off",
    merge_strategy: str = "manual",
) -> dict[str, Any]:
    """Create a Praetor task."""
    task = create_task(
        Path(repo_root),
        title=title,
        depends_on=depends_on,
        parallel_ok=parallel_ok,
        agent=agent,
        verify=verify,
        review=review,
        merge_strategy=merge_strategy,
    )
    return task_to_dict(task)


@server.tool()
def start_drain(
    repo_root: str,
    max_parallel: int = 1,
    base_branch: str = "main",
    merge_strategy: str | None = None,
    max_iterations: int | None = None,
    max_runtime_s: float | None = None,
    max_review_retries: int | None = None,
    model: str | None = None,
    effort: str | None = None,
    reviewer_adapter: str | None = None,
    reviewer_model: str | None = None,
    reviewer_effort: str | None = None,
) -> dict[str, str]:
    """Drain ready Praetor tasks using Claude Code."""
    review_adapter = resolve_reviewer_adapter(
        executor_adapter="claude",
        executor_model=model,
        executor_effort=effort,
        reviewer_adapter=reviewer_adapter,
        reviewer_model=reviewer_model,
        reviewer_effort=reviewer_effort,
    )
    drain_queue(
        Path(repo_root),
        ClaudeCodeAdapter(model=model, effort=effort),
        max_parallel=max_parallel,
        base_branch=base_branch,
        merge_strategy=merge_strategy,
        max_iterations=max_iterations,
        max_runtime_s=max_runtime_s,
        max_review_retries=max_review_retries,
        reviewer_adapter=review_adapter,
    )
    return {"status": "completed"}


@server.tool()
def merge_task(
    repo_root: str,
    task_id: str,
    base_branch: str = "main",
) -> dict[str, Any]:
    """Merge one pending Praetor task."""
    return merge_one_task(Path(repo_root), task_id, base_branch=base_branch).model_dump()


@server.tool()
def merge_all_pending(
    repo_root: str,
    base_branch: str = "main",
    retry_failed: bool = False,
) -> list[dict[str, Any]]:
    """Merge all pending Praetor tasks."""
    return merge_all_pending_core(
        Path(repo_root),
        base_branch=base_branch,
        retry_failed=retry_failed,
    )


@server.tool()
def get_logs(repo_root: str, task_id: str) -> dict[str, Any]:
    """Read a Praetor task log."""
    root = Path(repo_root)
    log_path = root / ".praetor" / "logs" / f"{task_id}.log"
    review_failure = None
    try:
        review_failure = review_failure_for_task(root, state_get_task(root, task_id))
    except KeyError:
        pass
    return {
        "task_id": task_id,
        "log": log_path.read_text() if log_path.exists() else "",
        "review_failure": review_failure,
    }


@server.tool()
def get_latest_run(repo_root: str) -> dict[str, Any] | None:
    """Fetch the latest Praetor run-history record."""
    run = latest_run(Path(repo_root))
    return None if run is None else run.model_dump(mode="json")


def run_stdio() -> None:
    server.run(transport="stdio")
