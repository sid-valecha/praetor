import builtins
import json
import pytest
from hashlib import sha1
from datetime import UTC, datetime, timedelta
from pathlib import Path

from praetor.frontmatter import dump_task
from praetor.maintain import (
    MaintainItem,
    MaintainScan,
    _default_github_provider,
    _extract_context_files,
    write_proposals_to_tasks,
    proposals_from_scan,
    scan,
)
from praetor.models import Task, TaskStatus
from praetor.task_creation import create_task
from praetor.state import init_workspace, list_tasks


def test_scan_returns_empty_items_when_no_tasks(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    result = scan(tmp_path)

    assert isinstance(result, MaintainScan)
    assert result.items == []
    assert result.latest_run is None
    assert result.repo_root == str(tmp_path)


def test_scan_excludes_github_intake_by_default(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    def github_provider(repo_root: Path) -> list[MaintainItem]:
        return [
            MaintainItem(
                source="github:issue:1",
                url="https://github.example/issues/1",
                classification="needs_owner",
                fit="Open GitHub issue needs triage.",
                risk="Untriaged external work can drift from local queue state.",
                proof="Issue #1 is open.",
                blocker="Needs owner triage.",
                next_action="Review issue #1.",
            ),
        ]

    result = scan(tmp_path, github_provider=github_provider)

    assert result.items == []


def test_scan_includes_github_intake_when_enabled(tmp_path: Path) -> None:
    init_workspace(tmp_path)

    def github_provider(repo_root: Path) -> list[MaintainItem]:
        return [
            MaintainItem(
                source="github:pr:22",
                url="https://github.example/pulls/22",
                classification="needs_owner",
                fit="Open GitHub PR has requested changes.",
                risk="Review feedback can block merge readiness.",
                proof="Review decision: CHANGES_REQUESTED.",
                blocker="Requested changes are unresolved.",
                next_action="Inspect PR #22 review comments.",
            ),
        ]

    result = scan(tmp_path, include_github=True, github_provider=github_provider)

    [item] = result.items
    assert item.source == "github:pr:22"
    assert item.classification == "needs_owner"
    assert "CHANGES_REQUESTED" in item.proof


def test_default_github_provider_fallback_uses_github_intake_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "praetor.github_intake":
            raise ModuleNotFoundError(name)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    [item] = list(_default_github_provider(tmp_path))

    assert item.source == "github:intake"
    assert item.classification == "needs_owner"


def test_ready_pending_task_with_verify_is_autonomous(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("ready-a", TaskStatus.pending, verify="pytest -q"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.source == "task:ready-a"
    assert item.url is None
    assert item.classification == "autonomous"
    assert "pytest -q" in item.proof
    assert item.blocker is None
    assert "praetor run" in item.next_action


def test_ready_pending_task_without_verify_is_needs_owner(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("ready-no-verify", TaskStatus.pending, verify=None))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "needs_owner"
    assert item.blocker is not None
    assert "verify" in item.blocker.lower()


def test_pending_task_with_unresolved_deps_is_defer(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("dep", TaskStatus.pending, verify="pytest"))
    _write_task(
        tmp_path,
        _make_task(
            "waiter",
            TaskStatus.pending,
            verify="pytest",
            offset=1,
            depends_on=["dep"],
        ),
    )

    result = scan(tmp_path)

    items = {item.source: item for item in result.items}
    waiter = items["task:waiter"]
    assert waiter.classification == "defer"
    assert waiter.blocker is not None
    assert "dep" in waiter.blocker


def test_review_failed_is_needs_owner_with_review_summary(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("rev-a", TaskStatus.review_failed, verify="pytest"))
    _write_run_with_review(tmp_path, "run-rev", "rev-a")

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "needs_owner"
    assert item.blocker is not None
    assert "Unsafe validation change." in item.blocker
    assert "praetor reset" in item.next_action


def test_merge_failed_is_needs_owner_with_merge_next_action(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("mf", TaskStatus.merge_failed, verify="pytest"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "needs_owner"
    assert "praetor merge" in item.next_action


def test_failed_is_needs_owner_with_reset_next_action(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("boom", TaskStatus.failed, verify="pytest"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "needs_owner"
    assert "praetor reset" in item.next_action


def test_blocked_is_defer(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("blk", TaskStatus.blocked, verify="pytest"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "defer"


def test_cancelled_is_defer(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("cx", TaskStatus.cancelled, verify="pytest"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "defer"


def test_pending_merge_is_needs_owner_with_merge_next_action(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("pm", TaskStatus.pending_merge, verify="pytest"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "needs_owner"
    assert "praetor merge" in item.next_action


def test_running_is_needs_owner_with_cautious_reset_guidance(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("stuck", TaskStatus.running, verify="pytest"))

    result = scan(tmp_path)

    [item] = result.items
    assert item.classification == "needs_owner"
    assert "praetor reset" in item.next_action
    assert "only if no runner is active" in item.next_action
    assert "no liveness check" in item.blocker.lower()


def test_done_tasks_are_excluded(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("d1", TaskStatus.done, verify="pytest"))
    _write_task(
        tmp_path,
        _make_task("p1", TaskStatus.pending, verify="pytest", offset=1),
    )

    result = scan(tmp_path)

    sources = {item.source for item in result.items}
    assert sources == {"task:p1"}


def test_latest_run_metadata_is_populated(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("rev-a", TaskStatus.review_failed, verify="pytest"))
    _write_run_with_review(tmp_path, "run-rev", "rev-a")

    result = scan(tmp_path)

    assert result.latest_run is not None
    assert result.latest_run.id == "run-rev"
    assert result.latest_run.status == "completed"


def test_scan_is_read_only(tmp_path: Path) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("ready-a", TaskStatus.pending, verify="pytest"))
    _write_task(tmp_path, _make_task("rev-a", TaskStatus.review_failed, verify="pytest", offset=1))
    _write_run_with_review(tmp_path, "run-rev", "rev-a")

    praetor_dir = tmp_path / ".praetor"
    before = _snapshot_tree(praetor_dir)

    scan(tmp_path)

    after = _snapshot_tree(praetor_dir)
    assert before == after


def test_proposals_from_scan_extracts_context_and_description_for_github_issue(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    item = MaintainItem(
        source="github:issue:octo-org/octo-repo#101",
        url="https://github.com/octo-org/octo-repo/issues/101",
        classification="needs_owner",
        fit="Open issue requires owner triage.",
        risk="Action not yet taken.",
        proof="Issue #101: Add endpoint docs\nPlease document new endpoint.",
        blocker="Needs owner triage.",
        next_action="Owner: triage issue.",
    )
    result = scan(
        tmp_path,
        include_github=True,
        github_provider=lambda *_args, **_kwargs: [item],
    )

    proposals = proposals_from_scan(result)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.title == "Address issue #101: Add endpoint docs"
    assert proposal.description is not None
    assert proposal.description.startswith(
        "Source: https://github.com/octo-org/octo-repo/issues/101"
    )
    assert proposal.suggested_verify is None
    assert proposal.context_files == []


def test_proposals_from_scan_filters_only_github_needs_owner_items(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    local_item = MaintainItem(
        source="task:ready-a",
        classification="needs_owner",
        fit="Local task with missing verify.",
        risk="Local execution requires owner action.",
        proof="Task ready-a is pending without a verifier.",
        blocker="Missing verify.",
        next_action="Owner: add verify command.",
    )
    github_item = MaintainItem(
        source="github:issue:octo-org/octo-repo#99",
        url="https://github.com/octo-org/octo-repo/issues/99",
        classification="needs_owner",
        fit="Open issue requires owner triage.",
        risk="No owner review has yet been applied to this request.",
        proof="Issue #99: Add caching",
        blocker="Issue is open and user-facing; requires human review.",
        next_action="Owner: triage issue and create praetor task with verification.",
    )
    not_owner_item = MaintainItem(
        source="github:issue:octo-org/octo-repo#98",
        classification="defer",
        fit="Open issue resolved.",
        risk="Already done.",
        proof="Issue #98: Close old thread.",
        blocker=None,
        next_action="No action needed.",
    )

    result = scan(
        tmp_path,
        include_github=True,
        github_provider=lambda *_args, **_kwargs: [local_item, github_item, not_owner_item],
    )

    proposals = proposals_from_scan(result)

    assert len(proposals) == 1
    assert proposals[0].source == github_item.source


def test_proposals_from_scan_preserves_item_order_for_multiple_github_items(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    issue = MaintainItem(
        source="github:issue:octo-org/octo-repo#101",
        url="https://github.com/octo-org/octo-repo/issues/101",
        classification="needs_owner",
        fit="Open issue requires owner triage.",
        risk="No owner review has yet been applied to this request.",
        proof="Issue #101: Add endpoint docs\nPlease document new endpoint.",
        blocker="Needs owner triage.",
        next_action="Owner: triage issue.",
    )
    task = MaintainItem(
        source="task:ready-a",
        classification="needs_owner",
        fit="Local task with missing verify.",
        risk="Local execution requires owner action.",
        proof="Task ready-a is pending without a verifier.",
        blocker="Missing verify.",
        next_action="Owner: add verify command.",
    )
    pr = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has review feedback that needs owner action.",
        risk="Applying changes without review closure can introduce regressions.",
        proof="Pull request #202: Improve docs",
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
    )
    result = scan(
        tmp_path,
        include_github=True,
        github_provider=lambda *_args, **_kwargs: [issue, task, pr],
    )

    proposals = proposals_from_scan(result)

    assert len(proposals) == 2
    assert proposals[0].source == issue.source
    assert proposals[1].source == pr.source
    assert proposals[0].title == "Address issue #101: Add endpoint docs"
    assert proposals[1].title == "Address pull request feedback for #202: Improve docs"


def test_proposals_from_scan_skips_github_intake_diagnostics(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    intake_diagnostic = MaintainItem(
        source="github:intake",
        classification="needs_owner",
        fit="Intake unavailable.",
        risk="GitHub intake could not be loaded.",
        proof="GitHub intake unavailable.",
        blocker="GitHub provider did not return actionable findings.",
        next_action="Fix GitHub intake configuration.",
    )
    issue = MaintainItem(
        source="github:issue:octo-org/octo-repo#101",
        url="https://github.com/octo-org/octo-repo/issues/101",
        classification="needs_owner",
        fit="Open issue requires owner triage.",
        risk="No owner review has yet been applied to this request.",
        proof="Issue #101: Add endpoint docs",
        blocker="Needs owner triage.",
        next_action="Owner: triage issue.",
    )

    result = scan(
        tmp_path,
        include_github=True,
        github_provider=lambda *_args, **_kwargs: [intake_diagnostic, issue],
    )

    proposals = proposals_from_scan(result)

    assert len(proposals) == 1
    assert proposals[0].source == issue.source


def test_proposals_from_scan_skips_pr_review_thread_intake_diagnostics(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    diagnostic = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR review-thread intake is unavailable.",
        risk="Review-thread feedback cannot be inspected.",
        proof=(
            "Pull request #202: Improve docs\n"
            "Review threads unavailable: Resource not accessible by integration"
        ),
        blocker="Review-thread intake is unavailable.",
        next_action="Owner: fix GitHub auth/API access and rerun the intake.",
    )

    result = scan(
        tmp_path,
        include_github=True,
        github_provider=lambda *_args, **_kwargs: [diagnostic],
    )

    proposals = proposals_from_scan(result)

    assert proposals == []


def test_proposals_from_scan_extracts_review_context_file_for_pr_feedback(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    item = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has unresolved review feedback.",
        risk="Reviewer requested changes.",
        proof="Pull request #202: Improve docs\nUnresolved review thread: src/app.py:42 - Please clarify behavior.",
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
    )
    result = scan(
        tmp_path,
        include_github=True,
        github_provider=lambda *_args, **_kwargs: [item],
    )

    proposals = proposals_from_scan(result)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.title == "Address pull request feedback for #202: Improve docs"
    assert proposal.context_files == ["src/app.py"]
    assert proposal.suggested_verify is None
    assert proposal.description is not None
    assert "Open PR has unresolved review feedback." in proposal.description


def test_proposals_from_scan_extracts_extensionless_review_context_files(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    item = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has unresolved review feedback.",
        risk="Reviewer requested changes.",
        proof=(
            "Pull request #202: Improve build docs\n"
            "Unresolved review thread: Dockerfile:12 - Pin package versions.\n"
            "Unresolved review thread: Makefile:8 - Keep targets portable."
        ),
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
    )
    result = scan(
        tmp_path,
        include_github=True,
        github_provider=lambda *_args, **_kwargs: [item],
    )

    proposals = proposals_from_scan(result)

    assert len(proposals) == 1
    assert proposals[0].context_files == ["Dockerfile", "Makefile"]


def test_extract_context_files_ignores_url_fragments() -> None:
    proof = (
        "Unresolved review thread: https://github.com/user/repo/blob/main/src/app.py:42 - "
        "see also README.md:7"
    )

    files = _extract_context_files(proof)

    assert files == ["README.md"]


def test_extract_context_files_includes_extensionless_paths_with_line_numbers() -> None:
    proof = (
        "Unresolved review thread: Dockerfile:12 - Pin package versions.\n"
        "Unresolved review thread: Makefile:8 - Keep target portable.\n"
        "Unresolved review thread: scripts/build:19 - Avoid shell-specific flags."
    )

    files = _extract_context_files(proof)

    assert files == ["Dockerfile", "Makefile", "scripts/build"]


def test_extract_context_files_ignores_bare_url_fragments() -> None:
    proof = "Check github.com/user/repo/blob/main/src/app.py:42 and README.md:7"

    files = _extract_context_files(proof)

    assert files == ["README.md"]


def test_extract_context_files_ignores_prose_timestamps_and_versions() -> None:
    proof = "Meeting moved to 10:30 after Python 3.11 started failing in CI."

    files = _extract_context_files(proof)

    assert files == []


def test_write_proposals_to_tasks_creates_deterministic_id_and_skips_duplicates(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    proposal = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has unresolved review feedback.",
        risk="Reviewer requested changes.",
        proof=(
            "Pull request #202: Improve build docs\n"
            "Unresolved review thread: src/app.py:42 - Please clarify behavior."
        ),
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
        title="Address pull request feedback for #202: Improve build docs",
        description=(
            "Source: https://github.com/octo-org/octo-repo/pull/202\n"
            "Fit: Open PR has unresolved review feedback.\n"
            "Risk: Reviewer requested changes.\n"
            "Proof: Pull request #202: Improve build docs\nUnresolved review thread: "
            "src/app.py:42 - Please clarify behavior.\nBlocker: Open review feedback must be resolved."
        ),
        context_files=["src/app.py"],
        suggested_verify="pytest tests/repo",
    )

    created_first, skipped_first = write_proposals_to_tasks(tmp_path, [proposal])
    assert len(created_first) == 1
    assert skipped_first == []

    created_second, skipped_second = write_proposals_to_tasks(tmp_path, [proposal])
    assert created_second == []
    assert skipped_second == created_first

    tasks = list_tasks(tmp_path)
    assert len(tasks) == 1
    assert tasks[0].context_files == ["src/app.py"]


def test_write_proposals_to_tasks_skips_duplicate_after_title_changes(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    proposal = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has unresolved review feedback.",
        risk="Reviewer requested changes.",
        proof="Pull request #202: Improve build docs",
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
        title="Address pull request feedback for #202: Improve build docs",
    )
    renamed = proposal.model_copy(
        update={"title": "Address pull request feedback for #202: Renamed PR title"}
    )

    created_first, skipped_first = write_proposals_to_tasks(tmp_path, [proposal])
    created_second, skipped_second = write_proposals_to_tasks(tmp_path, [renamed])

    assert len(created_first) == 1
    assert skipped_first == []
    assert created_second == []
    assert skipped_second == created_first
    assert len(list_tasks(tmp_path)) == 1


def test_write_proposals_to_tasks_creates_new_task_for_new_feedback(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    first_feedback = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has unresolved review feedback.",
        risk="Reviewer requested changes.",
        proof=(
            "Pull request #202: Improve docs\n"
            "Unresolved review thread: src/app.py:42 - Please clarify."
        ),
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
        title="Address pull request feedback for #202: Improve docs",
        description="Address first feedback.",
        context_files=["src/app.py"],
    )
    second_feedback = first_feedback.model_copy(
        update={
            "proof": (
                "Pull request #202: Improve docs\n"
                "Unresolved review thread: tests/test_app.py:7 - Add coverage."
            ),
            "description": "Address second feedback.",
            "context_files": ["tests/test_app.py"],
        }
    )

    created_first, skipped_first = write_proposals_to_tasks(tmp_path, [first_feedback])
    created_second, skipped_second = write_proposals_to_tasks(tmp_path, [second_feedback])

    assert skipped_first == skipped_second == []
    assert len(created_first) == len(created_second) == 1
    assert created_first != created_second
    assert len(list_tasks(tmp_path)) == 2


def test_write_proposals_to_tasks_skips_duplicate_after_pr_body_changes(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    proposal = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has unresolved review feedback.",
        risk="Reviewer requested changes.",
        proof=(
            "Pull request #202: Improve docs\n"
            "Original PR description.\n"
            "Unresolved review thread: src/app.py:42 - Please clarify."
        ),
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
        title="Address pull request feedback for #202: Improve docs",
        description="Address first feedback.",
        context_files=["src/app.py"],
    )
    body_edited = proposal.model_copy(
        update={
            "proof": (
                "Pull request #202: Improve docs\n"
                "Edited PR description.\n"
                "Unresolved review thread: src/app.py:42 - Please clarify."
            ),
            "description": "Address same feedback after body edit.",
        }
    )

    created_first, skipped_first = write_proposals_to_tasks(tmp_path, [proposal])
    created_second, skipped_second = write_proposals_to_tasks(tmp_path, [body_edited])

    assert len(created_first) == 1
    assert skipped_first == []
    assert created_second == []
    assert skipped_second == created_first
    assert len(list_tasks(tmp_path)) == 1


def test_write_proposals_to_tasks_skips_legacy_source_url_duplicate(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    source = "github:pull_request:octo-org/octo-repo#202"
    url = "https://github.com/octo-org/octo-repo/pull/202"
    legacy_digest = sha1(f"{source}|{url}".encode("utf-8")).hexdigest()[:8]
    legacy_task_id = f"maintain-github-pull-request-octo-org-octo-repo-202-{legacy_digest}"
    create_task(
        repo_root=tmp_path,
        title="Address pull request feedback for #202: Improve docs",
        depends_on=[],
        task_id=legacy_task_id,
        body=(
            "# Address pull request feedback for #202: Improve docs\n\n"
            "Proof: Pull request #202: Improve docs\n"
            "Unresolved review thread: src/app.py:42 - Please clarify."
        ),
    )
    proposal = MaintainItem(
        source=source,
        url=url,
        classification="needs_owner",
        fit="Open PR has unresolved review feedback.",
        risk="Reviewer requested changes.",
        proof=(
            "Pull request #202: Improve docs\n"
            "Unresolved review thread: src/app.py:42 - Please clarify."
        ),
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
        title="Address pull request feedback for #202: Improve docs",
        description="Address same feedback.",
        context_files=["src/app.py"],
    )

    created, skipped = write_proposals_to_tasks(tmp_path, [proposal])

    assert created == []
    assert skipped == [legacy_task_id]
    assert len(list_tasks(tmp_path)) == 1


def test_write_proposals_to_tasks_skips_duplicate_latest_review_after_pr_body_changes(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    proposal = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has requested changes.",
        risk="Reviewer requested changes.",
        proof=(
            "Pull request #202: Improve docs\n"
            "Original PR description.\n"
            "Latest review: CHANGES_REQUESTED - Please add coverage."
        ),
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
        title="Address pull request feedback for #202: Improve docs",
        description="Address first feedback.",
    )
    body_edited = proposal.model_copy(
        update={
            "proof": (
                "Pull request #202: Improve docs\n"
                "Edited PR description.\n"
                "Latest review: CHANGES_REQUESTED - Please add coverage."
            ),
            "description": "Address same feedback after body edit.",
        }
    )

    created_first, skipped_first = write_proposals_to_tasks(tmp_path, [proposal])
    created_second, skipped_second = write_proposals_to_tasks(tmp_path, [body_edited])

    assert len(created_first) == 1
    assert skipped_first == []
    assert created_second == []
    assert skipped_second == created_first
    assert len(list_tasks(tmp_path)) == 1


def test_write_proposals_to_tasks_distinguishes_multiline_review_feedback(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    auth_feedback = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has requested changes.",
        risk="Reviewer requested changes.",
        proof=(
            "Pull request #202: Improve docs\n"
            "Latest review: CHANGES_REQUESTED - Please add tests\n"
            "for auth."
        ),
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
        title="Address pull request feedback for #202: Improve docs",
        description="Address auth feedback.",
    )
    billing_feedback = auth_feedback.model_copy(
        update={
            "proof": (
                "Pull request #202: Improve docs\n"
                "Latest review: CHANGES_REQUESTED - Please add tests\n"
                "for billing."
            ),
            "description": "Address billing feedback.",
        }
    )

    created_first, skipped_first = write_proposals_to_tasks(tmp_path, [auth_feedback])
    created_second, skipped_second = write_proposals_to_tasks(tmp_path, [billing_feedback])

    assert skipped_first == skipped_second == []
    assert len(created_first) == len(created_second) == 1
    assert created_first != created_second
    assert len(list_tasks(tmp_path)) == 2


def test_write_proposals_to_tasks_skips_remaining_feedback_already_in_existing_task(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    combined_feedback = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has unresolved review feedback.",
        risk="Reviewer requested changes.",
        proof=(
            "Pull request #202: Improve docs\n"
            "Unresolved review thread: src/app.py:42 - Please clarify.\n"
            "Unresolved review thread: tests/test_app.py:7 - Add coverage."
        ),
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
        title="Address pull request feedback for #202: Improve docs",
        description=(
            "Source: https://github.com/octo-org/octo-repo/pull/202\n"
            "Proof: Pull request #202: Improve docs\n"
            "Unresolved review thread: src/app.py:42 - Please clarify.\n"
            "Unresolved review thread: tests/test_app.py:7 - Add coverage."
        ),
    )
    remaining_feedback = combined_feedback.model_copy(
        update={
            "proof": (
                "Pull request #202: Improve docs\n"
                "Unresolved review thread: tests/test_app.py:7 - Add coverage."
            ),
            "description": "Address remaining feedback.",
        }
    )

    created_first, skipped_first = write_proposals_to_tasks(tmp_path, [combined_feedback])
    created_second, skipped_second = write_proposals_to_tasks(tmp_path, [remaining_feedback])

    assert len(created_first) == 1
    assert skipped_first == []
    assert created_second == []
    assert skipped_second == created_first
    assert len(list_tasks(tmp_path)) == 1


def test_write_proposals_to_tasks_skips_remaining_feedback_in_same_batch(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    combined_feedback = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has unresolved review feedback.",
        risk="Reviewer requested changes.",
        proof=(
            "Pull request #202: Improve docs\n"
            "Unresolved review thread: src/app.py:42 - Please clarify.\n"
            "Unresolved review thread: tests/test_app.py:7 - Add coverage."
        ),
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
        title="Address pull request feedback for #202: Improve docs",
        description=(
            "Source: https://github.com/octo-org/octo-repo/pull/202\n"
            "Proof: Pull request #202: Improve docs\n"
            "Unresolved review thread: src/app.py:42 - Please clarify.\n"
            "Unresolved review thread: tests/test_app.py:7 - Add coverage."
        ),
    )
    remaining_feedback = combined_feedback.model_copy(
        update={
            "proof": (
                "Pull request #202: Improve docs\n"
                "Unresolved review thread: tests/test_app.py:7 - Add coverage."
            ),
            "description": (
                "Source: https://github.com/octo-org/octo-repo/pull/202\n"
                "Proof: Pull request #202: Improve docs\n"
                "Unresolved review thread: tests/test_app.py:7 - Add coverage."
            ),
        }
    )

    created, skipped = write_proposals_to_tasks(
        tmp_path,
        [combined_feedback, remaining_feedback],
    )

    assert len(created) == 1
    assert skipped == created
    assert len(list_tasks(tmp_path)) == 1


def test_write_proposals_to_tasks_does_not_skip_same_feedback_for_different_pr(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    create_task(
        repo_root=tmp_path,
        title="Address pull request feedback for #201: Improve docs",
        depends_on=[],
        task_id="existing-pr-201-feedback",
        body=(
            "# Address pull request feedback for #201: Improve docs\n\n"
            "Source: https://github.com/octo-org/octo-repo/pull/201\n"
            "Proof: Pull request #201: Improve docs\n"
            "Unresolved review thread: src/app.py:42 - Please clarify."
        ),
    )
    proposal = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has unresolved review feedback.",
        risk="Reviewer requested changes.",
        proof=(
            "Pull request #202: Improve docs\n"
            "Unresolved review thread: src/app.py:42 - Please clarify."
        ),
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
        title="Address pull request feedback for #202: Improve docs",
        description=(
            "Source: https://github.com/octo-org/octo-repo/pull/202\n"
            "Proof: Pull request #202: Improve docs\n"
            "Unresolved review thread: src/app.py:42 - Please clarify."
        ),
    )

    created, skipped = write_proposals_to_tasks(tmp_path, [proposal])

    assert len(created) == 1
    assert skipped == []
    assert len(list_tasks(tmp_path)) == 2


def test_write_proposals_to_tasks_writes_title_only_issue_with_existing_task(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)
    create_task(
        repo_root=tmp_path,
        title="Existing unrelated task",
        depends_on=[],
        task_id="existing-unrelated-task",
        body="# Existing unrelated task\n",
    )
    proposal = MaintainItem(
        source="github:issue:octo-org/octo-repo#123",
        url="https://github.com/octo-org/octo-repo/issues/123",
        classification="needs_owner",
        fit="Open issue requires owner triage.",
        risk="No owner review has yet been applied to this request.",
        proof="Issue #123: Add docs",
        blocker="Issue is open and user-facing; requires human review.",
        next_action="Owner: triage issue and create praetor task with verification.",
        title="Address issue #123: Add docs",
        description="Address issue.",
    )

    created, skipped = write_proposals_to_tasks(tmp_path, [proposal])

    assert len(created) == 1
    assert skipped == []
    assert len(list_tasks(tmp_path)) == 2


def test_create_task_refuses_to_overwrite_existing_task_with_explicit_id(
    tmp_path: Path,
) -> None:
    init_workspace(tmp_path)

    create_task(repo_root=tmp_path, title="Base", depends_on=[], task_id="same-id")

    with pytest.raises(ValueError, match="Task already exists: same-id"):
        create_task(repo_root=tmp_path, title="Overwrite", depends_on=[], task_id="same-id")


def _snapshot_tree(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            snapshot[str(path.relative_to(root))] = path.read_text()
    return snapshot


def _make_task(
    task_id: str,
    status: TaskStatus,
    *,
    verify: str | None = "pytest",
    offset: int = 0,
    depends_on: list[str] | None = None,
) -> Task:
    return Task(
        id=task_id,
        status=status,
        depends_on=depends_on or [],
        verify=verify,
        created=datetime(2026, 6, 14, 12, 0, tzinfo=UTC) + timedelta(minutes=offset),
        body=f"# {task_id}\n",
    )


def _write_task(repo_root: Path, task: Task) -> None:
    dump_task(task, repo_root / ".praetor" / "tasks" / f"{task.id}.md")


def _write_run_with_review(repo_root: Path, run_id: str, task_id: str) -> None:
    path = repo_root / ".praetor" / "runs" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": run_id,
                "status": "completed",
                "started_at": "2026-06-12T12:00:00Z",
                "finished_at": "2026-06-12T12:01:00Z",
                "max_parallel": 1,
                "base_branch": "main",
                "merge_strategy": None,
                "max_review_retries": 1,
                "task_runs": [
                    {
                        "task_id": task_id,
                        "status": "review_failed",
                        "started_at": "2026-06-12T12:00:10Z",
                        "finished_at": "2026-06-12T12:00:20Z",
                        "adapter": "mock",
                        "verify_command": "pytest",
                        "agent_exit_code": 0,
                        "verify_exit_code": 0,
                        "merge_status": None,
                        "detail": "review needs revision",
                        "review": {
                            "verdict": "needs_revision",
                            "severity": "error",
                            "summary": "Unsafe validation change.",
                            "findings": [
                                {
                                    "severity": "error",
                                    "file": "src/app.py",
                                    "line": 12,
                                    "message": "Input validation can be bypassed.",
                                    "recommendation": "Validate before writing.",
                                }
                            ],
                            "reviewer_adapter": "mock-reviewer",
                            "started_at": "2026-06-12T12:00:15Z",
                            "finished_at": "2026-06-12T12:00:18Z",
                            "duration_ms": 3000,
                        },
                    }
                ],
            }
        )
    )
