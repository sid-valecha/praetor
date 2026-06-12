from pathlib import Path
import subprocess
import time

from praetor.models import TaskResult

MODEL_ALIASES = {
    "spark": "haiku",
}


class ClaudeCodeAdapter:
    name = "claude"

    def __init__(
        self,
        *,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str = "auto",
    ) -> None:
        self.model = MODEL_ALIASES.get(model, model)
        self.effort = effort
        self.permission_mode = permission_mode

    def for_review(self) -> "ClaudeCodeAdapter":
        return ClaudeCodeAdapter(
            model=self.model,
            effort=self.effort,
            permission_mode="plan",
        )

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        start = time.monotonic()

        try:
            # --permission-mode auto: required because `-p` is non-interactive;
            # any prompt-requiring mode (default, acceptEdits) hangs since real
            # coding tasks routinely need Bash mid-agent (read repo state, run
            # tests, install deps). Praetor's trust boundary is the worktree
            # (parallel mode) or the user's Docker container (untrusted code) —
            # not per-action prompting.
            command = [
                "claude",
                "-p",
                "--permission-mode",
                self.permission_mode,
            ]
            if self.model is not None:
                command.extend(["--model", self.model])
            if self.effort is not None:
                command.extend(["--effort", self.effort])
            command.append(prompt)
            completed = subprocess.run(
                command,
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
