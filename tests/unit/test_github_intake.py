import json
import subprocess

from praetor.github_intake import classify_focused_pr_loop_state, scan_github_intake


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


def test_pr_intake_continues_when_issues_are_disabled() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "issue", "list"]:
            return 1, "", "GraphQL: Issues are disabled for this repository"
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 202,
                            "title": "Improve docs",
                            "url": "https://github.com/octo-org/octo-repo/pull/202",
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
            return 0, json.dumps(EMPTY_REVIEW_THREADS), ""
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.source == "github:pull_request:octo-org/octo-repo#202"
    assert item.classification == "defer"
    assert "approved with passing checks" in item.fit


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


def test_focused_issue_query_requests_state() -> None:
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
                        "state": "OPEN",
                    }
                ),
                "",
            )
        return 0, "[]", ""

    scan_github_intake("octo-org/octo-repo", issue_number=101, runner=runner)

    issue_command = next(command for command in commands if command[:3] == ["gh", "issue", "view"])
    json_fields = issue_command[issue_command.index("--json") + 1]
    assert "state" in json_fields


def test_focused_closed_issue_is_historical_defer() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "issue", "view"]:
            return (
                0,
                json.dumps(
                    {
                        "number": 103,
                        "title": "Already handled",
                        "body": "Resolved elsewhere.",
                        "labels": [],
                        "url": "https://github.com/octo-org/octo-repo/issues/103",
                        "state": "CLOSED",
                    }
                ),
                "",
            )
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", issue_number=103, runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "defer"
    assert item.blocker is None
    assert "closed" in item.fit.lower()
    assert "State: closed" in item.proof


def test_pr_list_query_uses_supported_gh_json_fields() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str]) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "[]", ""

    scan_github_intake("octo-org/octo-repo", runner=runner)

    pr_command = next(command for command in commands if command[:3] == ["gh", "pr", "list"])
    json_fields = pr_command[pr_command.index("--json") + 1]
    field_names = set(json_fields.split(","))
    assert "statusCheckRollup" in field_names
    assert "latestReviews" in field_names
    assert "comments" in field_names
    assert "commits" not in field_names
    assert "reviews" not in field_names
    assert "checks" not in field_names
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


def test_focused_pr_loop_state_classifies_current_review_thread() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "view"]:
            return (
                0,
                json.dumps(
                    {
                        "number": 202,
                        "title": "Improve docs",
                        "url": "https://github.com/octo-org/octo-repo/pull/202",
                        "state": "OPEN",
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
                                                "isOutdated": False,
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
        if command[:3] == ["gh", "repo", "view"]:
            return (
                0,
                json.dumps({"owner": {"login": "octo-org"}, "name": "octo-repo"}),
                "",
            )
        return 0, "[]", ""

    result = classify_focused_pr_loop_state(pr_number=202, runner=runner)

    assert result.state == "needs_repair"
    assert result.actionable_review_items == [
        "Unresolved review thread: README.md:7 - Please clarify docs."
    ]


def test_focused_pr_loop_state_blocks_when_review_threads_are_unavailable() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "view"]:
            return (
                0,
                json.dumps(
                    {
                        "number": 202,
                        "title": "Improve docs",
                        "url": "https://github.com/octo-org/octo-repo/pull/202",
                        "state": "OPEN",
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
            return 1, "", "GraphQL: Resource not accessible by integration"
        if command[:3] == ["gh", "repo", "view"]:
            return (
                0,
                json.dumps({"owner": {"login": "octo-org"}, "name": "octo-repo"}),
                "",
            )
        return 0, "[]", ""

    result = classify_focused_pr_loop_state(pr_number=202, runner=runner)

    assert result.state == "blocked"
    assert any("Resource not accessible" in reason for reason in result.blocked_reasons)


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


def test_focused_closed_unmerged_pr_with_blockers_is_historical_defer() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "view"]:
            return (
                0,
                json.dumps(
                    {
                        "number": 23,
                        "title": "Closed experiment",
                        "body": "Not merged.",
                        "url": "https://github.com/octo-org/octo-repo/pull/23",
                        "state": "CLOSED",
                        "reviewDecision": "CHANGES_REQUESTED",
                        "latestReviews": [
                            {
                                "state": "CHANGES_REQUESTED",
                                "body": "Please rework this.",
                            }
                        ],
                        "statusCheckRollup": {
                            "state": "COMPLETED",
                            "conclusion": "FAILURE",
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
                                                            "path": "praetor/github_intake.py",
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

    items = scan_github_intake("octo-org/octo-repo", pr_number=23, runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "defer"
    assert item.blocker is None
    assert "closed without merge" in item.fit.lower()
    assert "State: closed" in item.proof


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


def test_missing_repo_slug_reports_review_threads_unavailable() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 606,
                            "title": "Polish docs",
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
        if command[:3] == ["gh", "repo", "view"]:
            return 1, "", "not a git repository"
        return 0, "[]", ""

    items = scan_github_intake(runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "needs_owner"
    assert "review-thread intake is unavailable" in item.fit.lower()
    assert "repository slug could not be resolved" in item.proof.lower()


def test_unresolved_outdated_graphql_review_thread_is_reported() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 607,
                            "title": "Polish docs",
                            "url": "https://github.com/octo-org/octo-repo/pull/607",
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
                                                "isOutdated": True,
                                                "comments": {
                                                    "nodes": [
                                                        {
                                                            "body": "Superseded finding.",
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
    assert "review feedback" in item.fit.lower()
    assert "unresolved outdated review thread" in item.proof.lower()
    assert "superseded finding" in item.proof.lower()


def test_nullable_graphql_review_thread_response_is_reported_as_unavailable() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 607,
                            "title": "Polish docs",
                            "url": "https://github.com/octo-org/octo-repo/pull/607",
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
                        "data": None,
                        "errors": [
                            {
                                "message": "Resource not accessible by integration",
                            }
                        ],
                    }
                ),
                "",
            )
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "needs_owner"
    assert "review threads unavailable" in item.proof.lower()
    assert "resource not accessible" in item.proof.lower()


def test_null_review_threads_with_errors_is_reported_as_unavailable() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 608,
                            "title": "Polish docs",
                            "url": "https://github.com/octo-org/octo-repo/pull/608",
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
                                    "reviewThreads": None,
                                }
                            }
                        },
                        "errors": [
                            {
                                "message": "Could not resolve reviewThreads.",
                            }
                        ],
                    }
                ),
                "",
            )
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "needs_owner"
    assert "review-thread intake is unavailable" in item.fit.lower()
    assert "could not resolve reviewthreads" in item.proof.lower()


def test_truncated_graphql_review_threads_are_reported_as_unavailable() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 609,
                            "title": "Polish docs",
                            "url": "https://github.com/octo-org/octo-repo/pull/609",
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
                                        "nodes": [],
                                        "pageInfo": {
                                            "hasNextPage": True,
                                            "endCursor": "cursor-1",
                                        },
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
    assert "review-thread intake is unavailable" in item.fit.lower()
    assert "truncated" in item.proof.lower()


def test_review_thread_unavailable_does_not_mask_failing_checks() -> None:
    def runner(command: list[str]) -> tuple[int, str, str]:
        if command[:3] == ["gh", "pr", "list"]:
            return (
                0,
                json.dumps(
                    [
                        {
                            "number": 608,
                            "title": "Polish docs",
                            "url": "https://github.com/octo-org/octo-repo/pull/608",
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
            return (
                0,
                json.dumps(
                    {
                        "data": None,
                        "errors": [
                            {
                                "message": "Resource not accessible by integration",
                            }
                        ],
                    }
                ),
                "",
            )
        return 0, "[]", ""

    items = scan_github_intake("octo-org/octo-repo", runner=runner)

    assert len(items) == 1
    item = items[0]
    assert item.classification == "needs_owner"
    assert "failing checks" in item.fit.lower()
    assert "unit-tests" in item.proof
    assert "review feedback" not in item.fit.lower()
