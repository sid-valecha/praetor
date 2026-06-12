---
name: task-authoring
description: Use when a user asks to queue work, add a task, create a task, or schedule something for an agent to implement
---

# Task Authoring

## Overview

Praetor tasks are prompt files for implementer agents. A good task is small, dependency-aware, and has a shell verification command that exits 0 only when the work is done.

**Announce at start:** "I'm using the task-authoring skill to write Praetor task files."

## When to Use

Use this skill when the user asks to:
- queue work
- add or create a task
- schedule implementation work for an agent
- turn a scoped request into `.praetor/tasks/<id>.md`

Do not use this skill for broad feature decomposition; use `plan-decomposition` first, then author the resulting task files.

## Task Location

Tasks live in:

```text
.praetor/tasks/<id>.md
```

The `<id>` in the filename must match the `id` frontmatter field. Use stable, readable ids such as `001-add-auth-models` or `billing-webhook-tests`.

## Frontmatter Schema

Every task file must include the current Praetor task schema so queued work can run in sequential or parallel mode without later migration.

| Field | Type | Default | Version | Notes |
| --- | --- | --- | --- | --- |
| `id` | string | none, required | v0 | Must match `.praetor/tasks/<id>.md`. |
| `status` | enum string | `pending` | v0 | Use `pending`, `running`, `pending_merge`, `merge_failed`, `review_failed`, `cancelled`, `done`, `failed`, or `blocked`. The DAG resolver and `praetor status` derive readiness from `pending` tasks whose dependencies are all `done`; readiness is not a persisted status. |
| `depends_on` | list of task ids | `[]` | v0 | List every prerequisite task id. Empty list means no prerequisites. |
| `parallel_ok` | boolean | `true` | v1 honored | Set `false` for unsafe or cross-cutting sibling work. |
| `agent` | string | `claude` | v0 | Expected values are currently adapter names such as `claude`, `codex`, or `aider`; `praetor run --adapter` selects the runtime adapter. |
| `verify` | string or null | `null` | v0 | Prefer a real command. It is run by the shell after the agent finishes. |
| `review` | enum string | `off` | v1.2+ honored | Use `off`, `lenient`, or `strict`. |
| `merge_strategy` | enum string | `manual` | v1 honored | Use `manual` or `auto`. `manual` stops after verify and parks the task as `pending_merge` for `praetor merge`; `auto` merges automatically after verify passes. |
| `retry` | integer | `0` | future | Retry count metadata. Runner retry policy is deferred. |
| `priority` | enum string | `normal` | future | Scheduling hint: `low`, `normal`, or `high`. Ready-set priority ordering is deferred. |
| `env` | map | `{}` | future | Per-task environment metadata. Runtime env propagation is deferred. |
| `context_files` | list of paths | `[]` | future | File/context hints for agents. Adapter use is deferred. |
| `created` | UTC datetime string | none, required | v0 | Use ISO 8601 with `Z`, for example `2026-06-07T18:30:00Z`. |
| `body` | markdown body | empty string | v0 | The content after frontmatter. It is parsed into the task model and passed to the agent; do not put it in frontmatter. |

## Required Sections

Every task body must contain exactly these operational sections:

```markdown
## What to do
[the prompt for the implementer agent]

## How to verify
[success criteria and the exact command from frontmatter]

## Proof when complete
[what output, files, or behavior proves completion]
```

The `## What to do` section is a prompt, not a product spec. It should tell the implementer which files to inspect or change, constraints to respect, and the intended outcome.

Praetor runs implementers non-interactively. Write tasks as if the implementer already has permission to make the scoped edits in the task. Do not ask the agent to confirm before normal in-scope changes; tell it to ask only when the work would be destructive or outside the stated scope.

## Verify Commands

The `verify` field must be a shell command that exits 0 on success and non-zero on failure.

Good patterns:
- `pytest tests/unit/test_dag.py`
- `pytest tests/e2e/test_v0.py && ruff check`
- `cargo test -p billing`
- `npm test -- --run auth`
- `go test ./internal/webhook`
- docs/content checks such as `grep -q "Phase 27 dogfood" Handoff.md && grep -q "3dbad8a" Handoff.md`

Bad patterns:
- `manual check in browser`
- `make sure it works`
- `pytest maybe`
- `ask the user to verify`
- commands so broad they hide the intended proof, such as full monorepo tests for a one-file copy change
- unrelated tests for docs-only edits; make the command check the required text or rendered output

If no automated verification exists yet, the task should usually include creating a focused test and then set `verify` to that test command.

## Merge Strategy

Use the default `merge_strategy: manual` for most tasks so the user reviews verified work before integration. Set `merge_strategy: auto` only for tasks whose verify command is strong enough that you would merge a passing PR without further review.

## Using `praetor add`

Use `praetor add` when creating a simple task interactively because it writes a valid task file with an id, timestamp, dependencies, and verify command:

```bash
praetor add --title "Add webhook signature tests" --depends-on "001-add-webhook-parser" --verify "pytest tests/billing/test_webhook_signature.py"
```

After `praetor add`, open the created file and fill in `## What to do`, `## How to verify`, and `## Proof when complete`. Also check `parallel_ok`, `merge_strategy`, `review`, and `agent`, then adjust them if needed.

Edit files directly when:
- writing several tasks from a decomposition
- preserving manually chosen numeric ids
- setting fields or hand-picked ids more precisely than the CLI flags allow
- copying a reviewed task template

When editing directly, keep the filename, `id`, dependency ids, and verify command consistent.

## Worked Example

File: `.praetor/tasks/002-add-webhook-signature-tests.md`

```markdown
---
id: 002-add-webhook-signature-tests
status: pending
depends_on: [001-add-webhook-parser]
parallel_ok: true
agent: claude
verify: pytest tests/billing/test_webhook_signature.py
merge_strategy: manual
created: 2026-06-07T18:30:00Z
review: off
---

# Add webhook signature tests

## What to do

Read `praetor/billing/webhook.py` and the parser tests added by `001-add-webhook-parser`.

Create focused pytest coverage for valid signatures, invalid signatures, and missing signature headers. Keep the tests local to `tests/billing/test_webhook_signature.py`. Do not change production behavior unless the tests expose a real bug; if a bug is found, fix only the minimal code needed for these cases.

## How to verify

Run:

```bash
pytest tests/billing/test_webhook_signature.py
```

The command must exit 0 and cover valid, invalid, and missing signature cases.

## Proof when complete

Report the passing pytest output and summarize any production files changed. If no production code changed, say that explicitly.
```

## Common Mistakes

- Fuzzy verify commands: replace "check manually" with a command that exits 0 on success.
- Missing `depends_on`: if the task needs code, fixtures, schema, docs, or generated files from another task, list that task id.
- Body is a spec, not a prompt: convert goals into actionable instructions with files, constraints, and expected proof.
- Persisting `status: ready`: keep `status: pending`; Praetor derives readiness from done dependencies.
- Omitting schema fields: include `parallel_ok`, `agent`, `merge_strategy`, `review`, `retry`, `priority`, `env`, and `context_files` even when their default values are acceptable.
- Oversized tasks: split work if the verify command cannot prove completion for one agent session.
