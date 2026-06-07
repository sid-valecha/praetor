from pathlib import Path
import subprocess
import time

from praetor.models import TaskResult


class ClaudeCodeAdapter:
    name = "claude"

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        start = time.monotonic()

        try:
            completed = subprocess.run(
                ["claude", "-p", prompt],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            return TaskResult(
                exit_code=1,
                stdout="",
                stderr="timed out",
                duration_ms=duration_ms,
                diff=None,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        return TaskResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_ms=duration_ms,
            diff=None,
        )
