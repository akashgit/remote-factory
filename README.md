# Remote Factory

Domain-agnostic multi-agent software evolution loop. Automatically discovers what to measure in any project, then coordinates specialized agents to continuously improve it.

## How It Works

The factory operates as a state machine with 4 modes:

1. **Build** -- no project exists yet. Invokes the delegate skill to scaffold it.
2. **Discover** -- project exists but no factory setup. Auto-detects eval dimensions (tests, lint, type checking, coverage) from the project's tooling.
3. **Review** -- evals have been discovered but need human approval before the factory can act on them.
4. **Improve** -- factory is initialized. Runs the inner loop: observe state, hypothesize changes, implement via builder agents, guard-check, evaluate, keep or revert.

## Agent Topology

Six specialized agents coordinate via GitHub issues, PRs, and branches:

| Agent | Role |
|-------|------|
| **Researcher** | Introspects a project, discovers eval dimensions, writes agent overrides |
| **Strategist** | Reads history and scores, generates ranked hypotheses |
| **Builder** | Implements a single GitHub issue, opens one PR |
| **Reviewer** | Reviews PRs, runs guards, decides keep/revert |
| **Evaluator** | Runs evals, interprets results, writes narrative |
| **Archivist** | Writes Obsidian notes for institutional memory |

## Quick Start

```bash
# Install
cd ~/factory-projects/remote-factory
uv sync

# Discover what the factory can measure in a project
python -m factory discover ~/path/to/project

# Check state
python -m factory detect ~/path/to/project

# After reviewing evals and creating factory.md:
python -m factory init ~/path/to/project

# Run baseline eval
python -m factory eval ~/path/to/project

# Start an improvement experiment
python -m factory begin ~/path/to/project --hypothesis "Add type hints to core module"

# Check guards before keeping a change
python -m factory guard ~/path/to/project --baseline <sha>

# View experiment history
python -m factory history ~/path/to/project
```

## Eval Discovery

The factory auto-discovers eval dimensions using a 3-tier resolution:

| Tier | Source | Confidence |
|------|--------|------------|
| Explicit | User wrote `eval/score.py` | 1.0 |
| Discovered | Factory finds pytest, ruff, mypy in project config | 0.8 |
| Researched | Factory infers from project type + best practices | 0.5 |
| Fallback | Basic checks: does it build? does it import? | 0.2 |

Auto-generated evals enter `EVALS_PENDING_REVIEW` state -- they cannot drive the improvement loop until a human approves them.

## Project Structure

```
factory/
  models.py          # Pydantic v2 models (config, eval, experiments)
  state.py           # State machine (5 states)
  store.py           # .factory/ filesystem store
  cli.py             # CLI: detect, discover, init, eval, guard, begin, finalize, history, notify, run
  discovery/         # Auto-discovery: introspect, profile, generate
  eval/              # Runner, scorer, guards
  agents/            # Agent runner + prompts (researcher, strategist, builder, reviewer, evaluator, archivist)
  obsidian/          # Obsidian vault integration (experiment notes, dashboards)
  notify/            # Telegram notifications
```

## Predecessor

This is v2 of the software factory. v1 (`software-factory`) was SEO-coupled with manual eval setup. v2 is domain-agnostic with auto-discovery and a specialized agent topology.
