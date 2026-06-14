# AGENTS.md — Praetor

Instructions for any coding agent (Claude Code, Codex, Aider, etc.) working in this repo.

## Environment

**Use the `praetor` conda environment.** Do not use system Python, raw `venv`, or `uv`.

```bash
# First time only
conda create -n praetor python=3.11 -y

# Every session
conda activate praetor
pip install -e ".[dev]"
```

All `python`, `pip`, `pytest`, `ruff`, and `praetor` commands must be run with the `praetor` env active. If you're unsure, run `conda info --envs` and confirm `praetor *` shows the active marker.

## Stack

- Python 3.11+
- Typer (CLI), Pydantic (schema), python-frontmatter (parsing), Rich (output)
- pytest (tests), Ruff (lint + format)
- Package name on PyPI: `praetor-cli`; binary: `praetor`

## Source of truth

In Sid's maintainer checkout, read the local living docs in order before doing project-maintenance work:

1. `Handoff.md` — current state, decisions, next action
2. `roadmap.md` — design spec, v0 → v3 roadmap
3. `plan.md` — phased execution plan (P0–P15) with ownership tags

These three files override anything in this AGENTS.md if they conflict — they are living, this is static.

In public clones, these local planning files may be absent or intentionally private. Use `README.md`, `CONTRIBUTING.md`, and `SECURITY.md` as the public source of truth.

## Planning persistence

When a conversation produces a new product direction, phase, architectural decision, system behavior, or sequencing change, update the living docs in the same turn unless the user explicitly says not to. Use `roadmap.md` for public product direction, `plan.md` for execution phases, `design-notes.md` for locked decisions/failure modes, and `Handoff.md` for current PM state. Keep updates tight; do not create extra planning files unless the user asks.

## Conventions

- **State location:** `.praetor/` per-repo, gitignored
- **Frontmatter fields:** include current schema fields from day one (`parallel_ok`, `review`, `agent`, `merge_strategy`, `retry`, `priority`, `env`, `context_files`)
- **Filenames:** snake_case for Python modules
- **Commits:** the user commits manually; agents do not commit unless explicitly asked
- **No emojis in code or commits** unless the user asks
- **Test before declaring done:** every phase has a verify command in `plan.md` — run it

## Role discipline

- The PM session (long-lived Claude) dispatches and reviews; it does **not** write production code in `praetor/`
- Implementer subprocesses (scoped Claude or Codex sessions) write code within a single phase scope and report back
- Never trust self-reporting — the PM reviews the diff against the phase's deliverables and runs the verify command
