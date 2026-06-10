from typing import Any

from praetor.models import Task, TaskStatus


def task_to_dict(task: Task, ready_ids: set[str] | None = None) -> dict[str, Any]:
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
    }
    if ready_ids is not None:
        payload["ready"] = task.status is TaskStatus.pending and task.id in ready_ids
    return payload
