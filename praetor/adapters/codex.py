from pathlib import Path

from praetor.models import TaskResult


class CodexAdapter:
    name = "codex"

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        raise NotImplementedError("CodexAdapter is not implemented until v1.3")
