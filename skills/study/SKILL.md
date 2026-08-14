---
name: study
description: "Analyze the current codebase using Factory's observation engine and code graph. Generates a report covering code quality, eval scores, structural analysis, open issues, backlog items, observability coverage, and improvement opportunities. Use when the user wants to understand the state of their project before making changes."
disable-model-invocation: true
---

# /factory:study

Analyze the current codebase and generate an observation report with structural graph analysis.

## Prerequisites

```bash
command -v factory >/dev/null 2>&1 || uv tool install "${CLAUDE_PLUGIN_ROOT}"
```

## Execution

```bash
factory graph update "$(pwd)"
factory study "$(pwd)"
```

If graphify is installed and `graph.json` exists, explore the code graph:

```bash
factory graph query "<focus from observations>" --depth 2
factory graph explain "<key node>"
factory graph path "<A>" "<B>"
```

Write graph findings to `.factory/strategy/graph-context.md`, then combine:

```bash
cat .factory/strategy/observations.md .factory/strategy/graph-context.md \
  > .factory/strategy/study-combined.md
```

The combined report at `.factory/strategy/study-combined.md` covers:

- **Eval scores** — current composite and per-dimension breakdown
- **Open issues** — from GitHub, if available
- **Backlog items** — pending work from `.factory/strategy/backlog.md`
- **Observability coverage** — logging density and uninstrumented files
- **Hypothesis budget** — how many improvements to target this cycle
- **Cross-project insights** — patterns from sibling projects (if any)
- **Structural analysis** — key modules, dependency paths, architectural layers, entry points

For cross-project insights, pass `--projects-dir`:

```bash
factory study "$(pwd)" --projects-dir ~/factory-projects
```

After studying, use `/factory:implement` to act on the findings.
