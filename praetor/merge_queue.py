from pathlib import Path
from typing import Any

from praetor.merge import MergeResult, merge_task
from praetor.models import TaskStatus
from praetor.state import list_tasks, update_task_status


def merge_one_task(
    repo_root: Path,
    task_id: str,
    base_branch: str = "main",
) -> MergeResult:
    result = merge_task(task_id, repo_root, base_branch=base_branch)
    _apply_merge_result(repo_root, result)
    return result


def merge_all_pending(
    repo_root: Path,
    base_branch: str = "main",
    retry_failed: bool = False,
) -> list[dict[str, Any]]:
    statuses = {TaskStatus.pending_merge}
    if retry_failed:
        statuses.add(TaskStatus.merge_failed)

    results = []
    for task in list_tasks(repo_root):
        if task.status not in statuses:
            continue
        result = merge_one_task(repo_root, task.id, base_branch=base_branch)
        results.append(
            {
                "task_id": task.id,
                "success": result.success,
                "message": result.message,
            }
        )
    return results


def _apply_merge_result(repo_root: Path, result: MergeResult) -> None:
    if result.success:
        update_task_status(repo_root, result.task_id, TaskStatus.done)
        return

    _append_task_log(repo_root, result.task_id, _format_merge_failure(result))
    update_task_status(repo_root, result.task_id, TaskStatus.merge_failed)


def _format_merge_failure(result: MergeResult) -> str:
    content = f"{result.message}\n"
    if result.conflict_files:
        content += "Conflict files:\n"
        content += "".join(f"- {path}\n" for path in result.conflict_files)
    return content


def _append_task_log(repo_root: Path, task_id: str, content: str) -> None:
    log_path = repo_root / ".praetor" / "logs" / f"{task_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log_file:
        log_file.write(content)
