import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from click import unstyle
from typer.testing import CliRunner

from praetor.cli import app
from praetor.maintain import MaintainItem
from praetor.adapters.mock import MockAdapter
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskStatus
from praetor.pr_loop_state import PRLoopStateResult
from praetor.state import init_workspace, list_tasks

runner = CliRunner()

REQUIRED_ITEM_KEYS = {
    "source",
    "url",
    "classification",
    "fit",
    "risk",
    "proof",
    "blocker",
    "next_action",
}


def test_maintain_requires_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["maintain", "--once"])

    assert result.exit_code != 0
    assert ".praetor" in result.output


def test_maintain_requires_explicit_once_flag(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["maintain"])

    assert result.exit_code != 0
    assert "praetor maintain currently requires" in result.output


def test_maintain_once_exits_zero_with_empty_workspace(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["maintain", "--once"])

    assert result.exit_code == 0


def test_maintain_once_text_groups_items_by_classification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("ready-a", TaskStatus.pending, verify="pytest"))
    _write_task(tmp_path, _make_task("blk", TaskStatus.blocked, verify="pytest", offset=1))
    _write_task(
        tmp_path,
        _make_task("rev-a", TaskStatus.review_failed, verify="pytest", offset=2),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["maintain", "--once"])

    assert result.exit_code == 0
    assert "Autonomous" in result.output
    assert "Needs owner" in result.output
    assert "Defer" in result.output
    assert "task:ready-a" in result.output
    assert "task:rev-a" in result.output
    assert "task:blk" in result.output


def test_maintain_once_json_emits_item_list(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("ready-a", TaskStatus.pending, verify="pytest"))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["maintain", "--once", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "items" in payload
    assert "repo_root" in payload
    assert "latest_run" in payload
    [item] = payload["items"]
    assert REQUIRED_ITEM_KEYS - set(item) == set()
    assert item["classification"] == "autonomous"
    assert item["source"] == "task:ready-a"


def test_maintain_once_does_not_request_github_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls: list[bool] = []

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del github_pr, github_issue
        calls.append(include_github)
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(repo_root), items=[])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(app, ["maintain", "--once", "--json"])

    assert result.exit_code == 0
    assert calls == [False]


def test_maintain_once_github_requests_github_intake(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls: list[bool] = []

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del github_pr, github_issue
        calls.append(include_github)
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(repo_root), items=[])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(app, ["maintain", "--once", "--github", "--json"])

    assert result.exit_code == 0
    assert calls == [True]


def test_maintain_once_github_pr_requests_focused_pr_intake(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[bool, int | None, int | None]] = []

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        calls.append((include_github, github_pr, github_issue))
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(repo_root), items=[])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(app, ["maintain", "--once", "--github-pr", "22", "--json"])

    assert result.exit_code == 0
    assert calls == [(True, 22, None)]


def test_maintain_once_github_pr_json_includes_loop_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        assert include_github is True
        assert github_pr == 22
        assert github_issue is None
        from praetor.maintain import MaintainScan

        return MaintainScan(
            repo_root=str(repo_root),
            items=[],
            github_pr_loop_state=PRLoopStateResult(
                state="needs_repair",
                failing_checks=["Failing check: ci (conclusion=failure)."],
            ),
        )

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(app, ["maintain", "--once", "--github-pr", "22", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["github_pr_loop_state"]["state"] == "needs_repair"
    assert payload["github_pr_loop_state"]["failing_checks"] == [
        "Failing check: ci (conclusion=failure)."
    ]


def test_maintain_once_github_pr_text_prints_loop_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del include_github, github_pr, github_issue
        from praetor.maintain import MaintainScan

        return MaintainScan(
            repo_root=str(repo_root),
            items=[],
            github_pr_loop_state=PRLoopStateResult(
                state="waiting",
                waiting_review_items=["Review is still required."],
                pending_checks=["Pending check: ci (status=queued)."],
            ),
        )

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(app, ["maintain", "--once", "--github-pr", "22"])

    assert result.exit_code == 0
    assert "PR loop state: waiting" in result.output
    assert "Review is still required." in result.output
    assert "Pending check: ci" in result.output


def test_maintain_once_github_issue_requests_focused_issue_intake(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[bool, int | None, int | None]] = []

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        calls.append((include_github, github_pr, github_issue))
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(repo_root), items=[])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(app, ["maintain", "--once", "--github-issue", "17", "--json"])

    assert result.exit_code == 0
    assert calls == [(True, None, 17)]


def test_maintain_once_rejects_github_pr_and_issue_together(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["maintain", "--once", "--github-pr", "22", "--github-issue", "17"],
    )

    assert result.exit_code != 0
    assert "Choose only one" in result.output


def test_maintain_once_is_read_only(tmp_path: Path, monkeypatch) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("ready-a", TaskStatus.pending, verify="pytest"))
    monkeypatch.chdir(tmp_path)

    praetor_dir = tmp_path / ".praetor"
    before = _snapshot_tree(praetor_dir)

    result = runner.invoke(app, ["maintain", "--once"])

    assert result.exit_code == 0
    after = _snapshot_tree(praetor_dir)
    assert before == after


def test_maintain_once_with_propose_tasks_outputs_task_shape_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    proposal = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has unresolved review feedback.",
        risk="Review requested changes.",
        proof="Pull request #202: Improve docs\nUnresolved review thread: src/app.py:42 - Please clarify.",
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
    )

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del repo_root, github_pr, github_issue
        assert include_github
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(tmp_path), items=[proposal])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(app, ["maintain", "--once", "--github", "--propose-tasks"])

    assert result.exit_code == 0
    assert "title:" in result.output.lower()
    assert "description:" in result.output.lower()
    assert "src/app.py" in result.output


def test_maintain_once_with_propose_tasks_json_outputs_extended_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    proposal = MaintainItem(
        source="github:issue:octo-org/octo-repo#101",
        url="https://github.com/octo-org/octo-repo/issues/101",
        classification="needs_owner",
        fit="Open issue requires owner triage.",
        risk="Needs owner triage.",
        proof="Issue #101: Add endpoint docs",
        blocker="Needs owner triage.",
        next_action="Owner: triage issue.",
    )

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del repo_root, github_pr, github_issue
        assert include_github
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(tmp_path), items=[proposal])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(
        app,
        ["maintain", "--once", "--github", "--propose-tasks", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["items"]
    item = payload["items"][0]
    for required in ["title", "description", "context_files", "suggested_verify"]:
        assert required in item
    assert item["title"] == "Address issue #101: Add endpoint docs"
    assert item["description"].startswith(
        "Source: https://github.com/octo-org/octo-repo/issues/101"
    )


def test_maintain_once_with_propose_tasks_json_includes_extensionless_context_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    proposal = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has unresolved review feedback.",
        risk="Review requested changes.",
        proof=(
            "Pull request #202: Improve build docs\n"
            "Unresolved review thread: Dockerfile:12 - Pin package versions."
        ),
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
    )

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del repo_root, github_pr, github_issue
        assert include_github
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(tmp_path), items=[proposal])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(
        app,
        ["maintain", "--once", "--github", "--propose-tasks", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    [item] = payload["items"]
    assert item["context_files"] == ["Dockerfile"]


def test_maintain_once_with_propose_tasks_respects_github_pr_filter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[bool, int | None, int | None]] = []

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        calls.append((include_github, github_pr, github_issue))
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(repo_root), items=[])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(
        app,
        ["maintain", "--once", "--github-pr", "88", "--propose-tasks", "--json"],
    )

    assert result.exit_code == 0
    assert calls == [(True, 88, None)]


def test_maintain_once_with_propose_tasks_includes_pr_loop_state_repair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        assert include_github is True
        assert github_pr == 88
        assert github_issue is None
        from praetor.maintain import MaintainScan

        return MaintainScan(
            repo_root=str(repo_root),
            github_pr_number=88,
            github_pr_loop_state=PRLoopStateResult(
                state="needs_repair",
                actionable_review_items=[
                    "Unresolved review thread: src/app.py:42 - Please clarify."
                ],
                failing_checks=["Failing check: test (conclusion=failure)."],
            ),
        )

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(
        app,
        ["maintain", "--once", "--github-pr", "88", "--propose-tasks", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    [item] = payload["items"]
    assert item["source"] == "github:pull_request:current#88"
    assert item["title"] == "Address pull request feedback for #88: PR loop repair"
    assert item["context_files"] == ["src/app.py"]
    assert "Failing check: test" in item["description"]


def test_respond_to_review_does_not_require_once_flag_for_clean_pr(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[bool, int | None, int | None]] = []

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        calls.append((include_github, github_pr, github_issue))
        from praetor.maintain import MaintainScan

        return MaintainScan(
            repo_root=str(repo_root),
            github_pr_number=github_pr,
            github_pr_loop_state=PRLoopStateResult(state="clean"),
        )

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(
        app,
        ["maintain", "--github-pr", "88", "--respond-to-review", "--json"],
    )

    assert result.exit_code == 0
    assert calls == [(True, 88, None)]
    payload = json.loads(result.output)
    assert payload["github_pr_loop_state"]["state"] == "clean"
    assert payload["respond_to_review"] is True
    assert payload["max_cycles"] == 3
    assert payload["items"] == []
    assert payload["write_tasks"] is False
    assert payload["written_task_ids"] == []


def test_respond_to_review_requires_focused_github_pr(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["maintain", "--respond-to-review"], color=False)

    assert result.exit_code != 0
    assert "--respond-to-review requires --github-pr." in unstyle(result.output)


def test_respond_to_review_rejects_non_positive_max_cycles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["maintain", "--github-pr", "88", "--respond-to-review", "--max-cycles", "0"],
        color=False,
    )

    assert result.exit_code != 0
    assert "--max-cycles must be at least 1." in unstyle(result.output)


def test_respond_to_review_needs_repair_proposes_without_writing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        assert include_github is True
        assert github_pr == 88
        assert github_issue is None
        from praetor.maintain import MaintainScan

        return MaintainScan(
            repo_root=str(repo_root),
            github_pr_number=88,
            github_pr_loop_state=PRLoopStateResult(
                state="needs_repair",
                actionable_review_items=[
                    "Unresolved review thread: src/app.py:42 - Please clarify."
                ],
            ),
        )

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(
        app,
        ["maintain", "--github-pr", "88", "--respond-to-review", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["respond_to_review"] is True
    assert payload["items"][0]["source"] == "github:pull_request:current#88"
    assert payload["written_count"] == 0
    assert list_tasks(tmp_path) == []


def test_respond_to_review_needs_repair_writes_only_with_write_tasks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del include_github, github_issue
        from praetor.maintain import MaintainScan

        return MaintainScan(
            repo_root=str(repo_root),
            github_pr_number=github_pr,
            github_pr_loop_state=PRLoopStateResult(
                state="needs_repair",
                failing_checks=["Failing check: tests (conclusion=failure)."],
            ),
        )

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(
        app,
        [
            "maintain",
            "--github-pr",
            "88",
            "--respond-to-review",
            "--write-tasks",
            "--task-verify",
            "pytest -q",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["written_count"] == 1
    assert payload["skipped_count"] == 0
    tasks = list_tasks(tmp_path)
    assert len(tasks) == 1
    assert tasks[0].verify == "pytest -q"


def test_respond_to_review_run_repairs_requires_write_tasks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["maintain", "--github-pr", "88", "--respond-to-review", "--run-repairs"],
        color=False,
    )

    assert result.exit_code != 0
    assert "--run-repairs requires --write-tasks." in unstyle(result.output)


def test_respond_to_review_run_repairs_requires_task_verify(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "maintain",
            "--github-pr",
            "88",
            "--respond-to-review",
            "--write-tasks",
            "--run-repairs",
        ],
        color=False,
    )

    assert result.exit_code != 0
    assert "--run-repairs requires --task-verify." in unstyle(result.output)


def test_respond_to_review_run_repairs_rejects_blank_task_verify(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "maintain",
            "--github-pr",
            "88",
            "--respond-to-review",
            "--write-tasks",
            "--run-repairs",
            "--task-verify",
            "   ",
        ],
        color=False,
    )

    assert result.exit_code != 0
    assert "--task-verify must not be blank." in unstyle(result.output)


def test_respond_to_review_run_repairs_drains_scoped_repair_tasks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    _write_task(tmp_path, _make_task("unrelated-ready", TaskStatus.pending, verify="pytest -q"))
    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        assert include_github is True
        assert github_pr == 88
        assert github_issue is None
        from praetor.maintain import MaintainScan

        return MaintainScan(
            repo_root=str(repo_root),
            github_pr_number=88,
            github_pr_loop_state=PRLoopStateResult(
                state="needs_repair",
                actionable_review_items=[
                    "Unresolved review thread: src/app.py:42 - Please clarify."
                ],
            ),
        )

    def fake_drain_queue(repo_root: Path, adapter: object, **kwargs: object) -> None:
        captured["repo_root"] = repo_root
        captured["adapter"] = adapter
        captured.update(kwargs)

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)
    monkeypatch.setattr(
        "praetor.commands.maintain.get_adapter", lambda *_args, **_kwargs: MockAdapter()
    )
    monkeypatch.setattr(
        "praetor.commands.maintain.resolve_reviewer_adapter", lambda **_kwargs: None
    )
    monkeypatch.setattr("praetor.commands.maintain.drain_queue", fake_drain_queue)

    result = runner.invoke(
        app,
        [
            "maintain",
            "--github-pr",
            "88",
            "--respond-to-review",
            "--write-tasks",
            "--run-repairs",
            "--task-verify",
            "pytest -q",
            "--max-cycles",
            "2",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    [repair_task_id] = payload["repair_task_ids"]
    assert payload["run_repairs"] is True
    assert payload["drain_started"] is True
    assert payload["written_task_ids"] == [repair_task_id]
    assert captured["repo_root"] == tmp_path
    assert captured["max_iterations"] == 2
    assert captured["task_ids"] == {repair_task_id}
    tasks_by_id = {task.id: task for task in list_tasks(tmp_path)}
    assert tasks_by_id["unrelated-ready"].status is TaskStatus.pending
    assert tasks_by_id[repair_task_id].review == "strict"


def test_respond_to_review_run_repairs_refreshes_latest_run_after_drain(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    events: list[str] = []

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del include_github, github_issue
        from praetor.maintain import MaintainScan

        return MaintainScan(
            repo_root=str(repo_root),
            github_pr_number=github_pr,
            github_pr_loop_state=PRLoopStateResult(
                state="needs_repair",
                actionable_review_items=[
                    "Unresolved review thread: src/app.py:42 - Please clarify."
                ],
            ),
        )

    def fake_drain_queue(repo_root: Path, adapter: object, **kwargs: object) -> None:
        del repo_root, adapter, kwargs
        events.append("drain")

    def fake_latest_run(repo_root: Path) -> SimpleNamespace:
        del repo_root
        assert events == ["drain"]
        return SimpleNamespace(id="run-after-drain", status="completed")

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)
    monkeypatch.setattr(
        "praetor.commands.maintain.get_adapter", lambda *_args, **_kwargs: MockAdapter()
    )
    monkeypatch.setattr(
        "praetor.commands.maintain.resolve_reviewer_adapter", lambda **_kwargs: None
    )
    monkeypatch.setattr("praetor.commands.maintain.drain_queue", fake_drain_queue)
    monkeypatch.setattr("praetor.commands.maintain.load_latest_run", fake_latest_run)

    result = runner.invoke(
        app,
        [
            "maintain",
            "--github-pr",
            "88",
            "--respond-to-review",
            "--write-tasks",
            "--run-repairs",
            "--task-verify",
            "pytest -q",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["drain_started"] is True
    assert payload["latest_run"] == {"id": "run-after-drain", "status": "completed"}


def test_respond_to_review_run_repairs_skips_stale_task_without_requested_verify(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    drain_calls: list[dict[str, object]] = []

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del include_github, github_issue
        from praetor.maintain import MaintainScan

        return MaintainScan(
            repo_root=str(repo_root),
            github_pr_number=github_pr,
            github_pr_loop_state=PRLoopStateResult(
                state="needs_repair",
                actionable_review_items=[
                    "Unresolved review thread: src/app.py:42 - Please clarify."
                ],
            ),
        )

    def fake_drain_queue(repo_root: Path, adapter: object, **kwargs: object) -> None:
        del repo_root, adapter
        drain_calls.append(kwargs)

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)
    monkeypatch.setattr(
        "praetor.commands.maintain.get_adapter", lambda *_args, **_kwargs: MockAdapter()
    )
    monkeypatch.setattr(
        "praetor.commands.maintain.resolve_reviewer_adapter", lambda **_kwargs: None
    )
    monkeypatch.setattr("praetor.commands.maintain.drain_queue", fake_drain_queue)

    write_result = runner.invoke(
        app,
        [
            "maintain",
            "--github-pr",
            "88",
            "--respond-to-review",
            "--write-tasks",
            "--json",
        ],
    )
    assert write_result.exit_code == 0
    [task] = list_tasks(tmp_path)
    assert task.verify is None

    run_result = runner.invoke(
        app,
        [
            "maintain",
            "--github-pr",
            "88",
            "--respond-to-review",
            "--write-tasks",
            "--run-repairs",
            "--task-verify",
            "pytest -q",
            "--json",
        ],
    )

    assert run_result.exit_code == 0
    payload = json.loads(run_result.output)
    assert payload["written_task_ids"] == []
    assert payload["skipped_task_ids"] == [task.id]
    assert payload["repair_task_ids"] == []
    assert payload["drain_started"] is False
    assert drain_calls == []


def test_respond_to_review_run_repairs_skips_stale_task_without_review_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    drain_calls: list[dict[str, object]] = []

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del include_github, github_issue
        from praetor.maintain import MaintainScan

        return MaintainScan(
            repo_root=str(repo_root),
            github_pr_number=github_pr,
            github_pr_loop_state=PRLoopStateResult(
                state="needs_repair",
                actionable_review_items=[
                    "Unresolved review thread: src/app.py:42 - Please clarify."
                ],
            ),
        )

    def fake_drain_queue(repo_root: Path, adapter: object, **kwargs: object) -> None:
        del repo_root, adapter
        drain_calls.append(kwargs)

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)
    monkeypatch.setattr(
        "praetor.commands.maintain.get_adapter", lambda *_args, **_kwargs: MockAdapter()
    )
    monkeypatch.setattr(
        "praetor.commands.maintain.resolve_reviewer_adapter", lambda **_kwargs: None
    )
    monkeypatch.setattr("praetor.commands.maintain.drain_queue", fake_drain_queue)

    write_result = runner.invoke(
        app,
        [
            "maintain",
            "--github-pr",
            "88",
            "--respond-to-review",
            "--write-tasks",
            "--task-verify",
            "pytest -q",
            "--json",
        ],
    )
    assert write_result.exit_code == 0
    [task] = list_tasks(tmp_path)
    assert task.verify == "pytest -q"
    assert task.review == "off"

    run_result = runner.invoke(
        app,
        [
            "maintain",
            "--github-pr",
            "88",
            "--respond-to-review",
            "--write-tasks",
            "--run-repairs",
            "--task-verify",
            "pytest -q",
            "--json",
        ],
    )

    assert run_result.exit_code == 0
    payload = json.loads(run_result.output)
    assert payload["written_task_ids"] == []
    assert payload["skipped_task_ids"] == [task.id]
    assert payload["repair_task_ids"] == []
    assert payload["drain_started"] is False
    assert drain_calls == []


def test_respond_to_review_waiting_state_stays_report_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del include_github, github_issue
        from praetor.maintain import MaintainScan

        return MaintainScan(
            repo_root=str(repo_root),
            github_pr_number=github_pr,
            github_pr_loop_state=PRLoopStateResult(
                state="waiting",
                waiting_review_items=["Review is still required."],
            ),
        )

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(
        app,
        ["maintain", "--github-pr", "88", "--respond-to-review", "--write-tasks", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["github_pr_loop_state"]["state"] == "waiting"
    assert payload["items"] == []
    assert payload["written_count"] == 0
    assert list_tasks(tmp_path) == []


def test_maintain_once_with_propose_tasks_respects_github_issue_filter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[bool, int | None, int | None]] = []

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        calls.append((include_github, github_pr, github_issue))
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(repo_root), items=[])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(
        app,
        ["maintain", "--once", "--github-issue", "44", "--propose-tasks", "--json"],
    )

    assert result.exit_code == 0
    assert calls == [(True, None, 44)]


def test_maintain_once_propose_tasks_is_read_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    _write_task(tmp_path, _make_task("ready-a", TaskStatus.pending, verify="pytest"))

    praetor_dir = tmp_path / ".praetor"
    before = _snapshot_tree(praetor_dir)

    result = runner.invoke(app, ["maintain", "--once", "--propose-tasks"])

    assert result.exit_code == 0
    after = _snapshot_tree(praetor_dir)
    assert before == after


def test_maintain_once_with_propose_tasks_and_local_only_tasks_is_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    _write_task(tmp_path, _make_task("ready-a", TaskStatus.pending, verify=None))

    result = runner.invoke(app, ["maintain", "--once", "--propose-tasks"])

    assert result.exit_code == 0
    assert "title:" not in result.output.lower()
    assert "No maintainer proposals found." in result.output


def test_maintain_once_with_propose_tasks_requires_write_task_flag(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls: list[Path] = []

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del include_github, github_pr, github_issue
        calls.append(repo_root)
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(repo_root), items=[])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(app, ["maintain", "--once", "--write-tasks"], color=False)

    assert result.exit_code != 0
    assert calls == []


def test_maintain_once_with_task_verify_requires_write_task_flag(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["maintain", "--once", "--propose-tasks", "--task-verify", "pytest -q"],
        color=False,
    )

    assert result.exit_code != 0
    assert "--task-verify requires --write-tasks." in unstyle(result.output)


def test_maintain_once_with_propose_tasks_and_write_tasks_creates_task_markdown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    proposal = MaintainItem(
        source="github:issue:octo-org/octo-repo#101",
        url="https://github.com/octo-org/octo-repo/issues/101",
        classification="needs_owner",
        fit="Open issue requires owner triage.",
        risk="Needs owner triage.",
        proof="Issue #101: Add endpoint docs",
        blocker="Needs owner triage.",
        next_action="Owner: triage issue.",
    )

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del repo_root, github_pr, github_issue
        assert include_github
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(tmp_path), items=[proposal])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(
        app,
        ["maintain", "--once", "--github", "--propose-tasks", "--write-tasks", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["write_tasks"] is True
    assert payload["written_count"] == 1
    assert len(payload["written_task_ids"]) == 1
    assert payload["skipped_count"] == 0

    task_files = sorted((tmp_path / ".praetor" / "tasks").glob("*.md"))
    assert len(task_files) == 1

    result_rerun = runner.invoke(
        app,
        ["maintain", "--once", "--github", "--propose-tasks", "--write-tasks", "--json"],
    )
    payload_rerun = json.loads(result_rerun.output)
    assert payload_rerun["written_count"] == 0
    assert payload_rerun["skipped_count"] == 1
    assert payload_rerun["skipped_task_ids"] == payload["written_task_ids"]


def test_maintain_once_with_propose_tasks_and_task_verify_uses_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    proposal = MaintainItem(
        source="github:issue:octo-org/octo-repo#101",
        url="https://github.com/octo-org/octo-repo/issues/101",
        classification="needs_owner",
        fit="Open issue requires owner triage.",
        risk="Needs owner triage.",
        proof="Issue #101: Add endpoint docs",
        blocker="Needs owner triage.",
        next_action="Owner: triage issue.",
    )

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del repo_root, github_pr, github_issue
        assert include_github
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(tmp_path), items=[proposal])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(
        app,
        [
            "maintain",
            "--once",
            "--github",
            "--propose-tasks",
            "--write-tasks",
            "--task-verify",
            "pytest -q",
            "--json",
        ],
        color=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["write_tasks"] is True

    tasks = list_tasks(tmp_path)
    assert len(tasks) == 1
    assert tasks[0].verify == "pytest -q"


def test_maintain_once_with_propose_tasks_skips_github_intake_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    intake_item = MaintainItem(
        source="github:intake",
        classification="needs_owner",
        fit="Intake unavailable.",
        risk="GitHub intake could not be loaded.",
        proof="GitHub intake unavailable.",
        blocker="GitHub provider did not return actionable findings.",
        next_action="Fix GitHub intake configuration.",
    )
    issue_item = MaintainItem(
        source="github:issue:octo-org/octo-repo#101",
        url="https://github.com/octo-org/octo-repo/issues/101",
        classification="needs_owner",
        fit="Open issue requires owner triage.",
        risk="Needs owner triage.",
        proof="Issue #101: Add endpoint docs",
        blocker="Needs owner triage.",
        next_action="Owner: triage issue.",
    )

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del repo_root, github_pr, github_issue
        assert include_github
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(tmp_path), items=[intake_item, issue_item])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(
        app,
        ["maintain", "--once", "--github", "--propose-tasks", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [item["source"] for item in payload["items"]] == [issue_item.source]


def test_maintain_once_with_propose_tasks_skips_pr_review_thread_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    diagnostic_item = MaintainItem(
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
    review_item = MaintainItem(
        source="github:pull_request:octo-org/octo-repo#202",
        url="https://github.com/octo-org/octo-repo/pull/202",
        classification="needs_owner",
        fit="Open PR has unresolved review feedback.",
        risk="Review requested changes.",
        proof=(
            "Pull request #202: Improve docs\n"
            "Unresolved review thread: src/app.py:42 - Please clarify."
        ),
        blocker="Open review feedback must be resolved.",
        next_action="Owner: resolve review feedback.",
    )

    def fake_scan(
        repo_root: Path,
        *,
        include_github: bool = False,
        github_pr: int | None = None,
        github_issue: int | None = None,
    ):
        del repo_root, github_pr, github_issue
        assert include_github
        from praetor.maintain import MaintainScan

        return MaintainScan(repo_root=str(tmp_path), items=[diagnostic_item, review_item])

    monkeypatch.setattr("praetor.commands.maintain.scan", fake_scan)

    result = runner.invoke(
        app,
        ["maintain", "--once", "--github", "--propose-tasks", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [item["proof"] for item in payload["items"]] == [review_item.proof]


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
