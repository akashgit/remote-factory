# Remote Factory

Domain-agnostic multi-agent software evolution loop. Automatically discovers what to measure in any project, then coordinates specialized agents to continuously improve it.

## What is it?

The factory is a Python CLI + orchestration system that:

1. **Discovers** what to measure in a project (tests, lint, type checking, coverage, observability)
2. **Evaluates** the project with a composite score
3. **Hypothesizes** improvements based on low-scoring areas
4. **Implements** changes via builder agents (Claude subprocesses)
5. **Guards** against scope violations and regressions
6. **Archives** decisions and patterns to an Obsidian vault

## Architecture

Three layers work together:

**Python CLI** (`factory/`) -- the engine. Commands like `factory detect`, `factory eval`, `factory study`. Pure tools that don't make decisions.

**Skill** (`SKILL.md`) -- the brain. An orchestration protocol loaded into Claude's context via `/factory`. Defines the workflow: observe, hypothesize, build, eval, keep/revert.

**Agents** -- 6 specialist Claude subprocesses spawned by the orchestrator:

| Agent | Role |
|-------|------|
| **Researcher** | Analyze code, find gaps, search for best practices |
| **Strategist** | Generate ranked hypotheses from observations and scores |
| **Builder** | Implement a single GitHub issue, open one PR |
| **Reviewer** | Guard rules + code review on PR |
| **Evaluator** | Run evals, report scores, compare before/after |
| **Archivist** | Write notes to Obsidian vault for institutional memory |

## Modes

The factory operates as a state machine with 4 modes:

| Mode | When | What happens |
|------|------|-------------|
| **Build** | No repo or incomplete | Delegate scaffolds MVP from plan |
| **Discover** | Repo exists, no factory | Auto-detect eval dimensions, generate `eval/score.py` |
| **Review** | Evals discovered, not reviewed | Human gate: approve eval profile before automation |
| **Improve** | Factory initialized | Inner loop: observe -> hypothesize -> build -> eval -> keep/revert |

## Eval Discovery

Auto-discovers eval dimensions using a 3-tier resolution:

| Tier | Source | Confidence |
|------|--------|------------|
| Explicit | User wrote `eval/score.py` | 1.0 |
| Discovered | Factory finds pytest, ruff, mypy in project config | 0.8 |
| Researched | Factory infers from project type + best practices | 0.5 |
| Fallback | Basic checks: does it build? does it import? | 0.2 |

Auto-generated evals enter `EVALS_PENDING_REVIEW` state -- they cannot drive the improvement loop until a human approves them.

### Built-in Dimensions

| Dimension | Source | What it measures |
|-----------|--------|-----------------|
| tests | Discovered | Test suite passes |
| lint | Discovered | Linter passes (ruff, eslint, clippy) |
| type_check | Researched | Type checker passes (mypy, tsc) |
| coverage | Researched | Test coverage |
| observability | Researched | Logging coverage, structured logging, request tracing |

## Observability

The factory treats observability as a first-class concern. It is analyzed during **study** and scored during **eval**.

**Study phase** (`factory study`): deep observability coverage analysis -- identifies uninstrumented files, detects logging frameworks, and generates recommendations.

**Eval phase** (`eval/score.py`): includes an `eval_observability()` dimension that scores the project on logging practices. The composite score is:

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| Function coverage | 40% | Fraction of functions with log statements |
| Structured logging | 25% | Whether structlog/pino/winston/slog is used |
| Request tracing | 20% | Whether request ID / correlation ID patterns exist |
| Log density | 15% | Log statements per function |

**Strategy phase**: if observability score is below 0.5, the strategist MUST generate an observability improvement hypothesis as HIGH priority.

## Quick Start

```bash
# Install
cd ~/factory-projects/remote-factory
uv sync

# Or use the factory skill in Claude Code
/factory
```

### CLI Commands

| Command | Description |
|---------|-------------|
| `factory detect <path>` | Print project state |
| `factory discover <path>` | Introspect project, generate eval profile |
| `factory init <path>` | Create `.factory/` from `factory.md` |
| `factory eval <path>` | Run evals, print JSON composite score |
| `factory study <path>` | Analyze code + interaction logs, write observations |
| `factory guard <path> --baseline <sha>` | Check guard rules |
| `factory begin <path> --hypothesis "..."` | Start experiment |
| `factory finalize <path> --id N --verdict keep/revert` | Finalize experiment |
| `factory history <path>` | Print experiment history |
| `factory status <path>` | Print project status summary |
| `factory archive <path>` | Write experiment notes to Obsidian vault |
| `factory run <path>` | Run a full factory cycle (for cron/automation) |

## Project Structure

```
factory/
├── models.py              # Pydantic v2 models (config, eval, experiments)
├── state.py               # State machine (5 states)
├── store.py               # .factory/ filesystem store
├── cli.py                 # CLI entry point (argparse subcommands)
├── study.py               # Interaction log analysis + observability coverage
├── discovery/
│   ├── introspect.py      # Project introspection (language, framework, tools)
│   ├── profile.py         # Build eval profile from project metadata
│   └── generate.py        # Generate eval/score.py from profile
├── eval/
│   ├── runner.py          # Run eval commands as subprocesses
│   ├── scorer.py          # Composite score computation
│   └── guards.py          # Guard rule enforcement
├── agents/
│   ├── runner.py          # Agent subprocess spawner
│   └── prompts/           # Agent role prompts (researcher, strategist, etc.)
├── obsidian/
│   ├── notes.py           # Obsidian vault integration
│   └── templates.py       # Note templates
└── notify/
    └── telegram.py        # Telegram notifications
```

## Configuration

Each managed project has a `factory.md` at its root:

```markdown
# Factory Config

## Goal
One sentence describing what the project should achieve.

## Scope
### Modifiable
- src/**
- tests/**
### Read-only
- README.md

## Guards
- Do not modify files in .factory/
- Do not remove existing tests

## Eval
- Command: python eval/score.py
- Threshold: 0.8
```

## Obsidian Integration

The factory uses a dedicated Obsidian vault (`~/factory-vault/`) for institutional memory:

```
~/factory-vault/
├── 00-Factory/          # Cross-project knowledge (Dashboard, Patterns)
├── 10-Projects/{name}/  # Per-project notes (Experiments, Strategies)
├── 20-Knowledge/        # Concepts and external sources
├── _templates/          # Note templates
└── MEMORY.md            # Thin pointer index for agent orientation
```

## Environment

All Claude CLI and Anthropic API access uses Google Vertex AI:

```bash
export CLAUDE_CODE_USE_VERTEX=1
export CLOUD_ML_REGION=your-region
export ANTHROPIC_VERTEX_PROJECT_ID=your-gcp-project
```

## Development

```bash
uv sync          # Install dependencies
pytest -v        # Run tests
ruff check .     # Lint
```

## Predecessor

This is v2 of the software factory. v1 (`software-factory`) was SEO-coupled with manual eval setup. v2 is domain-agnostic with auto-discovery and a specialized agent topology.
