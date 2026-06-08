# Praetor

Praetor is a local-first task queue and DAG executor for coding agents; it is not another coding agent.

## Install

Praetor requires Python 3.11 or newer.

Primary install:

```bash
pipx install praetor-cli
```

Alternative install:

```bash
pip install praetor-cli
```

The PyPI package is `praetor-cli`; the installed binary is `praetor`.

## Quickstart

```bash
cd your-project
praetor init
praetor add --title 'Implement auth module' --verify 'pytest tests/test_auth.py'
praetor status
praetor run
```

## CLI Reference

| Command | Key options | Purpose |
|---|---|---|
| `praetor init` | none | Create `.praetor/` state in the current repository. |
| `praetor add` | `--title`, `--depends-on`, `--verify`, `--agent` | Create a task markdown file under `.praetor/tasks/`. |
| `praetor status` | none | Print task status, dependencies, and verify commands. |
| `praetor run` | `--adapter` | Drain ready tasks sequentially with the selected agent adapter. |
| `praetor logs <task-id>` | `<task-id>` | Print the saved log for one task. |

## Task File Schema

Tasks live in `.praetor/tasks/<id>.md`. The markdown body is the prompt given to the agent; the frontmatter is Praetor's task metadata.

```markdown
---
id: implement-auth-module-a1b2c3d4
status: pending
depends_on: []
parallel_ok: false
agent: claude
verify: pytest tests/test_auth.py
review: off
created: 2026-06-08T14:22:00Z
---

# Implement auth module

## What to do
Add the authentication module and wire it into the existing app.

## How to verify
Run `pytest tests/test_auth.py` and confirm it passes.

## Proof when complete
Summarize the files changed and include the verify output.
```

| Field | Description |
|---|---|
| `id` | Stable task id; also used as the task filename stem. |
| `status` | `pending`, `running`, `done`, `failed`, or `blocked`. `ready` is derived by the DAG resolver, not written by v0. |
| `depends_on` | List of task ids that must be `done` before this task can run. |
| `parallel_ok` | v1+: whether this task may run concurrently with other ready tasks. Present in v0 files for forward compatibility. |
| `agent` | v1+: intended agent for this task. v0 accepts the field, while `praetor run --adapter` selects the runtime adapter. |
| `verify` | Shell command run after the agent exits. A non-zero exit keeps the task from completing. |
| `review` | v1+: reviewer mode, one of `off`, `lenient`, or `strict`. Present in v0 files for forward compatibility. |
| `created` | UTC timestamp for task creation. |

## How It Works

Praetor stores state as files under `.praetor/`: task markdown in `.praetor/tasks/`, per-task logs in `.praetor/logs/`, and global run metadata in `.praetor/state.json`. The DAG resolver computes the ready set from `pending` tasks whose dependencies are all `done`. v0 runs one ready task at a time. After the agent exits, the task's `verify` command gates whether the task is marked `done` or `failed`.

## Docker / Sandboxed Runs

Build the image:

```bash
docker build -t praetor-cli .
```

Run Praetor against the current repository:

```bash
docker run --rm -it -v "$PWD:/repo" -w /repo praetor-cli praetor run --adapter claude
```

Use the container as the trust boundary for permission-bypassing agent runs such as `--dangerously-skip-permissions`; run that bypass inside the container, not on the host.

```bash
docker run --rm -it -v "$PWD:/repo" -w /repo praetor-cli \
  claude --dangerously-skip-permissions
```

## Roadmap

[v1 parallel execution](roadmap.md#roadmap) adds worktrees, a worker pool, and concurrent execution for eligible DAG siblings. [v1.1 MCP + Claude Code plugin](roadmap.md#roadmap) adds the MCP server and plugin distribution. v2 adds planner mode; v3 adds the meta loop and a GUI over the same file-based state.

## License

See [LICENSE](LICENSE).
