from dataclasses import dataclass
from pathlib import Path
import tomllib


DEFAULT_MAX_REVIEW_RETRIES = 1


@dataclass(frozen=True)
class PraetorConfig:
    max_review_retries: int = DEFAULT_MAX_REVIEW_RETRIES


def load_config(repo_root: Path) -> PraetorConfig:
    config_path = repo_root / ".praetor" / "config.toml"
    if not config_path.exists():
        return PraetorConfig()

    try:
        data = tomllib.loads(config_path.read_text())
    except tomllib.TOMLDecodeError as exc:
        msg = f"Invalid .praetor/config.toml: {exc}"
        raise ValueError(msg) from exc

    allowed_keys = {"max_review_retries"}
    unknown_keys = set(data) - allowed_keys
    if unknown_keys:
        joined = ", ".join(sorted(unknown_keys))
        msg = f"Unsupported config key in .praetor/config.toml: {joined}"
        raise ValueError(msg)

    if "max_review_retries" not in data:
        return PraetorConfig()

    return PraetorConfig(
        max_review_retries=_validate_max_review_retries(
            data["max_review_retries"],
            source=".praetor/config.toml",
        )
    )


def resolve_max_review_retries(repo_root: Path, explicit: int | None) -> int:
    if explicit is not None:
        return _validate_max_review_retries(explicit, source="explicit value")
    return load_config(repo_root).max_review_retries


def _validate_max_review_retries(value: object, *, source: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"max_review_retries in {source} must be a non-negative integer"
        raise ValueError(msg)
    if value < 0:
        msg = f"max_review_retries in {source} must be >= 0"
        raise ValueError(msg)
    return value
