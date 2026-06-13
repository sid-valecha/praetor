from pathlib import Path
import json
import subprocess
import tempfile
import time

from praetor.models import TaskResult

MODEL_ALIASES = {
    "spark": "gpt-5.3-codex-spark",
}

REVIEW_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "severity", "summary", "findings"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "needs_revision", "blocked"]},
        "severity": {"type": "string", "enum": ["info", "warning", "error"]},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "message", "file", "line", "recommendation"],
                "properties": {
                    "severity": {"type": "string", "enum": ["info", "warning", "error"]},
                    "message": {"type": "string"},
                    "file": {"type": ["string", "null"]},
                    "line": {"type": ["integer", "null"]},
                    "recommendation": {"type": ["string", "null"]},
                },
            },
        },
    },
}


class CodexAdapter:
    name = "codex"

    def __init__(
        self,
        *,
        model: str | None = None,
        effort: str | None = None,
        review_mode: bool = False,
    ) -> None:
        self.model = MODEL_ALIASES.get(model, model)
        self.effort = effort
        self.review_mode = review_mode

    def for_review(self) -> "CodexAdapter":
        return CodexAdapter(
            model=self.model,
            effort=self.effort,
            review_mode=True,
        )

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        start = time.monotonic()
        schema_path = None

        try:
            schema_path = self._write_review_schema() if self.review_mode else None
            command = self._command(cwd, schema_path=schema_path)
            completed = subprocess.run(
                command,
                input=prompt,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return TaskResult(
                exit_code=124,
                stdout="",
                stderr=f"codex CLI timed out after {exc.timeout} seconds",
                duration_ms=duration_ms,
                diff=None,
            )
        except FileNotFoundError:
            duration_ms = int((time.monotonic() - start) * 1000)
            return TaskResult(
                exit_code=127,
                stdout="",
                stderr="codex CLI not found on PATH",
                duration_ms=duration_ms,
                diff=None,
            )
        except OSError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return TaskResult(
                exit_code=1,
                stdout="",
                stderr=str(exc),
                duration_ms=duration_ms,
                diff=None,
            )
        finally:
            if schema_path is not None:
                schema_path.unlink(missing_ok=True)

        duration_ms = int((time.monotonic() - start) * 1000)
        return TaskResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_ms=duration_ms,
            diff=None,
        )

    def _command(self, cwd: Path, *, schema_path: Path | None = None) -> list[str]:
        if self.review_mode:
            command = [
                "codex",
                "exec",
                "--cd",
                str(cwd),
                "--sandbox",
                "read-only",
                "-c",
                _codex_config("approval_policy", "never"),
            ]
            if schema_path is not None:
                command.extend(["--output-schema", str(schema_path)])
        else:
            command = [
                "codex",
                "exec",
                "--cd",
                str(cwd),
                "--sandbox",
                "workspace-write",
                "-c",
                _codex_config("approval_policy", "never"),
            ]

        if self.model is not None:
            command.extend(["--model", self.model])
        if self.effort is not None:
            command.extend(["-c", _codex_config("model_reasoning_effort", self.effort)])
        command.append("-")
        return command

    def _write_review_schema(self) -> Path:
        schema_file = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="praetor-codex-review-",
            delete=False,
        )
        with schema_file:
            json.dump(REVIEW_OUTPUT_SCHEMA, schema_file)
        return Path(schema_file.name)


def _codex_config(key: str, value: str) -> str:
    return f"{key}={json.dumps(value)}"
