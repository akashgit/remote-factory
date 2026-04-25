# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

```bash
uv sync                          # Install all deps (including dev group)
factory --help                   # Verify CLI entry point
```

## Test

```bash
pytest -v                        # Full suite
pytest tests/test_models.py -v   # Single file
pytest -k "test_detect" -v       # By name pattern
pytest --cov                     # With coverage
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` — async test functions run without `@pytest.mark.asyncio`. Shared fixtures (`tmp_project`, `sample_config`, `python_project`, `obsidian_vault`) live in `tests/conftest.py`.

## Lint & Type Check

```bash
ruff check .                     # Lint
ruff check --fix .               # Lint with autofix
mypy factory/                    # Type check
```

## Style

- Python 3.11+ — use `X | Y` unions, not `Union[X, Y]`
- Snake_case everywhere
- 100 char line length (enforced by ruff)
- All Pydantic models use `ConfigDict(strict=True, extra="forbid")`
- Async/await by default — library functions in `store.py` and `eval/runner.py` are async, the CLI wraps them with `asyncio.run()`
- Structured logging via `structlog` — use `log = structlog.get_logger()` at module level

## Architecture (v2 — CEO Agent)

The factory is a **three-layer system** with a dedicated CEO agent as the orchestrator:

### Layer 1: Python CLI (`factory/`)

Pure tools that don't make decisions. Entry point is `factory/cli.py` → `factory.cli:main` (registered as `factory` script in pyproject.toml). Each subcommand is a `cmd_*` function dispatched via a handler dict.

### Layer 2: CEO Agent (`factory/agents/prompts/ceo.md`)

A dedicated Claude Code agent that owns the full factory workflow. Spawned via `factory ceo /path` or `factory run /path`. The CEO detects project state, routes to modes (Build/Discover/Review/Improve/Meta), spawns specialist agents, makes keep/revert decisions, and ensures mandatory archival. SKILL.md is a thin launcher shim that spawns the CEO.

### Layer 3: Specialist Agents (`factory/agents/`)

Seven specialist Claude Code subprocesses spawned by the CEO via `factory agent <role>`. Agent prompts are resolved via `factory/agents/runner.py` with a two-tier lookup: project-specific override (`.factory/agents/<role>.md`) then factory default (`factory/agents/prompts/<role>.md`). Evolved playbooks from `factory/agents/playbooks/<role>.md` are auto-injected.

**Roles:** Researcher (observe), Strategist (hypothesize), Builder (implement), Reviewer (guard), Evaluator (measure), Archivist (record), CEO (orchestrate).

### Key data flow

1. **State detection** (`factory/state.py`): Checks git, `.factory/config.json`, and `eval_profile.json` to determine one of 5 `ProjectState` enum values
2. **Discovery** (`factory/discovery/`): `introspect.py` → `profile.py` → `generate.py` — detects project language/framework, builds an `EvalProfile` of dimensions, generates `eval/score.py`
3. **Eval** (`factory/eval/`): `runner.py` executes the eval command as a subprocess, expects JSON stdout `{"results": [...]}`. Growth dimensions (`growth.py`) are computed locally and merged at 50/50 with project hygiene dimensions. `scorer.py` computes the weighted composite
4. **Strategy** (`factory/strategy.py`): FEEC priority heuristic (Fix > Exploit > Explore > Combine) classifies hypotheses by keyword matching, with stuck detection after 3+ consecutive same-category reverts
5. **Store** (`factory/store.py`): `ExperimentStore` manages the `.factory/` directory — config, TSV history, per-experiment dirs with hypothesis/eval/diff/verdict artifacts
6. **Checkpoint** (`factory/checkpoint.py`): Saves and loads CEO state for crash-resilient resume
7. **Analysis** (`factory/analysis.py`): Experiment comparison (`diff`) and FEEC analysis (`explain`)

### Target project's `.factory/` layout

```
.factory/
├── config.json           # Parsed from factory.md (FactoryConfig model)
├── eval_profile.json     # Discovered eval dimensions (EvalProfile model)
├── results.tsv           # Append-only experiment history
├── experiments/
│   └── 001/              # Per-experiment: hypothesis.md, eval_before.json, eval_after.json, changes.diff, verdict.json
├── strategy/             # observations.md, current.md, insights.md, research.md
├── reviews/              # Agent output capture + CEO review verdicts
│   ├── <role>-latest.md  # Auto-saved stdout from each agent invocation
│   └── ceo-verdict-<role>.md  # CEO's review verdict (PROCEED/REDIRECT/ABORT)
└── agents/               # Per-project agent prompt overrides
```

### Models

All domain models live in `factory/models.py` as strict Pydantic v2 models. Key types: `ProjectState` (enum), `FactoryConfig`, `EvalProfile` / `EvalDimension`, `CompositeScore` / `EvalResult`, `ExperimentRecord`, `CrossProjectInsights`. The `Notifier` protocol defines the async notification interface.

## Environment

Requires Claude Code installed and authenticated. The factory spawns `claude` subprocesses — it does not call the API directly. Any Claude Code authentication method works (API key, Vertex AI, etc.).

## Running the factory

```bash
factory ceo /path/to/project                    # Launch CEO agent (single cycle)
factory ceo /path/to/project --mode meta        # Improve + ACE playbook evolution
factory ceo /path/to/project --focus "dashboard UI"  # Focus on a specific area
factory ceo --prompt "Build a weather CLI"      # Build from a raw prompt
factory run /path/to/project                    # Same as factory ceo
factory run /path/to/project --loop --interval 1800  # Continuous heartbeat
factory tmux /path/to/project --loop            # In detached tmux session
factory agent researcher --task "..." --project /path  # Invoke a specialist directly
factory dashboard --projects-dir ~/factory-projects    # Live web dashboard on :8420
factory export /path/to/project                 # Dump full project snapshot as JSON
factory checkpoint /path/to/project             # Save CEO state for crash recovery
factory resume /path/to/project                 # Resume from saved checkpoint
factory diff /path --exp1 N --exp2 M            # Compare two experiments
factory explain /path --exp N                   # Explain experiment with FEEC analysis
factory deferred-list /path                     # List pending deferred items
factory deferred-remove /path "item text"       # Remove a completed deferred item
factory precheck /path --score-before 0.7 --score-after 0.85  # Hard precheck gate
factory review --verdict KEEP --pr 42           # Post structured review on GitHub PR
```

`factory run` / `factory ceo` spawn the CEO agent as a `claude -p` subprocess. The CEO owns the full workflow: state detection, agent spawning, experiment lifecycle, and mandatory archival. The `--loop` flag adds a heartbeat wrapper with configurable interval and max cycles. `--mode meta` runs the full Improve loop on the factory itself, then ACE playbook evolution for all 7 agent roles. `--focus` narrows improvement efforts to a specific area (e.g. `--focus "eval reliability"`), ensuring at least 2 of 3 hypotheses target that area. `--prompt` builds a new project from a raw text description.

## Observability

**Events**: All agent invocations and cycle transitions are logged to `.factory/events.jsonl` as append-only structured events. The agent runner (`factory/agents/runner.py`) emits `agent.started`, `agent.completed`, `agent.failed`, and `agent.timeout` events automatically. The heartbeat loop emits `cycle.started` and `cycle.completed`.

**Dashboard**: `factory dashboard` starts a FastAPI server (default port 8420) that serves a live web UI with SSE-powered event streaming. It scans a projects directory for all `.factory/`-managed projects and shows real-time agent activity, experiment history, and project scores. Designed to run on an always-on machine.
