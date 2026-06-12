from pathlib import Path
from typing import Any

from praetor.models import Task, TaskStatus
from praetor.recovery import review_failure_for_task, waiting_on


def task_to_dict(
    task: Task,
    ready_ids: set[str] | None = None,
    *,
    repo_root: Path | None = None,
    tasks: list[Task] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": task.id,
        "status": task.status.value,
        "depends_on": task.depends_on,
        "parallel_ok": task.parallel_ok,
        "merge_strategy": task.merge_strategy,
        "agent": task.agent,
        "verify": task.verify,
        "review": task.review,
        "retry": task.retry,
        "priority": task.priority,
        "env": task.env,
        "context_files": task.context_files,
        "created": task.created.isoformat().replace("+00:00", "Z"),
        "review_failure": None,
        "waiting_on": [],
    }
    if repo_root is not None:
        payload["review_failure"] = review_failure_for_task(repo_root, task)
        if tasks is not None:
            payload["waiting_on"] = waiting_on(repo_root, task, tasks)
    if ready_ids is not None:
        payload["ready"] = task.status is TaskStatus.pending and task.id in ready_ids
    return payload
