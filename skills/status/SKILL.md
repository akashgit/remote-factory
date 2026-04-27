---
name: status
description: "Show the current Factory status for this project, including project state, experiment history, eval scores, and active backlog. Use when the user asks about the factory status, project state, or experiment history."
disable-model-invocation: true
---

# /factory:status

Show the current Factory status for this project.

## Prerequisites

```bash
command -v factory >/dev/null 2>&1 || uv tool install "${CLAUDE_PLUGIN_ROOT}"
```

## Execution

```bash
factory status "$(pwd)"
```

This shows:

- **Project state** — one of: no_repo, incomplete, no_factory, evals_pending_review, has_factory
- **Eval scores** — latest composite and per-dimension scores
- **Experiment history** — recent experiments with hypotheses, verdicts, and score deltas
- **Backlog** — pending items from `.factory/strategy/backlog.md`

If the project hasn't been initialized with the factory yet, run:

```bash
factory ceo "$(pwd)"
```

This will auto-detect the project state and guide it through setup (discovery, eval review, initialization).
