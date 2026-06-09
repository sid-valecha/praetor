from pathlib import Path

import frontmatter

from praetor.models import Task

FRONTMATTER_FIELDS = (
    "id",
    "status",
    "depends_on",
    "parallel_ok",
    "agent",
    "verify",
    "review",
    "merge_strategy",
    "created",
)


def parse_task(path: Path) -> Task:
    post = frontmatter.loads(path.read_text())
    data = dict(post.metadata)
    if data.get("review") is False:
        data["review"] = "off"
    data["body"] = post.content
    return Task.model_validate(data)


def dump_task(task: Task, path: Path) -> None:
    dumped = task.model_dump(mode="json")
    metadata = {field: dumped[field] for field in FRONTMATTER_FIELDS}
    post = frontmatter.Post(task.body, **metadata)
    content = frontmatter.dumps(post)

    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(content)
    tmp_path.replace(path)
