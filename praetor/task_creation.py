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
    merge_strategy: str = "manual",
) -> Task:
    task = Task(
        id=_task_id_from_title(title),
        status=TaskStatus.pending,
        depends_on=depends_on,
        parallel_ok=parallel_ok,
        agent=agent,
        verify=verify,
        merge_strategy=merge_strategy,
        created=datetime.now(UTC),
        body=f"# {title}\n",
    )
    init_workspace(repo_root)
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")
    return task


def _task_id_from_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not slug:
        slug = "task"
    return f"{slug}-{str(uuid4())[:8]}"
