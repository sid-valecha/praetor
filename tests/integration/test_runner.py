from datetime import UTC, datetime, timedelta
from pathlib import Path

from praetor.adapters import MockAdapter
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskResult, TaskStatus
from praetor.runner import drain_queue, render_task_prompt, run_once
from praetor.run_history import latest_run
from praetor.state import get_task, init_workspace, update_task_status


class SequenceAdapter:
    name = "sequence"

    def __init__(self, outputs: list[TaskResult]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        self.prompts.append(prompt)
        return self.outputs.pop(0)


class ReviewRoutingAdapter:
    name = "executor"

    def __init__(self) -> None:
        self.executor_prompts: list[str] = []
        self.review_prompts: list[str] = []
        self.review_adapter = _ReviewOnlyAdapter(self.review_prompts)

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        self.executor_prompts.append(prompt)
        return TaskResult(exit_code=0, stdout="implemented\n", stderr="", duration_ms=1)

    def for_review(self) -> "_ReviewOnlyAdapter":
        return self.review_adapter


class RetryReviewAdapter:
    name = "retry-executor"

    def __init__(
        self,
        *,
        executor_results: list[TaskResult],
        review_results: list[TaskResult],
    ) -> None:
        self.executor_results = executor_results
        self.review_results = review_results
        self.executor_prompts: list[str] = []
        self.review_prompts: list[str] = []
        self.review_adapter = _RetryReviewOnlyAdapter(self)

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        self.executor_prompts.append(prompt)
        return self.executor_results.pop(0)

    def for_review(self) -> "_RetryReviewOnlyAdapter":
        return self.review_adapter


class _RetryReviewOnlyAdapter:
    name = "retry-reviewer"

    def __init__(self, parent: RetryReviewAdapter) -> None:
        self.parent = parent

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        self.parent.review_prompts.append(prompt)
        return self.parent.review_results.pop(0)


class _ReviewOnlyAdapter:
    name = "reviewer"

    def __init__(self, prompts: list[str]) -> None:
        self.prompts = prompts

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        self.prompts.append(prompt)
        return _review_result("pass")


def make_task(
    task_id: str,
    *,
    offset: int = 0,
    status: TaskStatus = TaskStatus.pending,
    depends_on: list[str] | None = None,
    verify: str | None = None,
    review: str = "off",
) -> Task:
    return Task(
        id=task_id,
        status=status,
        depends_on=depends_on or [],
        verify=verify,
        review=review,
        created=datetime(2026, 6, 7, 12, 0, tzinfo=UTC) + timedelta(minutes=offset),
        body=f"# Task {task_id}\n",
    )


def write_task(repo_root: Path, task: Task) -> None:
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")


def test_run_once_returns_false_on_empty_queue(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    assert run_once(tmp_path, MockAdapter()) is False


def test_render_task_prompt_includes_non_interactive_edit_authority() -> None:
    task = make_task("A")
    task.body = "# Update docs\n\nEdit Handoff.md."

    prompt = render_task_prompt(
        task,
        context="Project context.",
        review_failure={
            "verdict": "needs_revision",
            "severity": "error",
            "summary": "Docs were not edited.",
            "findings": [],
        },
    )

    authority = "Praetor runs are non-interactive."
    assert authority in prompt
    assert prompt.index(authority) < prompt.index("Latest reviewer feedback")
    assert prompt.index(authority) < prompt.index("# Update docs")


def test_run_once_processes_one_task(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A"))

    run_once(tmp_path, MockAdapter(exit_code=0))

    assert get_task(tmp_path, "A").status is TaskStatus.done


def test_run_once_returns_true_when_task_processed(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A"))

    assert run_once(tmp_path, MockAdapter(exit_code=0)) is True


def test_drain_queue_linear_three_tasks(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", offset=0))
    write_task(tmp_path, make_task("B", offset=1, depends_on=["A"]))
    write_task(tmp_path, make_task("C", offset=2, depends_on=["B"]))

    drain_queue(tmp_path, MockAdapter(exit_code=0))

    assert get_task(tmp_path, "A").status is TaskStatus.done
    assert get_task(tmp_path, "B").status is TaskStatus.done
    assert get_task(tmp_path, "C").status is TaskStatus.done


def test_drain_queue_max_parallel_one_does_not_create_worktrees(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A"))

    drain_queue(tmp_path, MockAdapter(exit_code=0), max_parallel=1)

    worktrees_dir = tmp_path / ".praetor" / "worktrees"
    assert not worktrees_dir.exists() or list(worktrees_dir.iterdir()) == []
    assert get_task(tmp_path, "A").status is TaskStatus.done


def test_task_failure_marks_failed_and_propagates_blocked(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", offset=0))
    write_task(tmp_path, make_task("B", offset=1, depends_on=["A"]))

    run_once(tmp_path, MockAdapter(exit_code=1))

    assert get_task(tmp_path, "A").status is TaskStatus.failed
    assert get_task(tmp_path, "B").status is TaskStatus.blocked


def test_verify_failure_marks_failed(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", verify="false"))

    run_once(tmp_path, MockAdapter(exit_code=0))

    assert get_task(tmp_path, "A").status is TaskStatus.failed


def test_verify_success_marks_done(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", verify="true"))

    run_once(tmp_path, MockAdapter(exit_code=0))

    assert get_task(tmp_path, "A").status is TaskStatus.done


def test_log_file_written(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A"))

    run_once(tmp_path, MockAdapter(exit_code=0, stdout="task output\n"))

    log_path = tmp_path / ".praetor" / "logs" / "A.log"
    assert log_path.is_file()
    assert log_path.read_text()


def test_resume_skips_done_tasks(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", status=TaskStatus.done))

    drain_queue(tmp_path, MockAdapter(exit_code=1))

    assert get_task(tmp_path, "A").status is TaskStatus.done


def test_drain_queue_writes_run_history(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    config_path = tmp_path / ".praetor" / "config.toml"
    config_path.write_text("max_review_retries = 2\n")
    write_task(tmp_path, make_task("A", verify="true"))

    drain_queue(tmp_path, MockAdapter(exit_code=0))

    run = latest_run(tmp_path)
    assert run is not None
    assert run.status == "completed"
    assert run.task_runs[0].task_id == "A"
    assert run.task_runs[0].status == "done"
    assert run.task_runs[0].verify_exit_code == 0
    assert run.max_review_retries == 2


def test_reviewer_pass_allows_completion(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", verify="true", review="strict"))
    adapter = SequenceAdapter(
        [
            TaskResult(exit_code=0, stdout="implemented\n", stderr="", duration_ms=1),
            _review_result("pass"),
        ]
    )

    drain_queue(tmp_path, adapter)

    assert get_task(tmp_path, "A").status is TaskStatus.done
    run = latest_run(tmp_path)
    assert run.task_runs[0].review.verdict == "pass"
    assert "Praetor review:" in (tmp_path / ".praetor" / "logs" / "A.log").read_text()


def test_reviewer_uses_review_adapter_when_available(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", verify="true", review="strict"))
    adapter = ReviewRoutingAdapter()

    drain_queue(tmp_path, adapter)

    assert get_task(tmp_path, "A").status is TaskStatus.done
    assert len(adapter.executor_prompts) == 1
    assert len(adapter.review_prompts) == 1
    run = latest_run(tmp_path)
    assert run.task_runs[0].adapter == "executor"
    assert run.task_runs[0].review.reviewer_adapter == "reviewer"


def test_reviewer_needs_revision_marks_review_failed_without_blocking_dependents(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", verify="true", review="strict"))
    write_task(tmp_path, make_task("B", offset=1, depends_on=["A"]))
    adapter = SequenceAdapter(
        [
            TaskResult(exit_code=0, stdout="implemented\n", stderr="", duration_ms=1),
            _review_result("needs_revision"),
        ]
    )

    drain_queue(tmp_path, adapter, max_review_retries=0)

    assert get_task(tmp_path, "A").status is TaskStatus.review_failed
    assert get_task(tmp_path, "B").status is TaskStatus.pending
    assert latest_run(tmp_path).task_runs[0].review.verdict == "needs_revision"


def test_reviewer_needs_revision_retries_once_and_injects_feedback(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", verify="true", review="strict"))
    adapter = RetryReviewAdapter(
        executor_results=[
            TaskResult(exit_code=0, stdout="first attempt\n", stderr="", duration_ms=1),
            TaskResult(exit_code=0, stdout="second attempt\n", stderr="", duration_ms=1),
        ],
        review_results=[
            _review_result(
                "needs_revision",
                severity="error",
                summary="missing validation for empty input",
                findings=[
                    {
                        "severity": "error",
                        "file": "validator.py",
                        "line": 12,
                        "message": "empty input still passes",
                        "recommendation": "reject empty input before saving",
                    }
                ],
            ),
            _review_result("pass", summary="fixed"),
        ],
    )

    drain_queue(tmp_path, adapter)

    task = get_task(tmp_path, "A")
    assert task.status is TaskStatus.done
    assert task.retry == 1
    assert len(adapter.executor_prompts) == 2
    retry_prompt = adapter.executor_prompts[1]
    assert "Latest reviewer feedback" in retry_prompt
    assert "verdict: needs_revision" in retry_prompt
    assert "missing validation for empty input" in retry_prompt
    assert "validator.py:12" in retry_prompt
    assert "reject empty input before saving" in retry_prompt
    assert "# Task A" in retry_prompt
    assert "Verify command: true" in retry_prompt


def test_manual_reset_after_pass_does_not_inject_resolved_review_feedback(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", verify="true", review="strict"))
    first_adapter = RetryReviewAdapter(
        executor_results=[
            TaskResult(exit_code=0, stdout="first attempt\n", stderr="", duration_ms=1),
            TaskResult(exit_code=0, stdout="second attempt\n", stderr="", duration_ms=1),
        ],
        review_results=[
            _review_result(
                "needs_revision",
                summary="old criticism",
                findings=[
                    {
                        "severity": "error",
                        "message": "resolved problem",
                        "recommendation": "old recommendation",
                    }
                ],
            ),
            _review_result("pass", summary="fixed"),
        ],
    )
    drain_queue(tmp_path, first_adapter)
    assert get_task(tmp_path, "A").status is TaskStatus.done

    update_task_status(tmp_path, "A", TaskStatus.pending)
    second_adapter = RetryReviewAdapter(
        executor_results=[
            TaskResult(exit_code=0, stdout="fresh attempt\n", stderr="", duration_ms=1),
        ],
        review_results=[
            _review_result("pass", summary="fresh pass"),
        ],
    )
    drain_queue(tmp_path, second_adapter)

    assert get_task(tmp_path, "A").status is TaskStatus.done
    prompt = second_adapter.executor_prompts[0]
    assert "Latest reviewer feedback" not in prompt
    assert "old criticism" not in prompt
    assert "old recommendation" not in prompt


def test_reviewer_retry_exhaustion_leaves_review_failed_with_retry_count(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", verify="true", review="strict"))
    adapter = RetryReviewAdapter(
        executor_results=[
            TaskResult(exit_code=0, stdout="first attempt\n", stderr="", duration_ms=1),
            TaskResult(exit_code=0, stdout="second attempt\n", stderr="", duration_ms=1),
        ],
        review_results=[
            _review_result("needs_revision", summary="first rejection"),
            _review_result("needs_revision", summary="still broken"),
        ],
    )

    drain_queue(tmp_path, adapter)

    task = get_task(tmp_path, "A")
    assert task.status is TaskStatus.review_failed
    assert task.retry == 1
    run = latest_run(tmp_path)
    assert [task_run.status for task_run in run.task_runs] == ["pending", "review_failed"]
    assert run.task_runs[-1].review.summary == "still broken"
    log_text = (tmp_path / ".praetor" / "logs" / "A.log").read_text()
    assert "still broken" in log_text


def test_max_review_retries_zero_disables_automatic_review_retry(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", verify="true", review="strict"))
    adapter = RetryReviewAdapter(
        executor_results=[
            TaskResult(exit_code=0, stdout="first attempt\n", stderr="", duration_ms=1),
        ],
        review_results=[
            _review_result("needs_revision", summary="do not retry"),
        ],
    )

    drain_queue(tmp_path, adapter, max_review_retries=0)

    task = get_task(tmp_path, "A")
    assert task.status is TaskStatus.review_failed
    assert task.retry == 0
    assert len(adapter.executor_prompts) == 1


def test_review_retry_respects_max_iterations(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", verify="true", review="strict"))
    adapter = RetryReviewAdapter(
        executor_results=[
            TaskResult(exit_code=0, stdout="first attempt\n", stderr="", duration_ms=1),
            TaskResult(exit_code=0, stdout="second attempt\n", stderr="", duration_ms=1),
        ],
        review_results=[
            _review_result("needs_revision", summary="retry later"),
            _review_result("pass", summary="fixed later"),
        ],
    )

    drain_queue(tmp_path, adapter, max_iterations=1)

    task = get_task(tmp_path, "A")
    assert task.status is TaskStatus.pending
    assert task.retry == 1
    assert len(adapter.executor_prompts) == 1
    assert latest_run(tmp_path).status == "stopped"

    drain_queue(tmp_path, adapter, max_iterations=2)

    assert get_task(tmp_path, "A").status is TaskStatus.done
    assert len(adapter.executor_prompts) == 2


def test_reviewer_blocked_marks_blocked_and_propagates(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", verify="true", review="strict"))
    write_task(tmp_path, make_task("B", offset=1, depends_on=["A"]))
    adapter = SequenceAdapter(
        [
            TaskResult(exit_code=0, stdout="implemented\n", stderr="", duration_ms=1),
            _review_result("blocked"),
        ]
    )

    drain_queue(tmp_path, adapter)

    assert get_task(tmp_path, "A").status is TaskStatus.blocked
    assert get_task(tmp_path, "B").status is TaskStatus.blocked


def test_max_iterations_stops_dispatching_new_tasks(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    write_task(tmp_path, make_task("A", offset=0))
    write_task(tmp_path, make_task("B", offset=1))

    drain_queue(tmp_path, MockAdapter(exit_code=0), max_iterations=1)

    assert get_task(tmp_path, "A").status is TaskStatus.done
    assert get_task(tmp_path, "B").status is TaskStatus.pending
    assert latest_run(tmp_path).status == "stopped"


def _review_result(
    verdict: str,
    *,
    severity: str = "info",
    summary: str = "review summary",
    findings: list[dict[str, object]] | None = None,
) -> TaskResult:
    findings_json = "[]"
    if findings is not None:
        import json

        findings_json = json.dumps(findings)
    return TaskResult(
        exit_code=0,
        stdout=(
            "{"
            f'"verdict": "{verdict}", '
            f'"severity": "{severity}", '
            f'"summary": "{summary}", '
            f'"findings": {findings_json}'
            "}"
        ),
        stderr="",
        duration_ms=1,
    )
