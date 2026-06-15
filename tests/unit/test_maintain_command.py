import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from praetor.cli import app
from praetor.frontmatter import dump_task
from praetor.models import Task, TaskStatus
from praetor.state import init_workspace

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
