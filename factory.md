# Factory Configuration
<!-- This file configures the Remote Factory for your project. -->
<!-- The factory reads this during Init mode and generates .factory/config.json from it. -->
<!-- Fill in each section below. -->

## Goal
<!-- A single sentence describing what this project should achieve. -->

Provide a CLI and agent framework ("Remote Factory") that autonomously evolves software projects through systematic experimentation — detecting project state, discovering eval harnesses, running improvement cycles, and archiving learnings.

## Scope

### Modifiable
<!-- Files and directories the factory is allowed to create or edit. -->
<!-- One path per line. Glob patterns are supported. -->

- factory/**/*.py
- factory/dashboard/static/*
- tests/**/*.py
- templates/**
- docs/**
- eval/score.py

### Read-only
<!-- Files the factory may read but must never modify. -->

- README.md
- pyproject.toml
- CLAUDE.md
- factory.md

## Guards
<!-- Rules the factory must never violate. Checked before every commit. -->

- Do not delete or overwrite existing tests
- Do not modify files outside the declared scope
- Do not introduce secrets or credentials into the repository

## Eval

### Command
<!-- The shell command the factory runs to score a change. -->
<!-- It must output JSON to stdout matching the EvalResult format. -->

```bash
python eval/score.py
```

### Threshold
<!-- Minimum composite score (0.0-1.0) required to keep a change. -->

0.8

## Target Branch
<!-- Branch that experiment PRs target. Default: main -->
<!-- Set to a different branch (e.g. factory/dev) to stage factory changes before merging to main -->

main

## Project Eval
<!-- No project-specific eval dimensions for this project. -->

## Eval Weights
<!-- Default: hygiene 0.50, growth 0.50 -->

## Smoke Test
<!-- Optional shell command that must pass before any change is kept. -->
<!-- If configured, this runs as part of `factory precheck` — failure = mandatory revert. -->
<!-- Use for e2e verification: hit an endpoint, run a CLI command, check a process starts. -->

```bash
uv run python -m factory detect . && uv run python -m factory --help
```

## Constraints
<!-- Soft rules that guide behavior but don't block commits. -->

- Prefer small, incremental changes over large rewrites
- Each change should be accompanied by at least one test
- Follow the existing code style and conventions

## Research Target
<!-- Not a research project. -->

## Mutable Surfaces
<!-- Not used — this is not a research project. -->

## Fixed Surfaces
<!-- Not used — this is not a research project. -->

## Research Constraints
<!-- Not used. -->

## Cost Budget
<!-- Not configured. -->
