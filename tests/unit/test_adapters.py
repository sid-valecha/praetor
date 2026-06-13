from pathlib import Path
import subprocess

import pytest

from praetor.adapters import (
    ClaudeCodeAdapter,
    CodexAdapter,
    MockAdapter,
    get_adapter,
    resolve_reviewer_adapter,
)


def test_mock_adapter_returns_configured_values() -> None:
    adapter = MockAdapter(exit_code=2, stdout="out", stderr="err", duration_ms=42)

    result = adapter.exec("prompt", Path.cwd())

    assert result.exit_code == 2
    assert result.stdout == "out"
    assert result.stderr == "err"
    assert result.duration_ms == 42
    assert result.diff is None


def test_mock_adapter_defaults() -> None:
    result = MockAdapter().exec("prompt", Path.cwd())

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert result.duration_ms == 10
    assert result.diff is None


def test_codex_adapter_exec_invokes_codex_exec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, returncode=0, stdout="done\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = CodexAdapter(model="gpt-5.4-mini", effort="medium").exec("do work", tmp_path)

    assert result.exit_code == 0
    assert result.stdout == "done\n"
    assert captured["command"] == [
        "codex",
        "exec",
        "--cd",
        str(tmp_path),
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
        "--model",
        "gpt-5.4-mini",
        "-c",
        "model_reasoning_effort='medium'",
        "-",
    ]
    assert captured["input"] == "do work"
    assert captured["cwd"] == tmp_path
    assert captured["capture_output"] is True
    assert captured["text"] is True


def test_codex_adapter_maps_spark_model_alias() -> None:
    adapter = CodexAdapter(model="spark")

    assert adapter.model == "gpt-5.3-codex-spark"


def test_codex_adapter_for_review_invokes_read_only_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout='{"verdict":"pass"}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = CodexAdapter(model="gpt-5.4-mini", effort="high").for_review()
    result = adapter.exec("review this", tmp_path)

    assert result.exit_code == 0
    command = captured["command"]
    assert command[:8] == [
        "codex",
        "exec",
        "--cd",
        str(tmp_path),
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
    ]
    assert "--output-schema" in command
    assert command[-5:] == [
        "-c",
        "model='gpt-5.4-mini'",
        "-c",
        "model_reasoning_effort='high'",
        "-",
    ]
    assert captured["input"] == "review this"
    assert captured["cwd"] == tmp_path
    assert result.stdout == '{"verdict":"pass"}\n'


def test_get_adapter_claude() -> None:
    assert isinstance(get_adapter("claude"), ClaudeCodeAdapter)


def test_get_adapter_claude_applies_model_and_effort() -> None:
    adapter = get_adapter("claude", model="haiku", effort="low")

    assert isinstance(adapter, ClaudeCodeAdapter)
    assert adapter.model == "haiku"
    assert adapter.effort == "low"


def test_get_adapter_claude_maps_spark_to_haiku() -> None:
    adapter = get_adapter("claude", model="spark")

    assert isinstance(adapter, ClaudeCodeAdapter)
    assert adapter.model == "haiku"


def test_get_adapter_codex_applies_model_and_effort() -> None:
    adapter = get_adapter("codex", model="spark", effort="high")

    assert isinstance(adapter, CodexAdapter)
    assert adapter.model == "gpt-5.3-codex-spark"
    assert adapter.effort == "high"


def test_get_adapter_mock() -> None:
    assert isinstance(get_adapter("mock"), MockAdapter)


def test_get_adapter_rejects_model_for_mock() -> None:
    with pytest.raises(ValueError, match="only supported by the claude and codex adapters"):
        get_adapter("mock", model="haiku")


def test_resolve_reviewer_adapter_returns_none_without_reviewer_options() -> None:
    assert (
        resolve_reviewer_adapter(
            executor_adapter="claude",
            executor_model="haiku",
            executor_effort="low",
            reviewer_adapter=None,
            reviewer_model=None,
            reviewer_effort=None,
        )
        is None
    )


def test_resolve_reviewer_adapter_inherits_executor_claude_options() -> None:
    reviewer = resolve_reviewer_adapter(
        executor_adapter="claude",
        executor_model="spark",
        executor_effort="low",
        reviewer_adapter=None,
        reviewer_model=None,
        reviewer_effort="high",
    )

    assert isinstance(reviewer, ClaudeCodeAdapter)
    assert reviewer.model == "haiku"
    assert reviewer.effort == "high"


def test_resolve_reviewer_adapter_does_not_inherit_across_adapter_names() -> None:
    reviewer = resolve_reviewer_adapter(
        executor_adapter="claude",
        executor_model="haiku",
        executor_effort="low",
        reviewer_adapter="mock",
        reviewer_model=None,
        reviewer_effort=None,
    )

    assert isinstance(reviewer, MockAdapter)


def test_resolve_reviewer_adapter_rejects_model_for_mock_reviewer() -> None:
    with pytest.raises(ValueError, match="only supported by the claude and codex adapters"):
        resolve_reviewer_adapter(
            executor_adapter="mock",
            executor_model=None,
            executor_effort=None,
            reviewer_adapter=None,
            reviewer_model="haiku",
            reviewer_effort=None,
        )


def test_get_adapter_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown adapter 'unknown'. Valid adapters:"):
        get_adapter("unknown")


def test_claude_adapter_has_correct_name() -> None:
    assert ClaudeCodeAdapter().name == "claude"


def test_claude_adapter_returns_task_result_on_os_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_file_not_found(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("claude not found")

    monkeypatch.setattr(subprocess, "run", raise_file_not_found)

    result = ClaudeCodeAdapter().exec("prompt", tmp_path)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "claude not found" in result.stderr
    assert result.duration_ms >= 0
    assert result.diff is None


def test_claude_adapter_passes_model_effort_and_permission_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ClaudeCodeAdapter(model="haiku", effort="low").exec("prompt", tmp_path)

    assert result.exit_code == 0
    assert commands == [
        [
            "claude",
            "-p",
            "--permission-mode",
            "auto",
            "--model",
            "haiku",
            "--effort",
            "low",
            "prompt",
        ]
    ]


def test_claude_review_adapter_is_plan_mode_with_same_model_and_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = ClaudeCodeAdapter(model="opus", effort="high").for_review()
    result = adapter.exec("review prompt", tmp_path)

    assert result.exit_code == 0
    assert commands == [
        [
            "claude",
            "-p",
            "--permission-mode",
            "plan",
            "--model",
            "opus",
            "--effort",
            "high",
            "review prompt",
        ]
    ]
