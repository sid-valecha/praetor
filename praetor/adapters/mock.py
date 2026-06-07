from pathlib import Path

from praetor.models import TaskResult


class MockAdapter:
    name = "mock"

    def __init__(
        self,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        duration_ms: int = 10,
    ) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        return TaskResult(
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            duration_ms=self.duration_ms,
            diff=None,
        )
