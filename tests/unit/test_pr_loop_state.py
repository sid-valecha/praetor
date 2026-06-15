from praetor.pr_loop_state import classify_pr_loop_state, PRLoopStateResult


def _payload(overrides: dict[str, object] | None = None) -> dict[str, object]:
    base: dict[str, object] = {
        "state": "OPEN",
        "reviewDecision": "APPROVED",
        "statusCheckRollup": {"name": "ci", "status": "completed", "conclusion": "success"},
        "reviewThreads": {"nodes": []},
        "latestReviews": [],
        "comments": [],
    }
    if overrides:
        base.update(overrides)
    return base


def test_classifies_clean_pr_when_checks_pass_and_review_threads_are_resolved() -> None:
    result = classify_pr_loop_state(_payload())

    assert result == PRLoopStateResult(
        state="clean",
        actionable_review_items=[],
        waiting_review_items=[],
        failing_checks=[],
        pending_checks=[],
        blocked_reasons=[],
    )


def test_needs_repair_when_current_unresolved_review_thread_exists() -> None:
    result = classify_pr_loop_state(
        _payload(
            {
                "reviewThreads": {
                    "nodes": [
                        {
                            "isResolved": False,
                            "isOutdated": False,
                            "comments": {
                                "nodes": [
                                    {
                                        "path": "src/app.py",
                                        "line": 42,
                                        "body": "Please clarify behavior.",
                                    }
                                ]
                            },
                        }
                    ]
                }
            }
        )
    )

    assert result.state == "needs_repair"
    assert result.actionable_review_items == [
        "Unresolved review thread: src/app.py:42 - Please clarify behavior."
    ]


def test_waiting_when_only_outdated_threads_are_unresolved() -> None:
    result = classify_pr_loop_state(
        _payload(
            {
                "statusCheckRollup": {"name": "ci", "status": "completed", "conclusion": "success"},
                "reviewThreads": {
                    "nodes": [
                        {
                            "isResolved": False,
                            "isOutdated": True,
                            "comments": {
                                "nodes": [
                                    {
                                        "path": "src/app.py",
                                        "line": 12,
                                        "body": "Outdated note",
                                    }
                                ]
                            },
                        }
                    ]
                },
            }
        )
    )

    assert result.state == "waiting"
    assert result.actionable_review_items == []
    assert result.waiting_review_items == [
        "Unresolved outdated review thread: src/app.py:12 - Outdated note"
    ]


def test_needs_repair_from_latest_review_decision() -> None:
    result = classify_pr_loop_state(
        _payload(
            {
                "reviewDecision": "CHANGES_REQUESTED",
                "latestReviews": [
                    {
                        "state": "CHANGES_REQUESTED",
                        "body": "Please add tests.",
                    }
                ],
            }
        )
    )

    assert result.state == "needs_repair"
    assert result.actionable_review_items == [
        "Review decision requested changes.",
        "Latest review: CHANGES_REQUESTED - Please add tests.",
    ]


def test_needs_repair_when_status_checks_fail() -> None:
    result = classify_pr_loop_state(
        _payload(
            {
                "statusCheckRollup": {
                    "name": "unit-tests",
                    "state": "completed",
                    "conclusion": "failure",
                }
            }
        )
    )

    assert result.state == "needs_repair"
    assert result.failing_checks == ["Failing check: unit-tests (conclusion=failure)."]


def test_waiting_when_checks_are_pending() -> None:
    result = classify_pr_loop_state(
        _payload(
            {
                "statusCheckRollup": {
                    "name": "unit-tests",
                    "state": "in_progress",
                    "conclusion": "pending",
                }
            }
        )
    )

    assert result.state == "waiting"
    assert result.pending_checks == ["Pending check: unit-tests (state=in_progress)."]


def test_blocked_when_review_threads_are_unavailable() -> None:
    result = classify_pr_loop_state(
        _payload(
            {
                "reviewThreads": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": True},
                },
                "statusCheckRollup": {"name": "ci", "status": "completed", "conclusion": "failure"},
            }
        )
    )

    assert result.state == "blocked"
    assert result.blocked_reasons == [
        "Review threads unavailable: review thread results were truncated."
    ]


def test_blocked_when_page_info_present_but_review_thread_nodes_are_missing() -> None:
    result = classify_pr_loop_state(
        _payload(
            {
                "reviewThreads": {
                    "pageInfo": {"hasNextPage": False},
                },
                "statusCheckRollup": {"name": "ci", "status": "completed", "conclusion": "success"},
            }
        )
    )

    assert result.state == "blocked"
    assert result.blocked_reasons == ["Review thread payload did not include thread nodes."]


def test_blocked_when_page_info_present_but_review_thread_nodes_are_invalid() -> None:
    result = classify_pr_loop_state(
        _payload(
            {
                "reviewThreads": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": {"x": 1},
                },
                "statusCheckRollup": {"name": "ci", "status": "completed", "conclusion": "success"},
            }
        )
    )

    assert result.state == "blocked"
    assert result.blocked_reasons == ["Review thread payload had invalid `nodes` shape."]


def test_waiting_when_no_checks_are_reported() -> None:
    payload = _payload()
    payload.pop("statusCheckRollup")
    payload["reviewThreads"] = {"nodes": []}

    result = classify_pr_loop_state(payload)

    assert result.state == "waiting"
    assert not result.failing_checks
    assert not result.actionable_review_items


def test_blocked_when_review_threads_are_missing_even_with_passing_checks() -> None:
    payload = _payload()
    payload.pop("reviewThreads")

    result = classify_pr_loop_state(payload)

    assert result.state == "blocked"
    assert result.blocked_reasons == ["Review thread payload is missing."]


def test_waiting_when_check_outcome_is_unknown() -> None:
    result = classify_pr_loop_state(
        _payload(
            {
                "statusCheckRollup": {
                    "name": "integration-tests",
                    "status": "completed",
                    "state": "completed",
                    "conclusion": None,
                }
            }
        )
    )

    assert result.state == "waiting"
    assert result.pending_checks == ["Unknown check state: integration-tests (state=completed)."]


def test_waiting_when_check_conclusion_is_unknown_string() -> None:
    result = classify_pr_loop_state(
        _payload(
            {
                "statusCheckRollup": {
                    "name": "integration-tests",
                    "status": "completed",
                    "state": "completed",
                    "conclusion": "mystery_status",
                }
            }
        )
    )

    assert result.state == "waiting"
    assert result.pending_checks == ["Unknown check state: integration-tests (state=completed)."]


def test_clean_when_terminal_pr_is_closed_or_merged() -> None:
    payload = _payload({"state": "CLOSED", "mergedAt": "2026-01-01T00:00:00Z"})
    payload["reviewThreads"] = {
        "nodes": [
            {
                "isResolved": False,
                "isOutdated": False,
                "comments": {"nodes": [{"path": "src/app.py", "line": 1, "body": "legacy thread"}]},
            }
        ]
    }
    payload["statusCheckRollup"] = {
        "name": "ci",
        "state": "completed",
        "conclusion": "failure",
    }

    result = classify_pr_loop_state(payload)

    assert result.state == "clean"
    assert not result.blocked_reasons
