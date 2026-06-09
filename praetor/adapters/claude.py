from pathlib import Path
import subprocess
import time

from praetor.models import TaskResult


class ClaudeCodeAdapter:
    name = "claude"

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        start = time.monotonic()

        try:
            # --permission-mode auto: required because `-p` is non-interactive;
            # any prompt-requiring mode (default, acceptEdits) hangs since real
            # coding tasks routinely need Bash mid-agent (read repo state, run
            # tests, install deps). Praetor's trust boundary is the worktree
            # (parallel mode) or the user's Docker container (untrusted code) —
            # not per-action prompting.
            completed = subprocess.run(
                ["claude", "-p", "--permission-mode", "auto", prompt],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return TaskResult(
                exit_code=1,
                stdout="",
                stderr=str(exc),
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
