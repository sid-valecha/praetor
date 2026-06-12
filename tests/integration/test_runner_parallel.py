from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess
import threading
import time

import pytest

from praetor.frontmatter import dump_task
from praetor.models import Task, TaskResult, TaskStatus
from praetor.runner import drain_queue
from praetor.state import get_task, init_workspace
from praetor.worktree import create_worktree


class SleepRecordingAdapter:
    name = "sleep-recording"

    def __init__(self, sleep_s: float = 0.2) -> None:
        self.sleep_s = sleep_s
        self.records: list[tuple[str, Path, float, float]] = []
        self._lock = threading.Lock()

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        task_id = _task_id_from_prompt(prompt)
        start = time.perf_counter()
        time.sleep(self.sleep_s)
        end = time.perf_counter()
        with self._lock:
            self.records.append((task_id, cwd, start, end))
        return TaskResult(exit_code=0, stdout=f"{task_id} done\n", stderr="", duration_ms=0)


class FileEditingAdapter(SleepRecordingAdapter):
    def __init__(self, edits: dict[str, dict[str, str]]) -> None:
        super().__init__(sleep_s=0.0)
        self.edits = edits

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        task_id = _task_id_from_prompt(prompt)
        for relative_path, content in self.edits.get(task_id, {}).items():
            path = cwd / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return super().exec(prompt, cwd, timeout_s)


class ReviewRejectingFileEditingAdapter(FileEditingAdapter):
    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        if prompt.startswith("You are the Praetor task reviewer."):
            return TaskResult(
                exit_code=0,
                stdout=(
                    "{"
                    '"verdict": "needs_revision", '
                    '"severity": "error", '
                    '"summary": "wrong behavior", '
                    '"findings": []'
                    "}"
                ),
                stderr="",
                duration_ms=0,
            )
        return super().exec(prompt, cwd, timeout_s)


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-b", "main")
    _git(repo_root, "config", "user.email", "praetor@example.com")
    _git(repo_root, "config", "user.name", "Praetor Tests")
    (repo_root / ".gitignore").write_text(".praetor/\n")
    (repo_root / "README.md").write_text("# Scratch\n")
    _git(repo_root, "add", ".gitignore", "README.md")
    _git(repo_root, "commit", "-m", "initial commit")
    init_workspace(repo_root)
    return repo_root


def test_three_independent_tasks_run_in_parallel(scratch_repo: Path) -> None:
    _write_three_tasks(scratch_repo)
    adapter = SleepRecordingAdapter(sleep_s=1.0)

    drain_queue(scratch_repo, adapter, max_parallel=3)

    assert len(adapter.records) == 3
    latest_start = max(start for _, _, start, _ in adapter.records)
    earliest_end = min(end for _, _, _, end in adapter.records)
    assert latest_start < earliest_end


def test_parallel_drain_creates_worktrees(scratch_repo: Path) -> None:
    _write_three_tasks(scratch_repo)

    drain_queue(scratch_repo, SleepRecordingAdapter(), max_parallel=3)

    worktrees_dir = scratch_repo / ".praetor" / "worktrees"
    task_dirs = [path for path in worktrees_dir.iterdir() if path.is_dir()]
    assert {path.name for path in task_dirs} == {"task-a", "task-b", "task-c"}
    assert all((path / ".praetor-meta.json").is_file() for path in task_dirs)


def test_parallel_drain_with_manual_default_marks_pending_merge(scratch_repo: Path) -> None:
    _write_three_tasks(scratch_repo)

    drain_queue(scratch_repo, SleepRecordingAdapter(), max_parallel=3)

    assert get_task(scratch_repo, "task-a").status is TaskStatus.pending_merge
    assert get_task(scratch_repo, "task-b").status is TaskStatus.pending_merge
    assert get_task(scratch_repo, "task-c").status is TaskStatus.pending_merge


def test_parallel_drain_with_auto_override_merges_and_marks_done(
    scratch_repo: Path,
) -> None:
    _write_three_tasks(scratch_repo)

    drain_queue(
        scratch_repo,
        SleepRecordingAdapter(),
        max_parallel=3,
        merge_strategy="auto",
    )

    assert get_task(scratch_repo, "task-a").status is TaskStatus.done
    assert get_task(scratch_repo, "task-b").status is TaskStatus.done
    assert get_task(scratch_repo, "task-c").status is TaskStatus.done
    assert _branch_contains(scratch_repo, "main", "praetor/task-a")
    assert _branch_contains(scratch_repo, "main", "praetor/task-b")
    assert _branch_contains(scratch_repo, "main", "praetor/task-c")


def test_auto_merge_marks_merge_failed_when_post_merge_verify_fails(
    scratch_repo: Path,
) -> None:
    _write_task(
        scratch_repo,
        _make_task(
            "task-a",
            offset=0,
            verify=(
                'python -c "from pathlib import Path; '
                "raise SystemExit(1 if Path.cwd().name == 'repo' else 0)\""
            ),
        ),
    )

    drain_queue(
        scratch_repo,
        FileEditingAdapter({"task-a": {"task-a.txt": "task-a\n"}}),
        max_parallel=2,
        merge_strategy="auto",
    )

    assert get_task(scratch_repo, "task-a").status is TaskStatus.merge_failed
    assert _branch_contains(scratch_repo, "main", "praetor/task-a")
    log_text = (scratch_repo / ".praetor" / "logs" / "task-a.log").read_text()
    assert "Post-merge verify command:" in log_text
    assert "post-merge verify failed" in log_text


def test_parallel_drain_with_per_task_auto_field_merges(scratch_repo: Path) -> None:
    _write_task(scratch_repo, _make_task("task-a", offset=0))
    _write_task(scratch_repo, _make_task("task-b", offset=1, merge_strategy="auto"))
    _write_task(scratch_repo, _make_task("task-c", offset=2))

    drain_queue(scratch_repo, SleepRecordingAdapter(), max_parallel=3)

    assert get_task(scratch_repo, "task-a").status is TaskStatus.pending_merge
    assert get_task(scratch_repo, "task-b").status is TaskStatus.done
    assert get_task(scratch_repo, "task-c").status is TaskStatus.pending_merge
    assert _branch_contains(scratch_repo, "main", "praetor/task-b")


def test_review_failure_overrides_auto_merge(scratch_repo: Path) -> None:
    _write_task(
        scratch_repo, _make_task("task-a", offset=0, merge_strategy="auto", review="strict")
    )

    drain_queue(
        scratch_repo,
        ReviewRejectingFileEditingAdapter({"task-a": {"task-a.txt": "task-a\n"}}),
        max_parallel=2,
    )

    assert get_task(scratch_repo, "task-a").status is TaskStatus.review_failed
    assert not (scratch_repo / "task-a.txt").exists()
    log_text = (scratch_repo / ".praetor" / "logs" / "task-a.log").read_text()
    assert "verdict: needs_revision" in log_text


def test_parallel_ok_false_runs_alone(scratch_repo: Path) -> None:
    _write_task(scratch_repo, _make_task("task-a", offset=0))
    _write_task(scratch_repo, _make_task("task-b", offset=1, parallel_ok=False))
    _write_task(scratch_repo, _make_task("task-c", offset=2))
    adapter = SleepRecordingAdapter(sleep_s=0.2)

    drain_queue(scratch_repo, adapter, max_parallel=3)

    records = {task_id: (start, end) for task_id, _, start, end in adapter.records}
    false_start, false_end = records["task-b"]
    for task_id in {"task-a", "task-c"}:
        start, end = records[task_id]
        assert end <= false_start or start >= false_end


def test_worktree_collision_recovery(scratch_repo: Path) -> None:
    create_worktree("task-X", scratch_repo)
    _write_task(scratch_repo, _make_task("task-X", offset=0))
    _write_task(scratch_repo, _make_task("task-Y", offset=1))

    drain_queue(scratch_repo, SleepRecordingAdapter(), max_parallel=2)

    assert get_task(scratch_repo, "task-X").status is TaskStatus.failed
    assert get_task(scratch_repo, "task-Y").status is TaskStatus.pending_merge
    log_text = (scratch_repo / ".praetor" / "logs" / "task-X.log").read_text()
    assert "Worktree collision for task-X" in log_text


def test_worktree_collision_does_not_consume_iteration_budget(scratch_repo: Path) -> None:
    create_worktree("task-X", scratch_repo)
    _write_task(scratch_repo, _make_task("task-X", offset=0))
    _write_task(scratch_repo, _make_task("task-Y", offset=1))

    drain_queue(
        scratch_repo,
        SleepRecordingAdapter(),
        max_parallel=2,
        max_iterations=1,
    )

    assert get_task(scratch_repo, "task-X").status is TaskStatus.failed
    assert get_task(scratch_repo, "task-Y").status is TaskStatus.pending_merge


def test_merge_failed_does_not_block_independent_siblings(scratch_repo: Path) -> None:
    (scratch_repo / "shared.txt").write_text("base\n")
    _git(scratch_repo, "add", "shared.txt")
    _git(scratch_repo, "commit", "-m", "add shared")
    _write_task(scratch_repo, _make_task("task-a", offset=0))
    _write_task(scratch_repo, _make_task("task-b", offset=1))
    _write_task(scratch_repo, _make_task("task-c", offset=2))

    drain_queue(
        scratch_repo,
        FileEditingAdapter(
            {
                "task-a": {"shared.txt": "task-a\n"},
                "task-b": {"shared.txt": "task-b\n"},
                "task-c": {"task-c.txt": "task-c\n"},
            }
        ),
        max_parallel=3,
        merge_strategy="auto",
    )

    conflict_statuses = {
        get_task(scratch_repo, "task-a").status,
        get_task(scratch_repo, "task-b").status,
    }
    assert conflict_statuses == {TaskStatus.done, TaskStatus.merge_failed}
    assert get_task(scratch_repo, "task-c").status is TaskStatus.done


def test_pending_merge_blocks_dependent_task(scratch_repo: Path) -> None:
    _write_task(scratch_repo, _make_task("parent", offset=0))
    _write_task(scratch_repo, _make_task("child", offset=1, depends_on=["parent"]))

    drain_queue(scratch_repo, SleepRecordingAdapter(), max_parallel=2)

    assert get_task(scratch_repo, "parent").status is TaskStatus.pending_merge
    assert get_task(scratch_repo, "child").status is TaskStatus.pending


def _write_three_tasks(repo_root: Path) -> None:
    _write_task(repo_root, _make_task("task-a", offset=0))
    _write_task(repo_root, _make_task("task-b", offset=1))
    _write_task(repo_root, _make_task("task-c", offset=2))


def _make_task(
    task_id: str,
    *,
    offset: int = 0,
    parallel_ok: bool = True,
    depends_on: list[str] | None = None,
    merge_strategy: str = "manual",
    verify: str = "true",
    review: str = "off",
) -> Task:
    return Task(
        id=task_id,
        status=TaskStatus.pending,
        depends_on=depends_on or [],
        parallel_ok=parallel_ok,
        merge_strategy=merge_strategy,
        verify=verify,
        review=review,
        created=datetime(2026, 6, 8, 12, 0, tzinfo=UTC) + timedelta(minutes=offset),
        body=f"# {task_id}\n\nTASK_ID: {task_id}\n",
    )


def _write_task(repo_root: Path, task: Task) -> None:
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")


def _task_id_from_prompt(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith("TASK_ID: "):
            return line.removeprefix("TASK_ID: ")
    raise AssertionError("prompt missing TASK_ID marker")


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.rstrip("\n")


def _branch_contains(repo_root: Path, branch: str, contains: str) -> bool:
    return branch in _git(repo_root, "branch", "--contains", contains)
