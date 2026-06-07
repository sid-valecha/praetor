from pathlib import Path

import pytest

from praetor.adapters import ClaudeCodeAdapter, CodexAdapter, MockAdapter, get_adapter


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


def test_codex_adapter_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="CodexAdapter is not implemented until v1.3"):
        CodexAdapter().exec("prompt", Path.cwd())


def test_get_adapter_claude() -> None:
    assert isinstance(get_adapter("claude"), ClaudeCodeAdapter)


def test_get_adapter_mock() -> None:
    assert isinstance(get_adapter("mock"), MockAdapter)


def test_get_adapter_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown adapter 'unknown'. Valid adapters:"):
        get_adapter("unknown")


def test_claude_adapter_has_correct_name() -> None:
    assert ClaudeCodeAdapter().name == "claude"
