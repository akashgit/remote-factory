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

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` — async test functions run without `@pytest.mark.asyncio`. Shared fixtures (`tmp_project`, `sample_config`, `python_project`) live in `tests/conftest.py`. An autouse `_isolate_registry` fixture redirects the global registry to a temp directory during tests.

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

A dedicated Claude Code agent that owns the full factory workflow. Spawned via `factory ceo /path` or `factory run /path`. The CEO detects project state, routes to modes (Build/Discover/Review/Improve/Research/Interactive/Meta), spawns specialist agents, makes keep/revert decisions, and ensures mandatory archival. SKILL.md is a thin launcher shim that spawns the CEO.

### Layer 3: Specialist Agents (`factory/agents/`)

Nine specialist Claude Code subprocesses spawned by the CEO via `factory agent <role>`. Agent prompts are resolved via `factory/agents/runner.py` with a two-tier lookup: project-specific override (`.factory/agents/<role>.md`) then factory default (`factory/agents/prompts/<role>.md`). Evolved playbooks from `~/.factory/playbooks/<role>.md` (user-local, ACE-generated) are auto-injected, falling back to factory defaults in `factory/agents/playbooks/<role>.md`.

**Roles:** Researcher (observe), Strategist (hypothesize), Builder (implement), Reviewer (guard), Evaluator (measure), Archivist (record), Distiller (refine ideas), Scrum Master (standup/resume), CEO (orchestrate).

### Key data flow

1. **State detection** (`factory/state.py`): Checks git, `.factory/config.json`, and `eval_profile.json` to determine one of 5 `ProjectState` enum values
2. **Discovery** (`factory/discovery/`): `introspect.py` → `profile.py` → `generate.py` — detects project language/framework, builds an `EvalProfile` of dimensions, generates `eval/score.py`
3. **Eval** (`factory/eval/`): `runner.py` executes the eval command as a subprocess, expects JSON stdout `{"results": [...]}`. Growth dimensions (`growth.py`) are computed locally and merged at 50/50 with project hygiene dimensions. `scorer.py` computes the weighted composite
4. **Strategy** (`factory/strategy.py`): FEEC priority heuristic (Fix > Exploit > Explore > Combine) classifies hypotheses by keyword matching, with stuck detection after 3+ consecutive same-category reverts
5. **Store** (`factory/store.py`): `ExperimentStore` manages the `.factory/` directory — config, TSV history, per-experiment dirs with hypothesis/eval/diff/verdict artifacts. Auto-registers projects in the global registry on `begin()` and updates stats on `finalize()`
6. **Registry** (`factory/registry.py`): Global project registry at `~/.factory/registry.json` — self-registration pattern, project discovery for ACE/insights without `--projects-dir`
7. **Report** (`factory/report.py`): Performance report generation — consolidates experiment records, CEO verdicts, and observations into `.factory/performance_report.json` for ACE consumption
8. **Checkpoint** (`factory/checkpoint.py`): Saves and loads CEO state for crash-resilient resume
9. **Analysis** (`factory/analysis.py`): Experiment comparison (`diff`) and FEEC analysis (`explain`)

### Target project's `.factory/` layout

```
.factory/
├── config.json               # Parsed from factory.md (FactoryConfig model)
├── eval_profile.json         # Discovered eval dimensions (EvalProfile model)
├── results.tsv               # Append-only experiment history
├── performance_report.json   # Consolidated project data for ACE (auto-generated)
├── experiments/
│   └── 001/                  # Per-experiment: hypothesis.md, eval_before.json, eval_after.json, changes.diff, verdict.json
├── strategy/                 # observations.md, current.md, backlog.md, insights.md, research.md
├── reviews/                  # Agent output capture + CEO review verdicts
│   ├── <role>-latest.md      # Auto-saved stdout from each agent invocation
│   └── ceo-verdict-<role>.md # CEO's review verdict (PROCEED/REDIRECT/ABORT)
├── archive/                  # Long-term knowledge store (Archivist notes)
│   ├── experiments/          # Per-experiment learnings and decision rationale
│   ├── patterns/             # Recurring patterns and anti-patterns
│   └── decisions/            # Major architectural and strategy decisions
└── agents/                   # Per-project agent prompt overrides
```

### Models

All domain models live in `factory/models.py` as strict Pydantic v2 models. Key types: `ProjectState` (enum), `FactoryConfig`, `EvalProfile` / `EvalDimension`, `CompositeScore` / `EvalResult`, `ExperimentRecord`, `CrossProjectInsights`, `AgentVerdict`, `Observation`, `PerformanceReport`, `ProjectEntry` / `ProjectRegistry`. The `Notifier` protocol defines the async notification interface.

## Environment

Requires Claude Code installed and authenticated. The factory spawns `claude` subprocesses — it does not call the API directly. Any Claude Code authentication method works (API key, Vertex AI, etc.).

## Runners

The factory supports multiple CLI backends via the runner abstraction (`factory/runners/`). By default, it uses Claude Code (`claude` CLI), but Bob Shell (`bob` CLI) is also supported as a switchable alternative.

**Runner selection:** Set `FACTORY_RUNNER=bob` to use Bob Shell, or pass `--runner bob` to individual commands. Default is `claude`.

**Bob Shell specifics:**
- Requires `BOBSHELL_API_KEY` environment variable to be set
- Uses 'code' mode; agent role definitions are injected via the prompt
- Model selection is not configurable (Bob Shell uses its default model)

**Dry-run mode:** Set `FACTORY_BOB_DRY_RUN=1` to test Bob Shell integration without spending tokens. The factory returns stub responses and logs usage. This is automatically set in tests via `tests/conftest.py`.

**Token guardrails:** Bob Shell has no token telemetry, so the factory self-enforces invocation ceilings:
- `FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE` (default: 8)
- All invocations are logged to `.factory/bob_usage.jsonl`
- When ≤2 invocations remain before the ceiling, a warning is logged and emitted to `.factory/events.jsonl` (type: `bob.ceiling_warning`)
- Ceiling violations emit events to `.factory/events.jsonl` and abort with an actionable error message

**Important:** Target projects should add `.factory/` to their `.gitignore`. The factory writes experiment data, usage logs, and potentially sensitive auth files (`.factory/.bob_auth`) to this directory. These are project-local artifacts that should not be committed to version control.

## Running the factory

```bash
# Build — from idea, spec file, or GitHub URL
factory ceo "Build a weather CLI"               # Raw idea as positional arg
factory ceo ~/ideas/spec.md                     # Spec file → new project
factory ceo https://github.com/user/repo        # Clone and improve
factory ceo "distributed eval runner" --mode interactive  # Brainstorm → build
factory ceo "SWE-bench solver" --mode research            # Research ideation → build

# Improve — point at existing codebase
factory ceo /path/to/project                    # Single improvement cycle
factory run /path/to/project --loop --interval 1800  # Continuous heartbeat
factory tmux /path/to/project --loop            # In detached tmux session

# Focus — build exactly one thing
factory ceo /path/to/project --focus "dashboard UI"  # One item, one hypothesis, done

# Meta — improve the factory's own agents
factory ceo /path/to/project --mode meta        # Improve + ACE playbook evolution

# Agents & analysis
factory agent researcher --task "..." --project /path  # Invoke a specialist directly
factory study /path                             # Analyze code + write observations
factory diff /path --exp1 N --exp2 M            # Compare two experiments
factory explain /path --exp N                   # Explain experiment with FEEC analysis

# Backlog
factory backlog-list /path                      # List pending backlog items
factory backlog-add /path "item text"           # Add a new item to the backlog
factory backlog-remove /path "item text"        # Remove a completed backlog item

# Operations
factory dashboard --projects-dir ~/factory-projects    # Live web dashboard on :8420
factory export /path/to/project                 # Dump full project snapshot as JSON
factory checkpoint /path/to/project             # Save CEO state for crash recovery
factory resume /path/to/project                 # Resume from saved checkpoint
factory precheck /path --score-before 0.7 --score-after 0.85  # Hard precheck gate
factory review --verdict KEEP --pr 42           # Post structured review on GitHub PR
```

`factory run` / `factory ceo` spawn the CEO agent as a subprocess using the selected runner (`claude` by default, or `bob` with `--runner bob`). The CEO owns the full workflow: state detection, agent spawning, experiment lifecycle, and mandatory archival. The `--loop` flag adds a heartbeat wrapper with configurable interval and max cycles. `--mode meta` runs the full Improve loop on the factory itself, then ACE playbook evolution for all agent roles. `--focus` activates targeted mode: builds exactly one backlog item (e.g. `--focus "eval reliability"`), generating a single hypothesis and exiting after that experiment. Works in improve and research modes; mutually exclusive with `--loop`. `--mode interactive` enters ideation mode: pass a raw idea as the positional argument (e.g. `factory ceo "distributed eval runner" --mode interactive`). The CEO researches the space via the Researcher, then iteratively refines the idea with the Distiller agent through user feedback, producing an idea.md spec before building. Incompatible with `--headless` and `--focus`. `--mode research` enters research ideation for new projects (e.g. `factory ceo "SWE-bench solver" --mode research`) — the Distiller collects research config (target metric, mutable/fixed surfaces, constraints) before building. For existing projects with `research_target` configured, runs the research improvement loop directly. Incompatible with `--headless` (for new projects) and `--prompt`.

## Observability

**Events**: All agent invocations and cycle transitions are logged to `.factory/events.jsonl` as append-only structured events. The agent runner (`factory/agents/runner.py`) emits `agent.started`, `agent.completed`, `agent.failed`, and `agent.timeout` events automatically. The heartbeat loop emits `cycle.started` and `cycle.completed`.

**Dashboard**: `factory dashboard` starts a FastAPI server (default port 8420) that serves a live web UI with SSE-powered event streaming. It scans a projects directory for all `.factory/`-managed projects and shows real-time agent activity, experiment history, and project scores. Designed to run on an always-on machine.
