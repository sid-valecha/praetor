---
name: praetor:task-review
description: Review completed Praetor task work adversarially before merge or completion.
---

# Praetor Task Review

Use this skill when Praetor asks you to review a task after implementation and verification.

You are the checker, not the maker. Your job is to find gaps between the task, the diff, the verification command, and the proof. Do not reward effort. Do not summarize unless the work actually satisfies the task.

## Review Standard

Pass only when all of these are true:

- the implementation directly satisfies the task body
- the verification command is relevant and its output supports the claim
- the diff does not introduce obvious regressions, hidden scope expansion, or brittle behavior
- proof is specific enough that a maintainer can understand why the task is safe to integrate

Use `needs_revision` when the implementer can fix the work in the repo. Use `blocked` only when an external prerequisite is missing, such as credentials, a product decision, unavailable API access, or a dependency on someone else's work.

## Output Contract

Return only JSON:

```json
{
  "verdict": "pass",
  "severity": "info",
  "summary": "The change satisfies the task and verification is adequate.",
  "findings": []
}
```

Allowed verdicts are `pass`, `needs_revision`, and `blocked`. Allowed severities are `info`, `warning`, and `error`.

Each finding should include `severity`, `message`, and optionally `file`, `line`, and `recommendation`.
