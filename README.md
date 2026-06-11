# Praetor

[![CI](https://img.shields.io/github/actions/workflow/status/sid-valecha/praetor/ci.yml?branch=main&label=CI)](https://github.com/sid-valecha/praetor/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/praetor-cli)](https://pypi.org/project/praetor-cli/)
[![Python versions](https://img.shields.io/pypi/pyversions/praetor-cli)](https://pypi.org/project/praetor-cli/)
[![License: MIT](https://img.shields.io/github/license/sid-valecha/praetor)](LICENSE)

Praetor is a local-first task queue and DAG executor for coding agents; it is not another coding agent.

![Praetor parallel mode demo](docs/demo.gif)

## Install

Praetor requires Python 3.11 or newer.

Primary install (Python / cross-platform):

```bash
pipx install praetor-cli
```

macOS (via Homebrew tap):

```bash
brew tap sid-valecha/praetor
brew install praetor
```

Alternative install (no pipx):

```bash
pip install praetor-cli
```

The PyPI package is `praetor-cli`; the Homebrew formula is `sid-valecha/praetor/praetor`; the installed binary is `praetor`.

## Quickstart

Sequential mode is the default. It runs one ready task at a time in your current checkout.

```bash
cd your-project
praetor init
praetor add --title 'Implement auth module' --verify 'pytest tests/test_auth.py'
praetor status
praetor run
```

## Quickstart: Parallel Mode

Parallel mode runs eligible ready tasks in per-task git worktrees. Use it when independent tasks can be verified separately and merged back through Praetor. It requires a git repository and a base branch named `main`, unless you pass `--base-branch`.

Replace the `--verify` commands below with commands that exist in your project:

```bash
cd your-project
praetor init
praetor add --title "Refactor user module" --verify "pytest tests/users"
praetor add --title "Refactor billing module" --verify "pytest tests/billing"
praetor run --max-parallel 4
praetor status
praetor merge --all
```

`praetor run --max-parallel 4` enables parallel mode. Ready tasks with `parallel_ok: true` may run concurrently; tasks with `parallel_ok: false` run alone after the active pool drains. The default is still `--max-parallel 1`, which preserves sequential v0 behavior.

Manual merge is the default in parallel mode. After an agent exits, Praetor runs the task's `verify` command in that task's worktree, commits the verified worktree state to `praetor/<task-id>`, and marks the task `pending_merge`. `praetor status` shows `pending_merge` when verified work is waiting for integration and `merge_failed` when an attempted merge or post-merge verification failed and needs human recovery.

Merge all waiting tasks:

```bash
praetor merge --all
```

Merge selected tasks:

```bash
praetor merge implement-auth-module-a1b2c3d4
```

For an auto-merge run, opt in explicitly:

```bash
praetor run --max-parallel 4 --merge-strategy auto
```

## Watch Mode

`praetor loop` runs an initial drain, then stays alive and watches `.praetor/tasks/` for new task markdown files. Use it when a PM session or MCP orchestrator should keep feeding work without manually restarting the runner.

```bash
praetor loop --max-parallel 4
```

Pass `--once` to get the same single-pass behavior while exercising the loop command surface. In long-running mode, Ctrl-C requests cooperative shutdown: Praetor finishes any in-flight drain pass, does not start another one, and exits cleanly.

## CLI Reference

| Command | Key options | Purpose |
|---|---|---|
| `praetor` | `--install-completion`, `--show-completion` | Root command; Typer also exposes shell completion helpers. |
| `praetor init` | none | Create `.praetor/` state in the current repository. |
| `praetor add` | `--title`, `--depends-on`, `--verify`, `--parallel-ok/--no-parallel-ok`, `--merge-strategy`, `--agent` | Create a task markdown file under `.praetor/tasks/`. |
| `praetor status` | `--json` | Print task status. With `--json`, emit a JSON array (one object per task with all schema fields plus a derived `ready` bool) instead of the Rich table — for scripts, CI pipelines, and non-MCP agent callers. |
| `praetor run` | `--adapter`, `--max-parallel`, `--base-branch`, `--merge-strategy` | Drain ready tasks with the selected agent adapter. `--max-parallel 1` runs sequentially; values greater than 1 use worktrees. |
| `praetor loop` | `--adapter`, `--max-parallel`, `--base-branch`, `--merge-strategy`, `--once`, `--poll-interval` | Drain once, then keep watching `.praetor/tasks/` and drain again when new work appears. |
| `praetor merge` | `TASK_ID...`, `--all`, `--retry`, `--base-branch` | Merge `pending_merge` tasks back to the base branch. With `--retry`, also retry `merge_failed` tasks. |
| `praetor reset` | `TASK_ID...`, `--clean-worktree`, `--all-stale` | Reset failed, blocked, merge-failed, or stale-running tasks back to `pending`. |
| `praetor logs <task-id>` | `<task-id>` | Print the saved log for one task. |

## Merge Strategy

Parallel mode separates "verified in a worktree" from "integrated into the base branch." Manual merge is the default because auto-merging AI-authored commits to `main` is an explicit trust decision, not a safe default.

Each task has a `merge_strategy` field:

- `manual` parks verified work as `pending_merge`; a human runs `praetor merge <task-id>` or `praetor merge --all`.
- `auto` merges the task branch automatically after verify passes.

The CLI `--merge-strategy` flag on `praetor run` overrides all tasks for that run, regardless of their per-task field. For example, `praetor run --max-parallel 4 --merge-strategy auto` attempts to auto-merge every task completed during that run, including tasks whose frontmatter says `merge_strategy: manual`.

The `--merge-strategy` flag is only valid in parallel mode; passing it with `--max-parallel 1` is rejected with a clear error.

`praetor merge` uses `git merge --no-ff --no-edit` from `praetor/<task-id>` into the base branch. It refuses to merge if the base repo has uncommitted changes, records conflicts in the task log, and leaves the task as `merge_failed` for retry. After a successful merge, Praetor reruns that task's `verify` command on the base branch; a non-zero post-merge verify result also leaves the task as `merge_failed`.

## Worktrees

In parallel mode, each running task gets an isolated git worktree at:

```text
.praetor/worktrees/<task-id>/
```

Praetor records the task branch, base branch, and fork-point SHA in:

```text
.praetor/worktrees/<task-id>/.praetor-meta.json
```

Worktrees intentionally persist after task completion. They are needed for manual merge, conflict recovery, and post-mortem inspection. Disk-pressure cleanup is not implemented yet; it is tracked as [issue #7](https://github.com/sid-valecha/praetor/issues/7).

## Recovery Flows

If a task is `merge_failed`, inspect the task log and worktree, resolve the underlying conflict or base-branch issue, then retry:

```bash
praetor merge --retry <task-id>
```

If a previous runner crashed and left a task as `running`, `praetor run` fails closed with a stale-running error. Reset selected tasks or all stale-running tasks back to `pending`:

```bash
praetor reset <task-id>                    # set back to pending
praetor reset <task-id> --clean-worktree   # also remove the worktree
praetor reset --all-stale                  # reset every task currently in 'running' state
```

## Task File Schema

Tasks live in `.praetor/tasks/<id>.md`. The markdown body is the prompt given to the agent; the frontmatter is Praetor's task metadata.

```markdown
---
id: implement-auth-module-a1b2c3d4
status: pending
depends_on: []
parallel_ok: true
agent: claude
verify: pytest tests/test_auth.py
review: off
merge_strategy: manual
retry: 0
priority: normal
env: {}
context_files: []
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
| `status` | Persisted values are `pending`, `running`, `done`, `failed`, `blocked`, `pending_merge`, `merge_failed`, or `cancelled`. The DAG resolver and `praetor status` derive readiness from `pending` tasks whose dependencies are all `done`; readiness is not a persisted status. |
| `depends_on` | List of task ids that must be `done` before this task can run. |
| `parallel_ok` | Whether this task may run concurrently with other ready tasks. Default: `true`. Set `false` for cross-cutting or exclusive work. |
| `agent` | Intended agent for this task. Default: `claude`. `praetor run --adapter` selects the runtime adapter for the run. |
| `verify` | Shell command run after the agent exits. A non-zero exit keeps the task from completing. |
| `review` | v1+: reviewer mode, one of `off`, `lenient`, or `strict`. Present in v0 files for forward compatibility. |
| `merge_strategy` | Parallel-mode merge behavior, one of `manual` or `auto`. Default: `manual`. `praetor run --merge-strategy` overrides this field for all tasks in that run. |
| `retry` | Forward-compatible retry counter. Present in task files; retry policy is not implemented yet. |
| `priority` | Forward-compatible scheduling hint, one of `low`, `normal`, or `high`. Present in task files; ready-set priority ordering is not implemented yet. |
| `env` | Forward-compatible per-task environment map. Present in task files; runtime env propagation is tracked in [issue #13](https://github.com/sid-valecha/praetor/issues/13). |
| `context_files` | Forward-compatible list of context/file-scope hints. Present in task files; adapter use is deferred. |
| `created` | UTC timestamp for task creation. |
| `body` | Markdown body after the frontmatter. It is parsed into the task model and passed to the agent; it is not written as a frontmatter field. |

## How It Works

Praetor stores state as files under `.praetor/`: task markdown in `.praetor/tasks/`, per-task logs in `.praetor/logs/`, per-task worktrees in `.praetor/worktrees/`, and global run metadata in `.praetor/state.json`. The DAG resolver computes the ready set from `pending` tasks whose dependencies are all `done`.

With `--max-parallel 1`, Praetor runs one ready task at a time in the current checkout. After the agent exits, the task's `verify` command gates whether the task is marked `done` or `failed`.

With `--max-parallel > 1`, Praetor creates a worktree and branch for each dispatched task, runs the agent and verify command inside that worktree, commits the verified result, then either parks it as `pending_merge` or merges it automatically depending on the effective merge strategy. Once merged, Praetor reruns that task's `verify` command on the base branch before marking the task `done`.

## Limitations

v1 parallel execution is functional, but these items are intentionally not implemented yet:

- Dispatch-time conflict detection for overlapping task scopes: [issue #4](https://github.com/sid-valecha/praetor/issues/4)
- Worktree cleanup flag for disk-pressure management: [issue #7](https://github.com/sid-valecha/praetor/issues/7)
- Multi-OS CI: [issue #8](https://github.com/sid-valecha/praetor/issues/8)
- Per-task env propagation, run history, cancel command, blocked-task auto-unblock, and cost/token accounting are tracked in [issues](https://github.com/sid-valecha/praetor/issues).

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

[v1 parallel execution](roadmap.md#roadmap) adds worktrees, a worker pool, concurrent execution for eligible DAG siblings, and manual or automatic merge integration. [v1.1 MCP + Claude Code plugin](roadmap.md#roadmap) adds the MCP server and plugin distribution. v2 adds planner mode; v3 adds the meta loop and a GUI over the same file-based state.

## Use with Claude Code

Praetor ships a Claude Code plugin bundle in `plugin/`. It provides the task-authoring and plan-decomposition skills plus an MCP server config that runs `praetor mcp`; install or test it with `claude --plugin-dir ./plugin` after making sure the `praetor` executable is on `PATH`.

## License

See [LICENSE](LICENSE).
