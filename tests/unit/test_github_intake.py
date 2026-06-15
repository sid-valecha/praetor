import json
import subprocess

from praetor.github_intake import scan_github_intake


EMPTY_REVIEW_THREADS = {
    "data": {
        "repository": {
            "pullRequest": {
                "reviewThreads": {
                    "nodes": [],
                }
            }
        }
    }
}


def test_gh_command_failure_is_reported_as_needs_owner() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        del command
        return 1, "", "gh: command not found"

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "needs_owner"
    assert item.source == "github:intake"
    assert "gh" in item.proof.lower()


def test_gh_timeout_is_reported_as_needs_owner() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        raise subprocess.TimeoutExpired(command, timeout=15)

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "needs_owner"
    assert item.source == "github:intake"
    assert "timed out" in item.proof.lower()


def test_open_issue_is_marked_needs_owner() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "issue", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 101,
                            "title": "Add endpoint docs",
                            "body": "Please document new endpoint.",
                            "labels": [{"name": "documentation"}],
                            "url": "https://github.com/octo-org/octo-repo/issues/101",
                        }
                    ]
                ),
                "",
            )
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.source == "github:issue:octo-org/octo-repo#101"
    assert item.classification == "needs_owner"
    assert item.next_action.startswith("Owner")
    assert "Issue #101" in item.proof


def test_open_issue_that_mentions_autonomous_work_is_still_needs_owner() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "issue", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 102,
                            "title": "Document autonomous loop behavior",
                            "body": "Explain where autonomous execution is allowed.",
                            "labels": [],
                            "url": "https://github.com/octo-org/octo-repo/issues/102",
                        }
                    ]
                ),
                "",
            )
        if command[:3] == ["gh", "api", "graphql"]:
            return 0, json.dumps(EMPTY_REVIEW_THREADS), ""
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.source == "github:issue:octo-org/octo-repo#102"
    assert item.classification == "needs_owner"
    assert item.blocker is not None
    assert "human review" in item.blocker


def test_focused_issue_uses_issue_view() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        if command[:3] == ["gh", "issue", "view"]:
            return (
                0,
                json.dumps(
                    {
                        "number": 101,
                        "title": "Add endpoint docs",
                        "body": "Please document new endpoint.",
                        "labels": [{"name": "documentation"}],
                        "url": "https://github.com/octo-org/octo-repo/issues/101",
                    }
                ),
                "",
            )
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", issue_number=101, runner=runner)

    assert len(items) == 1
    assert items[0].source == "github:issue:octo-org/octo-repo#101"
    assert items[0].classification == "needs_owner"
    assert ["gh", "issue", "view"] == commands[0][:3]
    assert not any(command[:3] == ["gh", "issue", "list"] for command in commands)


def test_pr_list_query_uses_supported_gh_json_fields() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "[]", ""

    scan_github_intake("octo-org/octo-repo", runner=runner)

    pr_command = next(command for command in commands if command[:3] == ["gh", "pr", "list"])
    json_fields = pr_command[pr_command.index("--json") + 1]
    assert "statusCheckRollup" in json_fields
    assert "commits" in json_fields
    assert "checks" not in json_fields
    assert "--limit" in pr_command
    assert pr_command[pr_command.index("--limit") + 1] == "100"


def test_issue_list_query_uses_explicit_limit() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "[]", ""

    scan_github_intake("octo-org/octo-repo", runner=runner)

    issue_command = next(command for command in commands if command[:3] == ["gh", "issue", "list"])
    assert "--limit" in issue_command
    assert issue_command[issue_command.index("--limit") + 1] == "100"


def test_focused_pr_uses_pr_view_and_review_threads() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        if command[:3] == ["gh", "pr", "view"]:
            return (
                0,
                json.dumps(
                    {
                        "number": 202,
                        "title": "Improve docs",
                        "body": "Small doc fixes.",
                        "url": "https://github.com/octo-org/octo-repo/pull/202",
                        "reviewDecision": "APPROVED",
                        "statusCheckRollup": {
                            "state": "COMPLETED",
                            "conclusion": "SUCCESS",
                        },
                    }
                ),
                "",
            )
        if command[:3] == ["gh", "api", "graphql"]:
            return (
                0,
                json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "nodes": [
                                            {
                                                "isResolved": False,
                                                "comments": {
                                                    "nodes": [
                                                        {
                                                            "body": "Please clarify docs.",
                                                            "path": "README.md",
                                                            "line": 7,
                                                        }
                                                    ]
                                                },
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                    }
                ),
                "",
            )
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", pr_number=202, runner=runner)

    assert len(items) == 1
    assert items[0].source == "github:pull_request:octo-org/octo-repo#202"
    assert items[0].classification == "needs_owner"
    assert "README.md:7" in items[0].proof
    assert ["gh", "pr", "view"] == commands[0][:3]
    assert not any(command[:3] == ["gh", "pr", "list"] for command in commands)


def test_focused_merged_pr_with_unresolved_threads_is_historical_defer() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "view"]:
            return (
                0,
                json.dumps(
                    {
                        "number": 22,
                        "title": "Prepare maintainer scan",
                        "body": "Merged PR.",
                        "url": "https://github.com/octo-org/octo-repo/pull/22",
                        "state": "MERGED",
                        "mergedAt": "2026-06-14T12:00:00Z",
                        "reviewDecision": "APPROVED",
                        "statusCheckRollup": {
                            "state": "COMPLETED",
                            "conclusion": "SUCCESS",
                        },
                    }
                ),
                "",
            )
        if command[:3] == ["gh", "api", "graphql"]:
            return (
                0,
                json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "nodes": [
                                            {
                                                "isResolved": False,
                                                "comments": {
                                                    "nodes": [
                                                        {
                                                            "body": "Historical review finding.",
                                                            "path": "praetor/worktree.py",
                                                        }
                                                    ]
                                                },
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                    }
                ),
                "",
            )
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", pr_number=22, runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "defer"
    assert item.blocker is None
    assert "already merged" in item.fit.lower()
    assert "historical" in item.proof.lower()


def test_open_pr_approved_with_passing_checks_is_defer() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 202,
                            "title": "Improve docs",
                            "body": "Small doc fixes.",
                            "url": "https://github.com/octo-org/octo-repo/pull/202",
                            "reviewDecision": "APPROVED",
                            "statusCheckRollup": {
                                "state": "COMPLETED",
                                "conclusion": "SUCCESS",
                                "checkRuns": {
                                    "nodes": [
                                        {
                                            "name": "ci",
                                            "status": "completed",
                                            "conclusion": "SUCCESS",
                                        },
                                    ]
                                },
                            },
                        }
                    ]
                ),
                "",
            )
        if command[:3] == ["gh", "api", "graphql"]:
            return 0, json.dumps(EMPTY_REVIEW_THREADS), ""
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.source == "github:pull_request:octo-org/octo-repo#202"
    assert item.classification == "defer"
    assert "approved" in item.proof.lower()
    assert "mutation" in item.next_action.lower()


def test_open_pr_approved_with_status_check_rollup_array_is_passing() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 204,
                            "title": "Improve docs with array checks",
                            "url": "https://github.com/octo-org/octo-repo/pull/204",
                            "reviewDecision": "APPROVED",
                            "statusCheckRollup": [
                                {
                                    "name": "ci",
                                    "status": "completed",
                                    "conclusion": "SUCCESS",
                                },
                                {
                                    "context": "lint",
                                    "state": "SUCCESS",
                                },
                            ],
                        }
                    ]
                ),
                "",
            )
        if command[:3] == ["gh", "api", "graphql"]:
            return 0, json.dumps(EMPTY_REVIEW_THREADS), ""
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "defer"
    assert "approved with passing checks" in item.fit
    assert "Checks: passing" in item.proof


def test_open_pr_approved_with_pending_checks_is_pending_not_passing() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 203,
                            "title": "Improve docs while CI runs",
                            "body": "Small doc fixes.",
                            "url": "https://github.com/octo-org/octo-repo/pull/203",
                            "reviewDecision": "APPROVED",
                            "statusCheckRollup": {
                                "state": "PENDING",
                                "checkRuns": {
                                    "nodes": [
                                        {
                                            "name": "ci",
                                            "status": "queued",
                                            "conclusion": None,
                                        },
                                    ]
                                },
                            },
                        }
                    ]
                ),
                "",
            )
        if command[:3] == ["gh", "api", "graphql"]:
            return 0, json.dumps(EMPTY_REVIEW_THREADS), ""
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "defer"
    assert "pending review/check finalization" in item.fit
    assert "Checks: passing" not in item.proof


def test_open_pr_with_pending_rollup_and_successful_children_is_not_passing() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 204,
                            "title": "Improve docs while required CI is expected",
                            "url": "https://github.com/octo-org/octo-repo/pull/204",
                            "reviewDecision": "APPROVED",
                            "statusCheckRollup": {
                                "state": "PENDING",
                                "checkRuns": {
                                    "nodes": [
                                        {
                                            "name": "ci",
                                            "status": "completed",
                                            "conclusion": "SUCCESS",
                                        },
                                    ]
                                },
                            },
                        }
                    ]
                ),
                "",
            )
        if command[:3] == ["gh", "api", "graphql"]:
            return 0, json.dumps(EMPTY_REVIEW_THREADS), ""
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "defer"
    assert "pending review/check finalization" in item.fit
    assert "Checks: passing" not in item.proof


def test_open_pr_with_review_changes_requested_is_needs_owner() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 303,
                            "title": "Refactor engine",
                            "body": "Large refactor.",
                            "url": "https://github.com/octo-org/octo-repo/pull/303",
                            "reviewDecision": "CHANGES_REQUESTED",
                            "latestReviews": [
                                {
                                    "state": "CHANGES_REQUESTED",
                                    "body": "Please split this into smaller changes.",
                                }
                            ],
                        }
                    ]
                ),
                "",
            )
        if command[:3] == ["gh", "api", "graphql"]:
            return 0, json.dumps(EMPTY_REVIEW_THREADS), ""
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "needs_owner"
    assert "review" in item.proof.lower()
    assert "changes" in item.proof.lower()
    assert "split this into smaller changes" in item.proof
    assert item.blocker is not None
    assert item.blocker and "review" in item.blocker.lower()


def test_historical_review_changes_requested_does_not_block_latest_approval() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 304,
                            "title": "Refactor engine follow-up",
                            "url": "https://github.com/octo-org/octo-repo/pull/304",
                            "reviewDecision": "APPROVED",
                            "latestReviews": [
                                {
                                    "state": "APPROVED",
                                    "body": "Looks good now.",
                                }
                            ],
                            "reviews": [
                                {
                                    "state": "CHANGES_REQUESTED",
                                    "body": "Please split this into smaller changes.",
                                },
                                {
                                    "state": "APPROVED",
                                    "body": "Looks good now.",
                                },
                            ],
                            "statusCheckRollup": {
                                "state": "COMPLETED",
                                "conclusion": "SUCCESS",
                            },
                        }
                    ]
                ),
                "",
            )
        if command[:3] == ["gh", "api", "graphql"]:
            return 0, json.dumps(EMPTY_REVIEW_THREADS), ""
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "defer"
    assert item.blocker is None
    assert "approved" in item.proof.lower()


def test_open_pr_with_failing_checks_is_needs_owner() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 404,
                            "title": "WIP task",
                            "url": "https://github.com/octo-org/octo-repo/pull/404",
                            "reviewDecision": "APPROVED",
                            "statusCheckRollup": {
                                "state": "COMPLETED",
                                "conclusion": "FAILURE",
                                "checkRuns": {
                                    "nodes": [
                                        {
                                            "name": "unit-tests",
                                            "status": "completed",
                                            "conclusion": "FAILURE",
                                        }
                                    ]
                                },
                            },
                        }
                    ]
                ),
                "",
            )
        if command[:3] == ["gh", "api", "graphql"]:
            return 0, json.dumps(EMPTY_REVIEW_THREADS), ""
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "needs_owner"
    assert "check" in item.proof.lower()
    assert "failing" in item.proof.lower()
    assert "unit-tests" in item.proof
    assert item.source == "github:pull_request:octo-org/octo-repo#404"


def test_open_pr_with_top_level_state_still_detects_failing_checks() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 405,
                            "title": "WIP task with state",
                            "url": "https://github.com/octo-org/octo-repo/pull/405",
                            "state": "OPEN",
                            "reviewDecision": "APPROVED",
                            "statusCheckRollup": {
                                "state": "COMPLETED",
                                "conclusion": "FAILURE",
                                "checkRuns": {
                                    "nodes": [
                                        {
                                            "name": "integration-tests",
                                            "status": "completed",
                                            "conclusion": "FAILURE",
                                        }
                                    ]
                                },
                            },
                        }
                    ]
                ),
                "",
            )
        if command[:3] == ["gh", "api", "graphql"]:
            return 0, json.dumps(EMPTY_REVIEW_THREADS), ""
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "needs_owner"
    assert "integration-tests" in item.proof
    assert "failing check" in item.proof.lower()


def test_open_pr_with_startup_failure_check_is_needs_owner() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 406,
                            "title": "Workflow cannot start",
                            "url": "https://github.com/octo-org/octo-repo/pull/406",
                            "reviewDecision": "APPROVED",
                            "statusCheckRollup": [
                                {
                                    "name": "docker build",
                                    "status": "completed",
                                    "conclusion": "STARTUP_FAILURE",
                                },
                                {
                                    "context": "legacy-ci",
                                    "state": "STALE",
                                },
                            ],
                        }
                    ]
                ),
                "",
            )
        if command[:3] == ["gh", "api", "graphql"]:
            return 0, json.dumps(EMPTY_REVIEW_THREADS), ""
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "needs_owner"
    assert "docker build" in item.proof
    assert "startup_failure" in item.proof.lower()


def test_open_pr_with_unresolved_review_comment_signal_is_needs_owner() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 505,
                            "title": "Cleanup",
                            "body": "Refactor cleanup.",
                            "url": "https://github.com/octo-org/octo-repo/pull/505",
                            "reviewDecision": "APPROVED",
                            "comments": [
                                {
                                    "isResolved": False,
                                    "author": {"login": "reviewer"},
                                    "body": "Please resolve variable naming.",
                                }
                            ],
                        }
                    ]
                ),
                "",
            )
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "needs_owner"
    assert "review" in item.proof.lower()
    assert "resolve variable naming" in item.proof


def test_open_pr_with_unresolved_graphql_review_thread_is_needs_owner() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 606,
                            "title": "Polish docs",
                            "body": "Small docs update.",
                            "url": "https://github.com/octo-org/octo-repo/pull/606",
                            "reviewDecision": "APPROVED",
                            "statusCheckRollup": {
                                "state": "COMPLETED",
                                "conclusion": "SUCCESS",
                            },
                        }
                    ]
                ),
                "",
            )
        if command[:3] == ["gh", "api", "graphql"]:
            return (
                0,
                json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "nodes": [
                                            {
                                                "isResolved": False,
                                                "comments": {
                                                    "nodes": [
                                                        {
                                                            "body": "Please clarify retry behavior.",
                                                            "path": "README.md",
                                                            "line": 42,
                                                            "author": {"login": "reviewer"},
                                                        }
                                                    ]
                                                },
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                    }
                ),
                "",
            )
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "needs_owner"
    assert "unresolved review thread" in item.proof.lower()
    assert "README.md:42" in item.proof
    assert "clarify retry behavior" in item.proof
