# Praetor — Design Spec

## Context

Coding agents today force a binary choice: full autonomy (one giant prompt, agent drifts, weak trust) or constant babysitting (manual prompt for each next step, exhausting). The middle ground — **queue up scoped work, let an implementer agent drain the queue, verify each result** — has no good tooling. Simon Last's thread (Notion) frames this as "your job is to add vetted tasks faster than the agent completes them; the implementer should never idle." That maps directly to the user's lived pain: finishing one feature with Claude Code and then having to hand-prompt the next instead of having five queued.

Praetor is the closed-loop harness around existing coding agents (Claude Code first, agent-agnostic by design). It is **not** another coding agent. It is the queue, the DAG executor, the verification/review harness, durable run ledger, and Claude Code plugin that teaches sessions how to use it.

The goal for the weekend is a solid **foundation**, not a demo. Every version after Monday extends the same base without rewrites.

## Goals

- Persistent task queue with sequential + parallel execution against headless coding-agent subprocesses
- File-based state in `.praetor/` — no DB, no daemon, no cloud
- Agent-agnostic via a thin adapter interface (Claude Code first; Codex, Aider follow)
- Ship as a Claude Code plugin (CLI + skills + MCP) for tier-2 adoption; plain CLI works standalone for tier-1 / non-Claude users
- Architecture supports reviewer agent, planner mode, GUI, meta-loop later — without schema migration
- Make the trust gate the product: executor output must survive verify commands, adversarial review, retry limits, and merge policy before it is considered shippable
- Support power-user operation: many agents, isolated worktrees, optional container execution, and PM-orchestrated delegation without constant permission babysitting

## Non-goals

- Building a coding agent
- Auto-decomposition of fuzzy goals into tasks (the user or their PM Claude does this)
- Hosted memory/vector service as a default dependency
- Multi-repo, remote execution, team collaboration (v3+ at earliest)

## Usage tiers

1. **Plain CLI** — `praetor add`, edit task markdown, `praetor run`. Works with any agent. Zero Claude assumptions.
2. **Claude Code plugin user** — installs plugin; their normal session uses bundled skills to author tasks, decompose goals, and drive the queue via MCP. No "PM session" terminology required.
3. **Power user** — dedicated long-running conductor session + worker pool, fully out of the loop. Same primitives, plus explicit retry budgets, cross-model review, and optional container isolation once those land.

All three share identical CLI substrate and state files.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Claude Code session (tier 2/3)                 │
│  ├── praetor:* skills (task authoring, etc.)    │
│  └── MCP client → praetor mcp server            │
└─────────────────┬───────────────────────────────┘
                  │
        ┌─────────▼──────────┐
        │  praetor CLI core  │  ← shared service layer
        │  ├── State (FS)    │
        │  ├── DAG resolver  │
        │  └── Runner        │
        └────────┬───────────┘
                 │
        ┌────────▼──────────┐
        │  AgentAdapter     │
        │  ├── ClaudeCode   │  ✓ v0
        │  ├── Codex        │  ✓ v1.3
        │  └── (interface)  │
        └────────┬──────────┘
                 │
        ┌────────▼──────────┐
        │  Worktree manager │  ✓ v1
        │  (parallel only)  │
        └───────────────────┘
```

**Stack:** Python 3.11+ in the `praetor` conda environment (Typer, Pydantic, python-frontmatter, Rich, pytest, Ruff).
**State location:** `.praetor/` per-repo, gitignored by default.
**State files:** `tasks/<id>.md`, `logs/<id>.log`, `state.json`, `context.md`.

`tasks/<id>.md` is the source of truth for task status and task metadata. `state.json` is for global/run metadata only, such as the last run timestamp, active run id, CLI version, and last aggregate verify result. Do not duplicate per-task status in `state.json`.

## Task schema

`.praetor/tasks/<id>.md`:

```markdown
---
id: 003-stripe-webhook
status: pending          # pending | running | pending_merge | merge_failed | review_failed | cancelled | done | failed | blocked
depends_on: [002-stripe-keys]
parallel_ok: true        # honored by v1+
agent: claude            # claude | codex | aider — v1.3+
verify: pytest tests/billing/test_webhook.py
review: off              # off | lenient | strict — v1.2+
merge_strategy: manual   # manual | auto — honored by v1+
retry: 0                 # future retry policy metadata
priority: normal         # low | normal | high — future scheduling hint
env: {}                  # future per-task env propagation
context_files: []        # future file/context hints
created: 2026-05-23T14:22:00Z
---

# Implement Stripe webhook handler

## What to do
[prompt body — the actual task description for the agent]

## How to verify
[explicit success criteria — checked by `verify` command]

## Proof when complete
[what artifacts/output prove this is done]
```

Forward-compatible frontmatter fields (`parallel_ok`, `review`, non-claude `agent`, `merge_strategy`, `retry`, `priority`, `env`, `context_files`) exist so files do not need schema migration later. Some are honored today; others are metadata until their runtime behavior lands.

Readiness is derived in v0: a task is ready to run when `status: pending` and all dependencies are `done`. Do not persist `status: ready` in v0 task files. The model may continue accepting `ready` as a compatibility/status enum value, but the v0 DAG and runner should not write it.

## AgentAdapter interface

```python
class TaskResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    diff: str | None = None  # populated by runner via git, not adapter
    tokens_used: int | None = None
    cost_usd: float | None = None

class AgentAdapter(Protocol):
    name: str
    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult: ...
```

Rich enough for the v1.2 reviewer (needs diff + stdout) without future changes. `ClaudeCodeAdapter` shells to `claude -p`. `CodexAdapter` is a stub raising `NotImplementedError`.

## CLI commands (v0)

| Command | Purpose |
|---|---|
| `praetor init` | Create `.praetor/`, seed `context.md` from CLAUDE.md if present |
| `praetor add` | Interactive task creation (or just edit files directly) |
| `praetor status` | Text DAG view: id, status, deps, last verify result |
| `praetor run [--max-parallel N]` | Drain the queue. v0 forces N=1; v1 honors `parallel_ok` |
| `praetor logs <id>` | Tail per-task log |

## Skills bundle (v0)

Ship as plain markdown in `skills/`. Claude Code picks them up when the plugin is installed.

- **`praetor:task-authoring`** — when user asks to queue work, teach: where tasks live, frontmatter schema, what/how-to-verify/proof discipline, good `verify` command patterns.
- **`praetor:plan-decomposition`** — when user asks to break a goal into tasks, teach: dependency analysis, parallel grouping, writing the DAG into task files correctly.
- **`praetor:task-review`** (v1.2) — adversarial reviewer instructions as a skill, not a hardcoded prompt, so it's transparent and user-tunable.

## Packaging

- **PyPI package**: `praetor-cli` (CLI + library)
- **Install**: `pipx install praetor-cli` (isolates the installed CLI from the user's system Python)
- **Binary name**: `praetor` (via `pyproject.toml` `[project.scripts]`)
- **Claude Code plugin**: bundles CLI, skills, MCP server config — single install (v1.1)

## Roadmap

**v0 — Foundation (shipped)**
- Repo, Python package scaffold using conda for local development (CI deferred to v0.5)
- Task schema + state layer + DAG resolver
- `AgentAdapter` interface; ClaudeCode adapter live; Codex stubbed
- Commands: `init`, `add`, `status`, `run` (sequential)
- Verify step + per-task logs
- Skills written: `task-authoring`, `plan-decomposition`

**v1 — Parallel execution (shipped)**
- Worktree manager
- Worker pool, `--max-parallel N`
- DAG executor dispatches eligible siblings concurrently
- Branch-per-task, manual/auto merge, post-merge verification
- Conflict detection remains deferred as [issue #4](https://github.com/sid-valecha/praetor/issues/4)

**v1.1 — MCP server + plugin release (shipped)**
- `praetor mcp` entrypoint
- Tools: `init_workspace`, `add_task`, `list_tasks`, `get_task`, `next_ready`, `start_drain`, `merge_task`, `merge_all_pending`, `get_logs`
- Claude Code plugin bundle under `plugin/`
- `praetor loop` watch mode
- `praetor reset` recovery command
- Progress events for `praetor run` / `praetor loop`

**v1.2 — Trustworthy closed loops (shipped)**
- Run history in `.praetor/runs/<run-id>.json`
- Post-verify adversarial reviewer for `review: lenient|strict`
- `review_failed` task state for reviewer rejections
- `task-review` skill shipped
- `--max-iterations` and `--max-runtime` guardrails for `praetor run` / `praetor loop`

**v1.2.1 — Review recovery UX**
- Make `review_failed` obvious in `praetor status`, logs, run history, and MCP responses
- Feed the latest reviewer findings into the next retry prompt so the next executor sees the criticism it must fix
- Add a review retry budget so loops do not burn tokens indefinitely
- Default automatic review retries: 1
- Config precedence: CLI flag first, then `.praetor/config.toml`, then built-in default
- Planned flags/config: `--max-review-retries N` and `.praetor/config.toml` `max_review_retries = 1`
- Stop cleanly once the retry budget is exhausted and leave the task in `review_failed` with findings intact

**v1.3 — Cross-model trust gate**
- Real Codex adapter, probably Aider after that
- Per-task agent selection via existing `agent:` field
- Add reviewer selection at run time: `--reviewer-adapter`, `--reviewer-model`, `--reviewer-effort`
- Default reviewer remains same adapter/model/effort as executor
- Strong path: Claude implements and Codex reviews, or Codex implements and Claude reviews
- Preserve maker/checker separation in run history so users can audit which agent wrote and which agent reviewed

**v1.4 — Memory compounding**
- Add `.praetor/learnings.md`
- Summarize completed, failed, and rejected runs into human-readable lessons
- Keep default memory local, inspectable, and file-based
- Treat `.praetor/runs/*.json` as the structured source of truth and `learnings.md` as the narrative layer
- Use reviewer failures as especially valuable memory: "what got missed, what fixed it, what to avoid next time"

**v1.x — Optional memory experiments**
- Define a `MemoryBackend` seam only once real query patterns appear
- SQLite/FTS is the first serious backend candidate because it stays local and inspectable
- Hyperspell/vector memory is an optional branch or demo integration, not a core default
- Hosted memory backends must be opt-in because they break the default no-cloud invariant

**v1.x — Power-user execution**
- Improve multi-agent operation for users with higher Codex/Claude budgets
- Run independent tasks across separate worktrees and branches with less manual babysitting
- Support Codex-native subagent workflows as a first-class PM pattern for exploration, review fan-out, and independent implementation threads
- Support Claude PM sessions delegating to Codex through `openai/codex-plugin-cc` as an interop path, while keeping Praetor's own queue/model independent of either vendor's plugin surface
- Explore containerized agent execution so permissive modes can run inside a controlled boundary
- Add ergonomics for PM sessions dispatching worker agents and reviewing results
- Dogfood Praetor on Praetor: use Praetor tasks, worktrees, review gates, and retry policies to build Praetor itself

**v2 — Planner mode + maintainer intake**
- `praetor plan "goal"` short-lived planning session writes plan.md + drafts tasks
- GitHub Issues / PR comment intake
- Linear and Slack intake once the task-source abstraction is clear
- Patrol loops for background maintenance: failing CI, stale blocked tasks, new issues, aging review failures

**v2.x — Quality of life**
- Cost tracking, hooks (pre/post task, e.g. ntfy), richer config, cancellation, cleanup, and workflow polish

**v3 — Meta loop + GUI**
- Reflection pass updates `context.md`
- Web GUI reading `.praetor/` state (trivial because state is on-disk markdown/json)
- Optional semantic recall over run history once markdown/json recall is demonstrably insufficient

## Historical Monday landing zone

Realistic: all of v0, possibly 30–50% of v1 (worktree scaffold, parallel runner rough). Anything past v1 is post-Monday, on the same base.

## Design discipline that protects the base

- Frontmatter fields for v1+ exist in v0 (ignored, not migrated)
- `AgentAdapter.exec()` returns rich `TaskResult` (not boolean) so reviewer slot-fits
- CLI commands and MCP tools share the same underlying service functions
- State is plain markdown + json on disk — GUI, sync, multi-tool inspection all become trivial later

## Verification (how we know v0 works)

End-to-end smoke test:
1. `praetor init` in a scratch repo
2. Add three linear tasks (each modifies one file, verify = `pytest` or equivalent)
3. `praetor run` drains all three sequentially; statuses transition correctly
4. Inject a failing verify on task 2 → confirm `failed` state, task 3 blocked, logs captured
5. Re-run resumes from `pending` set without re-doing `done`

Plus unit tests on: schema parse/write, DAG resolver (ready-set computation, cycle detection), adapter mock.

## Open questions (resolve before/during v0)

- ~~PyPI name `praetor-cli` availability~~ — confirmed available, 2026-05-26
- `.praetor/` is gitignored by default. `context.md` and `decisions.md` can be copied into repo docs manually later if a project wants committed process notes.
- Whether `praetor run` is foreground-blocking or detaches (lean: foreground v0, `--detach` later)
