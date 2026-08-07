# SWE-benchify-hard Benchmark Workflow

Minimal 4-node bug-fix pipeline for the SWE-benchify-hard dataset: 284 synthetic
Go bug-fix instances where at least one of Claude Haiku, Sonnet, or Opus failed
to solve the problem.

## Dataset

**Harbor:** `red-hat-ai/SWE-benchify-hard` ([Hub link](https://hub.harborframework.com/datasets/red-hat-ai/SWE-benchify-hard))

- 284 instances across 6 Go repositories
- Synthetic bugs introduced via AST mutation and LLM-guided semantic mutation
- Validated with Docker F2P/P2P, N-run flake quarantine, and self-screening
- Published by the SWE-benchify project (Red Hat AI Innovation Team)

## Pipeline

```
study → builder → gate_verify → auto_merge
                      ↑    ↓
                      └────┘ RELOOP (max 3)
```

Same structure as the vanilla `swebench` workflow, adapted for Go projects.

## Usage

```bash
factory workflow run swebenchifyhard .
```
