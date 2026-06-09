import json
import sys
from pathlib import Path

from praetor.frontmatter import dump_task, parse_task
from praetor.models import Task, TaskStatus


def _praetor_dir(repo_root: Path) -> Path:
    return repo_root / ".praetor"


def _tasks_dir(repo_root: Path) -> Path:
    return _praetor_dir(repo_root) / "tasks"


def _state_json_path(repo_root: Path) -> Path:
    return _praetor_dir(repo_root) / "state.json"


def init_workspace(repo_root: Path) -> None:
    praetor_dir = _praetor_dir(repo_root)
    tasks_dir = praetor_dir / "tasks"
    logs_dir = praetor_dir / "logs"

    tasks_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    context_path = praetor_dir / "context.md"
    if not context_path.exists():
        claude_path = repo_root / "CLAUDE.md"
        if claude_path.exists():
            _write_text_atomic(context_path, claude_path.read_text())
        else:
            _write_text_atomic(context_path, "# Praetor Context\n\nAdd shared task context here.\n")

    state_path = _state_json_path(repo_root)
    if not state_path.exists():
        write_global_state(repo_root, {"version": 1, "last_run": None})

    _ensure_gitignore_excludes_praetor(repo_root)


def _ensure_gitignore_excludes_praetor(repo_root: Path) -> None:
    # Without this, every Praetor run creates untracked files under .praetor/,
    # which causes the merge service (and the user's own git workflow) to see
    # the working tree as dirty. Dogfood caught this on first parallel run.
    gitignore_path = repo_root / ".gitignore"
    existing = gitignore_path.read_text() if gitignore_path.exists() else ""
    lines = existing.splitlines()
    if any(line.strip().rstrip("/") == ".praetor" for line in lines):
        return

    prefix = existing if existing.endswith("\n") or not existing else existing + "\n"
    _write_text_atomic(gitignore_path, prefix + ".praetor/\n")


def list_tasks(repo_root: Path) -> list[Task]:
    tasks_dir = _tasks_dir(repo_root)
    if not tasks_dir.exists():
        return []

    tasks = []
    for path in sorted(tasks_dir.glob("*.md")):
        try:
            tasks.append(parse_task(path))
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: failed to parse task file {path}: {exc}", file=sys.stderr)

    return sorted(tasks, key=lambda task: task.created)


def get_task(repo_root: Path, task_id: str) -> Task:
    task = _find_task(repo_root, task_id)
    if task is None:
        msg = f"Task not found: {task_id}"
        raise KeyError(msg)
    return task[0]


def update_task_status(repo_root: Path, task_id: str, status: TaskStatus) -> None:
    task = get_task(repo_root, task_id)
    path = _find_task_path(repo_root, task_id)
    if path is None:
        msg = f"Task not found: {task_id}"
        raise KeyError(msg)

    task.status = status
    dump_task(task, path)


def read_global_state(repo_root: Path) -> dict:
    path = _state_json_path(repo_root)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def write_global_state(repo_root: Path, data: dict) -> None:
    path = _state_json_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(path, json.dumps(data, indent=2))


def _write_text_atomic(path: Path, content: str) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(content)
    tmp_path.replace(path)


def _find_task(repo_root: Path, task_id: str) -> tuple[Task, Path] | None:
    tasks_dir = _tasks_dir(repo_root)
    if not tasks_dir.exists():
        return None

    for path in sorted(tasks_dir.glob("*.md")):
        try:
            task = parse_task(path)
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: failed to parse task file {path}: {exc}", file=sys.stderr)
            continue
        if task.id == task_id:
            return task, path
    return None


def _find_task_path(repo_root: Path, task_id: str) -> Path | None:
    found = _find_task(repo_root, task_id)
    if found is None:
        return None
    return found[1]
