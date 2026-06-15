import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from praetor.frontmatter import dump_task
from praetor.models import Task, TaskStatus
from praetor.state import init_workspace


def create_task(
    repo_root: Path,
    title: str,
    depends_on: list[str],
    parallel_ok: bool = True,
    agent: str = "claude",
    verify: str | None = None,
    review: str = "off",
    merge_strategy: str = "manual",
    task_id: str | None = None,
    context_files: list[str] | None = None,
    body: str | None = None,
) -> Task:
    task = Task(
        id=task_id or _task_id_from_title(title),
        status=TaskStatus.pending,
        depends_on=depends_on,
        parallel_ok=parallel_ok,
        agent=agent,
        verify=verify,
        review=review,
        merge_strategy=merge_strategy,
        created=datetime.now(UTC),
        context_files=context_files or [],
        body=f"# {title}\n" if body is None else body,
    )
    init_workspace(repo_root)
    task_path = repo_root / ".praetor" / "tasks" / f"{task.id}.md"
    if task_path.exists():
        raise ValueError(f"Task already exists: {task.id}")

    dump_task(task, task_path)
    return task


def _task_id_from_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not slug:
        slug = "task"
    return f"{slug}-{str(uuid4())[:8]}"
