import subprocess
from pathlib import Path

from praetor.dag import compute_ready_set, propagate_blocked
from praetor.models import AgentAdapter, Task, TaskStatus
from praetor.state import list_tasks, update_task_status


def render_task_prompt(task: Task, context: str = "") -> str:
    parts = []
    if context:
        parts.append(context.strip())
    if task.body:
        parts.append(task.body.strip())
    if task.verify is not None:
        parts.append(f"Verify command: {task.verify}")
    return "\n\n".join(parts)


def run_once(repo_root: Path, adapter: AgentAdapter) -> bool:
    ready_tasks = compute_ready_set(list_tasks(repo_root))
    if not ready_tasks:
        return False

    task = ready_tasks[0]
    update_task_status(repo_root, task.id, TaskStatus.running)

    try:
        context_path = repo_root / ".praetor" / "context.md"
        context = context_path.read_text() if context_path.exists() else ""
        prompt = render_task_prompt(task, context)
        result = adapter.exec(prompt, cwd=repo_root)
    except Exception:
        _mark_failed_and_propagate(repo_root, task.id)
        raise

    log_path = repo_root / ".praetor" / "logs" / f"{task.id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"{result.stdout}{result.stderr}")

    if result.exit_code != 0:
        _mark_failed_and_propagate(repo_root, task.id)
        return True

    if task.verify is None:
        update_task_status(repo_root, task.id, TaskStatus.done)
        return True

    verify_result = subprocess.run(
        task.verify,
        shell=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    with log_path.open("a") as log_file:
        log_file.write(f"{verify_result.stdout}{verify_result.stderr}")

    if verify_result.returncode != 0:
        _mark_failed_and_propagate(repo_root, task.id)
        return True

    update_task_status(repo_root, task.id, TaskStatus.done)
    return True


def drain_queue(repo_root: Path, adapter: AgentAdapter) -> None:
    while run_once(repo_root, adapter):
        pass


def _mark_failed_and_propagate(repo_root: Path, task_id: str) -> None:
    update_task_status(repo_root, task_id, TaskStatus.failed)
    for blocked_task_id in propagate_blocked(list_tasks(repo_root)):
        update_task_status(repo_root, blocked_task_id, TaskStatus.blocked)
