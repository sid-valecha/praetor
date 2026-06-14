import json
from pathlib import Path
import re
import subprocess

from pydantic import BaseModel

from praetor.models import validate_task_id


_BRANCH_PREFIX = "praetor"
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class WorktreeError(RuntimeError):
    """Raised on any git worktree subprocess failure or invariant violation."""


class Worktree(BaseModel):
    task_id: str
    path: Path
    branch: str
    # The branch name to merge back into (e.g., "main").
    base_branch: str
    base_sha: str


def create_worktree(
    task_id: str,
    repo_root: Path,
    base_branch: str = "main",
    base_ref: str = "HEAD",
) -> Worktree:
    task_id = _validate_task_id(task_id)
    repo_root = repo_root.resolve()
    base_branch = _validate_branch_name(base_branch, repo_root, "base branch")
    base_ref = _validate_ref_name(base_ref, "base ref")
    worktree_path = _task_worktree_path(repo_root, task_id)
    worktrees_dir = worktree_path.parent
    branch = _branch_name(task_id, repo_root)

    if worktree_path.exists():
        msg = f"Worktree path already exists: {worktree_path}"
        raise WorktreeError(msg)
    if _branch_exists(branch, repo_root):
        msg = f"Worktree branch already exists: {branch}"
        raise WorktreeError(msg)

    full_sha = _run_git(
        ["rev-parse", "--verify", "--end-of-options", base_ref],
        repo_root,
    ).splitlines()[0]

    worktrees_dir.mkdir(parents=True, exist_ok=True)
    _run_git(["worktree", "add", "-b", branch, str(worktree_path), full_sha], repo_root)
    _ignore_metadata_sidecar(worktree_path)
    _metadata_path(worktree_path).write_text(
        json.dumps(
            {
                "task_id": task_id,
                "branch": branch,
                "base_branch": base_branch,
                "base_sha": full_sha,
            },
            indent=2,
        )
    )

    return Worktree(
        task_id=task_id,
        path=worktree_path,
        branch=branch,
        base_branch=base_branch,
        base_sha=full_sha,
    )


def list_worktrees(repo_root: Path) -> list[Worktree]:
    repo_root = repo_root.resolve()
    worktrees_dir = _worktrees_dir(repo_root).resolve()
    output = _run_git(["worktree", "list", "--porcelain"], repo_root)
    worktrees = []

    for entry in _parse_porcelain(output):
        path_text = entry.get("worktree")
        if path_text is None:
            continue

        path = Path(path_text).resolve()
        if not _is_under(path, worktrees_dir):
            continue

        metadata_path = _metadata_path(path)
        if not metadata_path.exists():
            continue
        metadata = json.loads(metadata_path.read_text())
        task_id = _validate_task_id(metadata["task_id"])
        branch = _validate_branch_name(metadata["branch"], repo_root, "worktree branch")
        base_branch = _validate_branch_name(
            metadata["base_branch"],
            repo_root,
            "base branch",
        )
        base_sha = _validate_full_sha(metadata["base_sha"])

        worktrees.append(
            Worktree(
                task_id=task_id,
                path=path,
                branch=branch,
                base_branch=base_branch,
                base_sha=base_sha,
            )
        )

    return worktrees


def get_worktree(task_id: str, repo_root: Path) -> Worktree | None:
    task_id = _validate_task_id(task_id)
    for worktree in list_worktrees(repo_root):
        if worktree.task_id == task_id:
            return worktree
    return None


def remove_worktree(task_id: str, repo_root: Path, force: bool = False) -> None:
    task_id = _validate_task_id(task_id)
    repo_root = repo_root.resolve()
    worktree_path = _task_worktree_path(repo_root, task_id)

    if not worktree_path.exists():
        if force:
            return
        msg = f"Worktree path does not exist: {worktree_path}"
        raise WorktreeError(msg)

    branch = branch_for_task(task_id, repo_root)

    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path))
    _run_git(args, repo_root)
    _delete_branch(branch, repo_root)


def branch_for_task(task_id: str, repo_root: Path) -> str:
    """Return the recorded branch for an existing worktree, or the deterministic
    branch name we would use to create one if no sidecar exists yet."""
    task_id = _validate_task_id(task_id)
    repo_root = repo_root.resolve()
    worktree_path = _task_worktree_path(repo_root, task_id)
    metadata_path = _metadata_path(worktree_path)
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        return _validate_branch_name(metadata["branch"], repo_root, "worktree branch")
    return _branch_name(task_id, repo_root)


def _run_git(args: list[str], cwd: Path) -> str:
    command = ["git", *args]
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        msg = f"Git command failed to start: {' '.join(command)}: {exc}"
        raise WorktreeError(msg) from exc

    if completed.returncode != 0:
        msg = (
            f"Git command failed: {' '.join(command)} "
            f"(exit {completed.returncode}): {completed.stderr.strip()}"
        )
        raise WorktreeError(msg)

    return completed.stdout.rstrip("\n")


def _worktrees_dir(repo_root: Path) -> Path:
    return repo_root / ".praetor" / "worktrees"


def _task_worktree_path(repo_root: Path, task_id: str) -> Path:
    worktrees_dir = _worktrees_dir(repo_root).resolve()
    worktree_path = (worktrees_dir / _validate_task_id(task_id)).resolve()
    if worktree_path.parent != worktrees_dir:
        msg = f"Invalid worktree path for task id: {task_id}"
        raise WorktreeError(msg)
    return worktree_path


def _metadata_path(worktree_path: Path) -> Path:
    return worktree_path / ".praetor-meta.json"


def _ignore_metadata_sidecar(worktree_path: Path) -> None:
    exclude_text = _run_git(["rev-parse", "--git-path", "info/exclude"], worktree_path)
    exclude_path = Path(exclude_text)
    if not exclude_path.is_absolute():
        exclude_path = worktree_path / exclude_path

    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text() if exclude_path.exists() else ""
    if ".praetor-meta.json" in existing.splitlines():
        return
    with exclude_path.open("a") as exclude_file:
        if existing and not existing.endswith("\n"):
            exclude_file.write("\n")
        exclude_file.write(".praetor-meta.json\n")


def _branch_name(task_id: str, repo_root: Path) -> str:
    task_id = _validate_task_id(task_id)
    if _ref_exists(f"refs/heads/{_BRANCH_PREFIX}", repo_root):
        return _validate_branch_name(
            f"{_BRANCH_PREFIX}-{task_id}",
            repo_root,
            "worktree branch",
        )
    return _validate_branch_name(
        f"{_BRANCH_PREFIX}/{task_id}",
        repo_root,
        "worktree branch",
    )


def _ref_exists(ref: str, repo_root: Path) -> bool:
    completed = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", ref],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False

    msg = (
        f"Git command failed: git show-ref --verify --quiet {ref} "
        f"(exit {completed.returncode}): {completed.stderr.strip()}"
    )
    raise WorktreeError(msg)


def _branch_exists(branch: str, repo_root: Path) -> bool:
    branch = _validate_branch_name(branch, repo_root, "worktree branch")
    completed = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False

    msg = (
        "Git command failed: "
        f"git show-ref --verify --quiet refs/heads/{branch} "
        f"(exit {completed.returncode}): {completed.stderr.strip()}"
    )
    raise WorktreeError(msg)


def _delete_branch(branch: str, repo_root: Path) -> None:
    branch = _validate_branch_name(branch, repo_root, "worktree branch")
    completed = subprocess.run(
        ["git", "branch", "-D", "--", branch],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return

    stderr = completed.stderr.strip()
    if branch in stderr and "not found" in stderr.lower():
        return

    msg = f"Git command failed: git branch -D {branch} (exit {completed.returncode}): {stderr}"
    raise WorktreeError(msg)


def _parse_porcelain(output: str) -> list[dict[str, str]]:
    entries = []
    current: dict[str, str] = {}

    for line in output.splitlines():
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue

        key, _, value = line.partition(" ")
        current[key] = value

    if current:
        entries.append(current)

    return entries


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_task_id(task_id: str) -> str:
    try:
        return validate_task_id(task_id)
    except ValueError as exc:
        msg = str(exc)
        raise WorktreeError(msg) from exc


def _validate_branch_name(value: str, repo_root: Path, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("-")
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or ".." in value
        or "@{" in value
        or value.endswith(".")
        or value.endswith(".lock")
        or any(ord(char) < 32 or char.isspace() for char in value)
        or any(
            part in {"", ".", ".."} or part.startswith(".") or part.startswith("-")
            for part in value.split("/")
        )
    ):
        msg = f"Invalid {label}: {value}"
        raise WorktreeError(msg)

    completed = subprocess.run(
        ["git", "check-ref-format", "--branch", value],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        msg = f"Invalid {label}: {value}"
        raise WorktreeError(msg)
    return value


def _validate_ref_name(value: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("-")
        or value.startswith("/")
        or "\\" in value
        or ".." in value
        or any(ord(char) < 32 or char.isspace() for char in value)
    ):
        msg = f"Invalid {label}: {value}"
        raise WorktreeError(msg)
    return value


def _validate_full_sha(value: str) -> str:
    if not isinstance(value, str) or _FULL_SHA_RE.fullmatch(value) is None:
        msg = f"Invalid base sha: {value}"
        raise WorktreeError(msg)
    return value
