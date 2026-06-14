# Security and trust boundaries

Praetor is designed as a local-first orchestrator, and most security trust is outside the core scheduler.

## Local boundaries

- `.praetor/` is local state and should be treated as sensitive.  
  It can include task prompts, command strings, branch refs, verifier output, and reviewer findings.
- The directory is gitignored by default, but local file shares and backups may still leak information.
- Remove `.praetor/` between environments if it contains project-restricted context.

## Adapter boundaries

- Adapter commands execute external CLI processes (for example `claude` and `codex`) with access to the current filesystem context.
- Keep credentials, PATs, and one-time codes out of task prompts and logs.
- Prefer low-risk sequential runs for first-time adapter configuration.

## Containers and privileged modes

- Prefer containerized execution for high-risk adapter experiments.
- Avoid `--dangerously-skip-permissions`-style workflows on the host when alternatives exist.

## Reporting a security concern

If you discover a potential security issue, please open a GitHub issue with a clear security impact summary and reproduction steps.  
If possible, include redacted command output from `.praetor/runs/*.json` and exact adapter/version details.
