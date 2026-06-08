from pathlib import Path
import subprocess

from pydantic import BaseModel


class WorktreeError(RuntimeError):
    """Raised on any git worktree subprocess failure or invariant violation."""


class Worktree(BaseModel):
    task_id: str
    path: Path
    branch: str
    base_ref: str


def create_worktree(task_id: str, repo_root: Path, base_ref: str = "HEAD") -> Worktree:
    repo_root = repo_root.resolve()
    worktrees_dir = _worktrees_dir(repo_root)
    worktree_path = worktrees_dir / task_id
    branch = _branch_name(task_id)

    if worktree_path.exists():
        msg = f"Worktree path already exists: {worktree_path}"
        raise WorktreeError(msg)
    if _branch_exists(branch, repo_root):
        msg = f"Worktree branch already exists: {branch}"
        raise WorktreeError(msg)

    full_sha = _run_git(["rev-parse", "--verify", base_ref], repo_root).splitlines()[0]

    worktrees_dir.mkdir(parents=True, exist_ok=True)
    _run_git(["worktree", "add", "-b", branch, str(worktree_path), full_sha], repo_root)

    return Worktree(task_id=task_id, path=worktree_path, branch=branch, base_ref=full_sha)


def list_worktrees(repo_root: Path) -> list[Worktree]:
    repo_root = repo_root.resolve()
    worktrees_dir = _worktrees_dir(repo_root)
    output = _run_git(["worktree", "list", "--porcelain"], repo_root)
    worktrees = []

    for entry in _parse_porcelain(output):
        path_text = entry.get("worktree")
        head = entry.get("HEAD")
        branch_ref = entry.get("branch")
        if path_text is None or head is None or branch_ref is None:
            continue

        path = Path(path_text).resolve()
        if not _is_under(path, worktrees_dir):
            continue

        prefix = "refs/heads/"
        if not branch_ref.startswith(prefix):
            continue

        worktrees.append(
            Worktree(
                task_id=path.name,
                path=path,
                branch=branch_ref.removeprefix(prefix),
                base_ref=head,
            )
        )

    return worktrees


def remove_worktree(task_id: str, repo_root: Path, force: bool = False) -> None:
    repo_root = repo_root.resolve()
    worktree_path = _worktrees_dir(repo_root) / task_id
    branch = _branch_name(task_id)

    if not worktree_path.exists():
        if force:
            return
        msg = f"Worktree path does not exist: {worktree_path}"
        raise WorktreeError(msg)

    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path))
    _run_git(args, repo_root)
    _delete_branch(branch, repo_root)


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


def _branch_name(task_id: str) -> str:
    return f"praetor/{task_id}"


def _branch_exists(branch: str, repo_root: Path) -> bool:
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
    completed = subprocess.run(
        ["git", "branch", "-D", branch],
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
