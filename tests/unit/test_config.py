from pathlib import Path

import pytest

from praetor.config import (
    DEFAULT_MAX_REVIEW_RETRIES,
    load_config,
    resolve_max_review_retries,
)


def test_missing_config_uses_default(tmp_path: Path) -> None:
    assert load_config(tmp_path).max_review_retries == DEFAULT_MAX_REVIEW_RETRIES
    assert resolve_max_review_retries(tmp_path, None) == DEFAULT_MAX_REVIEW_RETRIES


def test_config_reads_max_review_retries(tmp_path: Path) -> None:
    _write_config(tmp_path, "max_review_retries = 2\n")

    assert load_config(tmp_path).max_review_retries == 2
    assert resolve_max_review_retries(tmp_path, None) == 2


def test_config_zero_disables_review_retries(tmp_path: Path) -> None:
    _write_config(tmp_path, "max_review_retries = 0\n")

    assert load_config(tmp_path).max_review_retries == 0
    assert resolve_max_review_retries(tmp_path, None) == 0


def test_explicit_value_overrides_config_including_zero(tmp_path: Path) -> None:
    _write_config(tmp_path, "max_review_retries = 2\n")

    assert resolve_max_review_retries(tmp_path, 0) == 0
    assert resolve_max_review_retries(tmp_path, 3) == 3


@pytest.mark.parametrize(
    "content",
    [
        "max_review_retries = -1\n",
        "max_review_retries = true\n",
    ],
)
def test_invalid_config_value_is_rejected(tmp_path: Path, content: str) -> None:
    _write_config(tmp_path, content)

    with pytest.raises(ValueError, match="max_review_retries"):
        load_config(tmp_path)


@pytest.mark.parametrize("explicit", [-1, True])
def test_invalid_explicit_value_is_rejected(tmp_path: Path, explicit: int | bool) -> None:
    with pytest.raises(ValueError, match="max_review_retries"):
        resolve_max_review_retries(tmp_path, explicit)


def test_malformed_config_raises_clear_value_error(tmp_path: Path) -> None:
    _write_config(tmp_path, "max_review_retries = \n")

    with pytest.raises(ValueError, match=".praetor/config.toml"):
        load_config(tmp_path)


def test_unknown_config_key_is_rejected(tmp_path: Path) -> None:
    _write_config(tmp_path, "max_review_retries = 1\nother = 2\n")

    with pytest.raises(ValueError, match="Unsupported config key"):
        load_config(tmp_path)


def _write_config(repo_root: Path, content: str) -> None:
    config_path = repo_root / ".praetor" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(content)
