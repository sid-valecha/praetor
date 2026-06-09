---
name: plan-decomposition
description: Use when a user asks to break a goal into tasks, decompose a feature, or plan a sequence of agent work
---

# Plan Decomposition

## Overview

Praetor plans are dependency graphs of small task prompts. Decompose goals into tasks that one implementer agent can complete in one session, with a binary verification command per task.

**Announce at start:** "I'm using the plan-decomposition skill to break the goal into Praetor tasks."

## When to Use

Use this skill when the user asks to:
- break a goal into tasks
- decompose a feature
- plan a sequence of agent work
- create a queue where multiple agents may eventually run independent work

After decomposition, use `task-authoring` to write each task file.

## Dependency Analysis

Before writing files, identify what must happen before what.

Ask these questions:
- Does task B need code, tests, generated files, or decisions from task A?
- Does task B depend on an API shape, schema, CLI flag, migration, fixture, or contract introduced by task A?
- Would running task B first cause rework or guesswork?
- Can task B be verified without task A being complete?

If the answer is yes, B must list A in `depends_on`.

Typical dependency shapes:
- Linear: schema before parser before CLI.
- Fan-out: shared interface first, then independent implementations.
- Fan-in: independent pieces first, then integration or docs.
- Diamond: foundation, parallel siblings, final end-to-end task.

## Parallel Grouping

Set `parallel_ok: true` only for tasks that are safe to run at the same time as other ready sibling tasks.

Good parallel candidates:
- tests for separate modules after a shared interface exists
- independent adapters behind a locked protocol
- docs and focused tests that touch different files
- UI panels that do not share layout or state files

Keep `parallel_ok: false` when:
- tasks edit the same files or nearby code paths
- one task needs decisions discovered by another
- the task performs migrations, broad refactors, or cross-cutting formatting
- the verify command mutates shared state or requires exclusive resources

Praetor runs sequentially by default. When the user runs with `--max-parallel > 1`, ready tasks with `parallel_ok: true` may run concurrently in worktrees; `parallel_ok: false` tasks run alone.

## Merge Strategy

For most decomposition outputs, leave the default `merge_strategy: manual` so the user reviews each task before integration.

Suggest `merge_strategy: auto` only when all of these are true:
- the task has a verify command
- the task scope is genuinely isolated, such as a single file or single module
- the task is unlikely to overlap with sibling tasks
- the user has explicitly indicated comfort with auto-merging

## Writing the DAG

Represent the graph directly in each task's frontmatter:

```yaml
depends_on: []
```

or:

```yaml
depends_on: [001-foundation, 003-unit-tests]
```

Rules:
- Dependencies point backward to prerequisite task ids.
- Do not list downstream tasks.
- Do not create cycles.
- Do not use `status: ready`; use `status: pending` and let Praetor derive readiness.
- The filename must match `id`: `.praetor/tasks/<id>.md`.

## Task Sizing

Each task should fit one focused agent session.

A good task has:
- one clear behavioral outcome
- a small set of files to inspect or modify
- enough context to avoid guessing
- a binary `verify` command
- proof expectations that a reviewer can check

Split a task when:
- it says "also"
- the verify command is a whole-repo command but the work is only partly related
- the agent must design an interface and implement several consumers
- success requires both implementation and broad documentation or migration work

Merge tasks when:
- neither task can be verified alone
- they would edit the same tiny file in contradictory ways
- one task is only mechanical setup for the next and has no independent proof

## Worked Example

Goal: add a retry policy for failed Praetor tasks.

Decomposition:
- `001-add-retry-schema` defines the task/state fields and tests parsing.
- `002-render-retry-status` updates status output after the schema exists.
- `003-runner-retries-failed-tasks` implements retry behavior after the schema exists.
- `004-retry-e2e` verifies the CLI flow after status and runner behavior exist.

Task files:

```markdown
---
id: 001-add-retry-schema
status: pending
depends_on: []
parallel_ok: false
agent: claude
verify: pytest tests/unit/test_models.py tests/unit/test_frontmatter.py
merge_strategy: manual
created: 2026-06-07T19:00:00Z
review: off
---

# Add retry schema

## What to do

Add retry metadata to Praetor task parsing and serialization. Inspect `praetor/models.py` and `praetor/frontmatter.py`. Preserve round-trip behavior for existing task files.

## How to verify

Run `pytest tests/unit/test_models.py tests/unit/test_frontmatter.py`. The command must exit 0 and include coverage for default retry values and explicit frontmatter values.

## Proof when complete

Report the passing pytest output and the exact frontmatter fields added.
```

```markdown
---
id: 002-render-retry-status
status: pending
depends_on: [001-add-retry-schema]
parallel_ok: true
agent: claude
verify: pytest tests/unit/test_status_command.py
merge_strategy: manual
created: 2026-06-07T19:05:00Z
review: off
---

# Render retry status

## What to do

Update the status command to show retry count for tasks that have retry metadata. Keep output unchanged for tasks without retries.

## How to verify

Run `pytest tests/unit/test_status_command.py`. The command must exit 0 and cover tasks with and without retry metadata.

## Proof when complete

Report the passing pytest output and one sample status line.
```

```markdown
---
id: 003-runner-retries-failed-tasks
status: pending
depends_on: [001-add-retry-schema]
parallel_ok: false
agent: claude
verify: pytest tests/unit/test_runner.py
merge_strategy: manual
created: 2026-06-07T19:10:00Z
review: strict
---

# Retry failed tasks in the runner

## What to do

Update the sequential runner so a failed task is retried while retries remain, then marked `failed` only after the final failed attempt. Keep downstream blocked propagation unchanged after final failure.

## How to verify

Run `pytest tests/unit/test_runner.py`. The command must exit 0 and include a case where the second attempt succeeds and a case where retries are exhausted.

## Proof when complete

Report the passing pytest output and summarize the status transitions for both retry cases.
```

```markdown
---
id: 004-retry-e2e
status: pending
depends_on: [002-render-retry-status, 003-runner-retries-failed-tasks]
parallel_ok: false
agent: claude
verify: pytest tests/e2e/test_retry_policy.py
merge_strategy: manual
created: 2026-06-07T19:15:00Z
review: strict
---

# Add retry policy e2e coverage

## What to do

Add an end-to-end test that initializes a scratch Praetor workspace, creates a task with retry metadata, runs the queue, and verifies retry behavior plus status output.

## How to verify

Run `pytest tests/e2e/test_retry_policy.py`. The command must exit 0 and fail if retry execution or retry status rendering is broken.

## Proof when complete

Report the passing pytest output and the final task statuses from the e2e fixture.
```

Why this DAG works:
- `001` is first because schema controls all later task files and code paths.
- `002` and `003` both depend on `001`; they can be developed independently after the schema exists, but only `002` is marked parallel-safe because `003` changes shared runner behavior.
- `004` depends on both implementation branches because it verifies the integrated workflow.

## Pitfalls

- Circular dependencies: if A depends on B and B depends on A, neither can run. Break the cycle by extracting a smaller foundation task.
- Tasks too large: if a task spans schema, runner, CLI, docs, and e2e, split it into foundation, implementation, and integration tasks.
- Verify commands too broad: `pytest` for a focused CLI display task gives weak proof and slow feedback. Prefer the narrow test file that proves the task.
- Hidden dependencies: if a task needs an interface, fixture, migration, or decision from another task, put that task id in `depends_on`.
- Unsafe parallelism: do not set `parallel_ok: true` for siblings that edit the same files or make competing design decisions.
