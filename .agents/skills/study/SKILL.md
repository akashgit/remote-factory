---
name: study
description: "Analyze the current codebase using Factory's observation engine. Generates a report covering code quality, eval scores, open issues, backlog items, observability coverage, and improvement opportunities. Use when the user wants to understand the state of their project before making changes."
disable-model-invocation: true
---

# /factory:study

Analyze the current codebase and generate an observation report.

## Prerequisites

```bash
command -v factory >/dev/null 2>&1 || uv tool install "${CLAUDE_PLUGIN_ROOT}"
```

## Execution

```bash
factory study "$(pwd)"
```

This produces a report at `.factory/strategy/observations.md` covering:

- **Eval scores** — current composite and per-dimension breakdown
- **Open issues** — from GitHub, if available
- **Backlog items** — pending work from `.factory/strategy/backlog.md`
- **Observability coverage** — logging density and uninstrumented files
- **Hypothesis budget** — how many improvements to target this cycle
- **Cross-project insights** — patterns from sibling projects (if any)

For cross-project insights, pass `--projects-dir`:

```bash
factory study "$(pwd)" --projects-dir ~/factory-projects
```

After studying, use `/factory:implement` to act on the findings.
