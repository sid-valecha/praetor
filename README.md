# Praetor

[![CI](https://img.shields.io/github/actions/workflow/status/sid-valecha/praetor/ci.yml?branch=main&label=CI)](https://github.com/sid-valecha/praetor/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/praetor-cli)](https://pypi.org/project/praetor-cli/)
[![Python versions](https://img.shields.io/pypi/pyversions/praetor-cli)](https://pypi.org/project/praetor-cli/)
[![License: MIT](https://img.shields.io/github/license/sid-valecha/praetor)](LICENSE)

Praetor is a local-first closed-loop harness for coding agents; it is not another coding agent. It queues scoped work, runs agents in bounded loops, verifies results, records durable run evidence, and can require an independent reviewer before work is merged.

The package manager install paths (`pipx install praetor-cli`, Homebrew, `pip install praetor-cli`) track released versions from package distribution; `main` contains current development and can include unreleased behavior.

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

## Release and branch status

Praetor is local-first, but the release line is still separate from repository development:

- Installed binaries come from released versions on package hosts.
- The `main` branch may include unreleased work and documentation ahead of the latest published tag.
- Prefer released versions for automation and new projects; prefer `main` only when testing unreleased features intentionally.

## Quickstart

Sequential mode is the default. It runs one ready task at a time in your current checkout.

```bash
cd your-project
praetor init
praetor add --title 'Implement auth module' --verify 'pytest tests/test_auth.py'
praetor status
praetor run
```

### Safe first run

The demo flow is intended to be safe, deterministic, and easy to recover from:

1. Start in a disposable or sample repository first.
2. Start with `--max-parallel 1` to keep all work in the current checkout.
3. Use verifications that you can rerun locally (for example unit tests or lint), and make sure they are safe to execute.

```bash
cd your-project
praetor init
praetor add --title "Docs-only change smoke test" --verify "python -m compileall ."
praetor run --max-parallel 1
```

## Adapter prerequisites

Praetor delegates execution to adapters, and each adapter must be present and configured in your environment.

- Claude flow: install and authenticate the `claude` CLI, then run with `--adapter claude`.
- Codex flow: install and configure the `codex` CLI, then run with `--adapter codex`.

Run one-time adapter checks before broad adoption:

- Verify credentials and baseline command execution outside of valuable repositories first.
- Keep review mode enabled for risky work: `--reviewer-adapter` can be the same as or different from `--adapter`.
- Treat networked model calls as a trust boundary; keep logs and prompts in `.praetor` local and scoped.

## `praetor maintain --once` intake

`praetor maintain --once` is report-only by default; local task writes require explicit `--propose-tasks --write-tasks`.

- It performs a local one-pass maintainer triage and optional read-only GitHub intake.
- `--github`, `--github-pr`, and `--github-issue` are opt-in for GitHub input.
- `--propose-tasks` converts findings into deterministic repair proposals.
- `--write-tasks` writes deterministic `.praetor/tasks/*.md` files for proposal-backed work items.
- Proposal verify hints and final verify strategy are still owner-supplied outside this command.
- No autonomous GitHub mutations, pushes, review requests, or merges are part of the shipped maintainer flow.

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

`praetor run --max-parallel 4` enables parallel mode. Ready tasks with `parallel_ok: true` may run concurrently; tasks with `parallel_ok: false` run alone after the active pool drains. The default is still `--max-parallel 1`, which preserves sequential v0 behavior. Add `--max-iterations N` or `--max-runtime SECONDS` to stop dispatching new tasks after a bounded amount of work. With the Claude or Codex adapters, pass `--model MODEL` and `--effort LEVEL` to select the model and thinking budget for the executor. Praetor maps `--model spark` to Claude Code's `haiku` alias for Claude and `gpt-5.3-codex-spark` for Codex; other model strings pass through unchanged.

Manual merge is the default in parallel mode. After an agent exits, Praetor runs the task's `verify` command in that task's worktree, runs the optional reviewer gate, commits the accepted worktree state to `praetor/<task-id>`, and marks the task `pending_merge`. `praetor status` shows `pending_merge` when accepted work is waiting for integration and `merge_failed` when an attempted merge or post-merge verification failed and needs human recovery.

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

`--max-iterations` and `--max-runtime` also work with `praetor loop`; they apply to each drain pass, not to the whole watcher lifetime.

## CLI Reference

| Command | Key options | Purpose |
|---|---|---|
| `praetor` | `--install-completion`, `--show-completion` | Root command; Typer also exposes shell completion helpers. |
| `praetor init` | none | Create `.praetor/` state in the current repository. |
| `praetor add` | `--title`, `--depends-on`, `--verify`, `--parallel-ok/--no-parallel-ok`, `--merge-strategy`, `--review`, `--agent` | Create a task markdown file under `.praetor/tasks/`. |
| `praetor status` | `--json` | Print task status. With `--json`, emit a JSON array (one object per task with all schema fields plus a derived `ready` bool) instead of the Rich table — for scripts, CI pipelines, and non-MCP agent callers. |
| `praetor run` | `--adapter`, `--model`, `--effort`, `--reviewer-adapter`, `--reviewer-model`, `--reviewer-effort`, `--max-parallel`, `--base-branch`, `--merge-strategy`, `--max-iterations`, `--max-runtime` | Drain ready tasks with the selected agent adapter. `--max-parallel 1` runs sequentially; values greater than 1 use worktrees. `--model` and `--effort` are Claude/Codex executor options; reviewer options override the review route for that run. |
| `praetor loop` | `--adapter`, `--model`, `--effort`, `--reviewer-adapter`, `--reviewer-model`, `--reviewer-effort`, `--max-parallel`, `--base-branch`, `--merge-strategy`, `--once`, `--poll-interval`, `--max-iterations`, `--max-runtime` | Drain once, then keep watching `.praetor/tasks/` and drain again when new work appears. Executor and reviewer model/effort flags follow the same semantics as `praetor run`. |
| `praetor maintain` | `--once`, `--github`, `--github-pr`, `--github-issue`, `--propose-tasks`, `--write-tasks`, `--json` | Read-only maintainer intake for local state triage and optional read-only GitHub issue/PR/CI intake. `--propose-tasks` converts findings into deterministic repair proposals; `--write-tasks` materializes generated proposals as deterministic `.praetor/tasks/*.md` files. PR-loop state classification exists in the `praetor.pr_loop_state` library module, but CLI exposure is tracked separately. Conservatively, the shipped command is `report-only` by default and does not perform autonomous GitHub mutation, branch push, review-request, or merge actions. |
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

## Review Gate

Set `review: lenient` or `review: strict` to run an adversarial reviewer after the agent exits and the verify command passes. Praetor has two roles: executor and reviewer. `--adapter` chooses who writes. Review starts a fresh independent checker; by default, that checker uses the same adapter/model/effort as the executor. Power users can route review to another adapter with `--reviewer-adapter`, `--reviewer-model`, and `--reviewer-effort`.

```bash
praetor run --adapter claude
praetor run --adapter codex
praetor run --adapter claude --reviewer-adapter codex
praetor run --adapter codex --reviewer-adapter claude
```

The first two commands are the normal path: same-agent-family executor and fresh reviewer. The last two commands are optional cross-agent review. The Claude adapter switches reviewer calls to read-only plan permission mode. The Codex adapter uses read-only `codex exec` with a Praetor review output schema for reviewer calls. The reviewer must return structured JSON. `pass` continues the normal completion or merge path. `needs_revision` marks the task `review_failed` and leaves dependents pending. `blocked` marks the task blocked and propagates that block to dependents.

Review always wins over auto-merge. If a task has `merge_strategy: auto` but the reviewer rejects it, Praetor does not commit or merge the work.

### Optional Bridge Plugins

Praetor does not require bridge plugins for native review. If you are working directly in Claude Code and want to call Codex from that host, use [`openai/codex-plugin-cc`](https://github.com/openai/codex-plugin-cc). If you are working directly in Codex and want to call Claude Code from that host, use `cc-plugin-codex`.

Those plugins are host-workflow helpers. Praetor core uses native adapters and records executor/reviewer evidence in `.praetor/runs/*.json`.

## Run History

`.praetor` is local state storage and is not intended as a public artifact:

- `.praetor/tasks/` stores task payloads with prompts and checks.
- `.praetor/runs/` stores verifier/reviewer evidence and timing traces.
- `.praetor/logs/` stores terminal logs.
- `.praetor/worktrees/` stores per-task git worktrees used during parallel execution.

Do not publish `.praetor` by default. Keep it in local-only directories and out of file shares where project-restricted evidence, secrets, or token-bearing outputs could leak.

Every `praetor run` and drain pass writes durable evidence to:

```text
.praetor/runs/<run-id>.json
```

Run records include task attempts, executor adapter/model/effort, verify exit code, reviewer adapter/model/effort, review verdict and findings, merge outcome, timestamps, and final run status. This is the audit trail for closed-loop execution and the substrate for future cost tracking, reflection, and GUI views.

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
| `status` | Persisted values are `pending`, `running`, `done`, `failed`, `blocked`, `pending_merge`, `merge_failed`, `review_failed`, or `cancelled`. The DAG resolver and `praetor status` derive readiness from `pending` tasks whose dependencies are all `done`; readiness is not a persisted status. |
| `depends_on` | List of task ids that must be `done` before this task can run. |
| `parallel_ok` | Whether this task may run concurrently with other ready tasks. Default: `true`. Set `false` for cross-cutting or exclusive work. |
| `agent` | Intended agent for this task. Default: `claude`. `praetor run --adapter` selects the runtime adapter for the run. |
| `verify` | Shell command run after the agent exits. A non-zero exit keeps the task from completing. |
| `review` | Reviewer mode, one of `off`, `lenient`, or `strict`. Non-`off` values run the post-verify reviewer gate. |
| `merge_strategy` | Parallel-mode merge behavior, one of `manual` or `auto`. Default: `manual`. `praetor run --merge-strategy` overrides this field for all tasks in that run. |
| `retry` | Forward-compatible retry counter. Present in task files; retry policy is not implemented yet. |
| `priority` | Forward-compatible scheduling hint, one of `low`, `normal`, or `high`. Present in task files; ready-set priority ordering is not implemented yet. |
| `env` | Forward-compatible per-task environment map. Present in task files; runtime env propagation is tracked in [issue #13](https://github.com/sid-valecha/praetor/issues/13). |
| `context_files` | Forward-compatible list of context/file-scope hints. Present in task files; adapter use is deferred. |
| `created` | UTC timestamp for task creation. |
| `body` | Markdown body after the frontmatter. It is parsed into the task model and passed to the agent; it is not written as a frontmatter field. |

## How It Works

Praetor stores state as files under `.praetor/`: task markdown in `.praetor/tasks/`, per-task logs in `.praetor/logs/`, per-run JSON records in `.praetor/runs/`, per-task worktrees in `.praetor/worktrees/`, and global run metadata in `.praetor/state.json`. The DAG resolver computes the ready set from `pending` tasks whose dependencies are all `done`.

With `--max-parallel 1`, Praetor runs one ready task at a time in the current checkout. After the agent exits, the task's `verify` command and optional reviewer gate determine whether the task is marked `done`, `failed`, `review_failed`, or `blocked`.

With `--max-parallel > 1`, Praetor creates a worktree and branch for each dispatched task, runs the agent and verify command inside that worktree, runs the optional reviewer, commits the verified and reviewed result, then either parks it as `pending_merge` or merges it automatically depending on the effective merge strategy. Once merged, Praetor reruns that task's `verify` command on the base branch before marking the task `done`.

## Limitations

v1 parallel execution is functional, but these items are intentionally not implemented yet:

- Dispatch-time conflict detection for overlapping task scopes: [issue #4](https://github.com/sid-valecha/praetor/issues/4)
- Worktree cleanup flag for disk-pressure management: [issue #7](https://github.com/sid-valecha/praetor/issues/7)
- Multi-OS CI: [issue #8](https://github.com/sid-valecha/praetor/issues/8)
- Per-task env propagation, cancel command, blocked-task auto-unblock, and cost/token accounting are tracked in [issues](https://github.com/sid-valecha/praetor/issues).

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

[v1 parallel execution](roadmap.md#roadmap) added worktrees, a worker pool, concurrent execution for eligible DAG siblings, and manual or automatic merge integration. [v1.1 MCP + Claude Code plugin](roadmap.md#roadmap) added the MCP server and plugin distribution. v1.2 adds trustworthy closed-loop execution with run history, reviewer gating, and guardrails. v2 adds planner mode; v3 adds the meta loop and a GUI over the same file-based state.

## Use with Claude Code

Praetor ships a Claude Code plugin bundle in `plugin/`. It provides task-authoring, plan-decomposition, and task-review skills plus an MCP server config that runs `praetor mcp`; install or test it with `claude --plugin-dir ./plugin` after making sure the `praetor` executable is on `PATH`.

## License

See [LICENSE](LICENSE).
