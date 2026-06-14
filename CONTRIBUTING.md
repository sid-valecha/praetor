# Contributing to Praetor

Thanks for contributing to Praetor.

## Reporting issues

1. Search existing issues for duplicates.
2. Open a focused issue with reproducible steps, expected behavior, and versions.
3. Include the task/adapters used (`claude` or `codex`) and relevant run logs from `.praetor/runs/*.json` when sharing execution concerns.

## Development setup

Use the `praetor` conda environment for all Python/PyPI commands:

```bash
conda activate praetor
pip install -e ".[dev]"
```

## Before opening a pull request

1. Create a focused change scope.
2. Update docs when behavior changes.
3. Run the project checks:

```bash
conda activate praetor
pytest -q
ruff check
ruff format --check
```

## PR expectations

- Keep edits scoped to the task.
- Prefer one behavior change per PR.
- Include any docs updates required by CLI/behavior changes.
- Ensure your PR description explains risk, validation, and manual recovery expectations.

## Trust-boundary notes

- Treat `.praetor/` outputs as local project state.
- Avoid storing secrets in task prompts or verify commands.
- For first-time usage, prefer low-risk sequential runs (`--max-parallel 1`).
