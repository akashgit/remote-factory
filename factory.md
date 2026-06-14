# Factory Configuration
<!-- This file configures the Remote Factory for your project. -->
<!-- The factory reads this during Init mode and generates .factory/config.json from it. -->
<!-- Fill in each section below. -->

## Goal

Domain-agnostic multi-agent software evolution loop that can auto-discover evals and continuously improve any software project.

## Scope

### Modifiable
<!-- Files and directories the factory is allowed to create or edit. -->
<!-- One path per line. Glob patterns are supported. -->

- factory/**/*.py
- factory/agents/prompts/*.md
- factory/agents/agents.yml
- factory/dashboard/static/*
- tests/**/*.py
- templates/**
- docs/**
- .github/workflows/*.yml
- factory.md
- CLAUDE.md
- README.md
- pyproject.toml
- .agents/**
- .codex-plugin/**
- AGENTS.md
- scripts/**
- eval/**
- factory.md

### Read-only
<!-- Files the factory may read but must never modify. -->

## Guards
<!-- Rules the factory must never violate. Checked before every commit. -->

- Do not delete or overwrite existing tests
- Do not modify files outside the declared scope
- Do not introduce secrets or credentials into the repository
- Do not modify test fixtures that other tests depend on

## Eval

### Command
<!-- The shell command the factory runs to score a change. -->
<!-- It must output JSON to stdout matching the EvalResult format. -->

```bash
python eval/score.py
```

### Threshold
<!-- Minimum composite score (0.0-1.0) required to keep a change. -->

0.6

## Target Branch

main

## Project Eval
<!-- No project-specific eval dimensions for the factory itself -->
<!-- The factory uses the standard hygiene + growth eval framework -->

## Eval Weights
<!-- Using defaults: 50/50 hygiene/growth (no project eval) -->

## Hypothesis Budget
<!-- Controls how many hypotheses the Strategist generates per cycle. -->
<!-- These are defaults — override per-run with --min-growth, --max-new -->

- min_growth: 2
- max_new: 2

## Smoke Test
<!-- Optional e2e smoke test command. Failure = mandatory revert. -->

```bash
uv run pytest tests/test_models.py tests/test_guards.py tests/test_runners.py -x -q --tb=short -k 'not (BobAuth or preflight_error_unchanged)'
```

## Constraints
<!-- Soft rules that guide behavior but don't block commits. -->

- Prefer small, incremental changes over large rewrites
- Each change should be accompanied by at least one test
- Follow the existing code style and conventions
