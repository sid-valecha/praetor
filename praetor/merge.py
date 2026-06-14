from pathlib import Path
import re
import subprocess

from pydantic import BaseModel

from praetor.worktree import branch_for_task


class MergeError(RuntimeError):
    """Raised on git operations that leave the base repo needing human recovery."""


class MergeResult(BaseModel):
    task_id: str
    success: bool
    merge_commit_sha: str | None = None
    conflict_files: list[str] | None = None
    message: str


def merge_task(
    task_id: str,
    repo_root: Path,
    base_branch: str = "main",
) -> MergeResult:
    """Merge praetor/<task_id> into base_branch in the main repo working tree."""

    repo_root = repo_root.resolve()
    task_branch = branch_for_task(task_id, repo_root)
    previous_ref = _current_ref(repo_root)

    if _git_status_porcelain(repo_root):
        return MergeResult(
            task_id=task_id,
            success=False,
            message="base repo has uncommitted changes; refusing to merge",
        )

    if not _branch_exists(task_branch, repo_root):
        return MergeResult(
            task_id=task_id,
            success=False,
            message=f"branch not found: {task_branch}",
        )

    ancestor = _run_git(
        ["merge-base", "--is-ancestor", task_branch, base_branch],
        repo_root,
    )
    if ancestor.returncode == 0:
        return MergeResult(
            task_id=task_id,
            success=True,
            merge_commit_sha=_rev_parse(base_branch, repo_root),
            message="already merged",
        )

    switched = previous_ref != base_branch
    try:
        checkout = _run_git(["checkout", base_branch], repo_root)
        if checkout.returncode != 0:
            return MergeResult(
                task_id=task_id,
                success=False,
                message=checkout.stderr.strip() or checkout.stdout.strip(),
            )

        merge = _run_git(
            [
                "merge",
                "--no-ff",
                "--no-edit",
                "-m",
                f"praetor: merge task {task_id} (branch {task_branch})",
                task_branch,
            ],
            repo_root,
        )
        if merge.returncode == 0:
            return MergeResult(
                task_id=task_id,
                success=True,
                merge_commit_sha=_rev_parse("HEAD", repo_root),
                message="merged",
            )

        conflict_files = _parse_conflict_files(f"{merge.stdout}\n{merge.stderr}")
        abort = _run_git(["merge", "--abort"], repo_root)
        if abort.returncode != 0:
            msg = abort.stderr.strip() or abort.stdout.strip()
            raise MergeError(f"git merge --abort failed: {msg}")
        return MergeResult(
            task_id=task_id,
            success=False,
            conflict_files=conflict_files,
            message="merge conflict",
        )
    finally:
        if switched:
            restore = _run_git(["checkout", previous_ref], repo_root)
            if restore.returncode != 0:
                msg = restore.stderr.strip() or restore.stdout.strip()
                raise MergeError(f"failed to restore previous ref {previous_ref}: {msg}")


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        msg = f"Git command failed to start: {' '.join(command)}: {exc}"
        raise MergeError(msg) from exc


def _git_status_porcelain(repo_root: Path) -> str:
    result = _run_git(["status", "--porcelain"], repo_root)
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise MergeError(f"git status failed: {msg}")
    return result.stdout.strip()


def _branch_exists(branch: str, repo_root: Path) -> bool:
    result = _run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], repo_root)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    msg = result.stderr.strip() or result.stdout.strip()
    raise MergeError(f"git show-ref failed: {msg}")


def _rev_parse(ref: str, repo_root: Path) -> str:
    result = _run_git(["rev-parse", "--verify", ref], repo_root)
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise MergeError(f"git rev-parse failed: {msg}")
    return result.stdout.strip()


def _current_ref(repo_root: Path) -> str:
    branch = _run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], repo_root)
    if branch.returncode == 0:
        return branch.stdout.strip()
    return _rev_parse("HEAD", repo_root)


def _parse_conflict_files(output: str) -> list[str]:
    conflict_files = []
    for line in output.splitlines():
        match = re.search(r"Merge conflict in (.+)$", line)
        if match:
            conflict_files.append(match.group(1).strip())
    return conflict_files
