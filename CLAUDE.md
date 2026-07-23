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

## Architecture (v2 — CEO Agent + Workflow Graph Engine)

The factory is a **four-layer system**:

### Layer 1: Python CLI (`factory/`)

Pure tools that don't make decisions. Entry point is `factory/cli.py` → `factory.cli:main` (registered as `factory` script in pyproject.toml). Each subcommand is a `cmd_*` function dispatched via a handler dict. Key modules include `factory/clean_pr.py` (Clean PR Mode — strips non-essential artifacts from PRs before pushing to external repos).

### Layer 2: Workflow Graph Engine (`factory/workflow/`)

All 9 factory modes (build, design, improve, research, meta, discover, review, refine, founder) are defined as directed graphs of typed nodes in `factory/workflow/definitions.py`. Each graph is a `Workflow` Pydantic model with `AgentNode`, `FnNode`, `GateNode`, `ForkNode`, `JoinNode`, and `Study` primitives connected by `Edge` objects. See `factory/workflow/README.md` for full documentation.

The same graph definition produces two execution formats:
- **Headless:** `WorkflowExecutor` (`factory/workflow/executor.py`) walks the DAG deterministically — `factory workflow run <name> --project /path`
- **Interactive:** `skill_export.py` converts graphs to Claude Code `SKILL.md` files under `skills/workflow-*/` — the CEO agent reads these at runtime as mode-specific playbooks

### Layer 3: CEO Agent (`factory/agents/prompts/ceo.md` + `skills/workflow-*/SKILL.md`)

The CEO prompt is split into two parts:
- **`ceo.md` (501 lines)** — core identity, cross-cutting rules (Sacred Rules, FEEC, keep/revert framework, review gates, error recovery, self-learning). No mode-specific procedures.
- **`skills/workflow-*/SKILL.md` (8 files)** — each mode's full step-by-step playbook, auto-generated from the workflow graph definitions via `factory workflow export-skills`.

Spawned via `factory ceo /path` or `factory run /path`. The CEO receives `ceo.md` as its system prompt, detects project state, then reads the appropriate `SKILL.md` into its context and follows it as the mode-specific playbook.

### Layer 4: Specialist Agents (`factory/agents/`)

Eight specialist Claude Code subprocesses spawned by the CEO via `factory agent <role>`. Agent prompts are resolved via `factory/agents/runner.py` with a two-tier lookup: project-specific override (`.factory/agents/<role>.md`) then factory default (`factory/agents/prompts/<role>.md`). Evolved playbooks from `~/.factory/playbooks/<role>.md` (user-local, ACE-generated) are auto-injected, falling back to factory defaults in `factory/agents/playbooks/<role>.md`.

**Roles:** Researcher (observe), Strategist (hypothesize and refine ideas), Builder (implement), QA (health check + code review + adversarial QA), Archivist (record), Refiner (scope refinements), Failure Analyst (research mode), CEO (orchestrate).

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
10. **Adversarial** (`factory/adversarial.py`): GAN-style adversarial eval loop state machine — phase transitions with hysteresis, per-role streak counters, convergence detection. State persisted at `.factory/adversarial_state.json`

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
├── adversarial_state.json    # Adversarial loop state (phase, streaks, history)
├── archive/                  # Long-term knowledge store (Archivist notes)
│   ├── experiments/          # Per-experiment learnings and decision rationale
│   ├── patterns/             # Recurring patterns and anti-patterns
│   └── decisions/            # Major architectural and strategy decisions
└── agents/                   # Per-project agent prompt overrides
```

### Models

All domain models live in `factory/models.py` as strict Pydantic v2 models. Key types: `ProjectState` (enum), `FactoryConfig`, `EvalProfile` / `EvalDimension`, `CompositeScore` / `EvalResult`, `ExperimentRecord`, `CrossProjectInsights`, `AgentVerdict`, `Observation`, `PerformanceReport`, `ProjectEntry` / `ProjectRegistry`, `AdversarialConfig` / `AdversarialComponent` / `AdversarialState` / `AdversarialPhaseRecord`. The `Notifier` protocol defines the async notification interface. `FactoryConfig` includes `clean_pr` (bool), `clean_pr_include` (list[str]), and `clean_pr_exclude` (list[str]) for Clean PR Mode — stripping non-essential artifacts from PRs before pushing to external repos. `FactoryConfig.adversarial` (`AdversarialConfig | None`) holds the GAN-style adversarial eval loop configuration parsed from `factory.md`.

## Environment

Requires Claude Code installed and authenticated. The factory spawns `claude` subprocesses — it does not call the API directly. Any Claude Code authentication method works (API key, Vertex AI, etc.).

### Configuration (`~/.factory/config.toml`)

All `FACTORY_*` environment variables can also be set in `~/.factory/config.toml`. Env vars remain supported — config.toml is additive. Five-tier precedence: CLI flag > env var > profile credential > config.toml default > hardcoded default.

```toml
[defaults]
runner = "claude"
model = ""
projects_dir = "~/factory-projects"

[credentials.vertex]
FACTORY_RUNNER = "claude"
ANTHROPIC_API_KEY = "sk-ant-..."
```

**Commands:**
- `factory config show [--reveal]` — show resolved config with secrets masked
- `factory config edit` — open `~/.factory/config.toml` in `$EDITOR`
- `factory config migrate` — create starter config from current env vars (requires `tomli_w`)

**Credential profiles:** Use `--profile <name>` with `factory ceo`, `factory run`, or `factory agent` to load a `[credentials.<name>]` section. Profile keys are injected into `os.environ`.

**Implementation:** `factory/user_config.py` — `load_config()`, `resolve()`, `show_config()`, `migrate_env_to_config()`.

## Runners

The factory supports multiple CLI backends via the runner abstraction (`factory/runners/`). By default, it uses Claude Code (`claude` CLI). Bob Shell (`bob` CLI) and OpenAI Codex (`codex` CLI) are also supported as switchable alternatives.

**Runner selection:** Set `FACTORY_RUNNER=codex` (or `bob`) to switch backends, or pass `--runner codex` to individual commands. Default is `claude`.

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

**Codex specifics:**
- Requires `CODEX_API_KEY` (or `OPENAI_API_KEY`) environment variable (or set via config.toml profile)
- `CODEX_API_KEY` is auto-mapped to `OPENAI_API_KEY` in subprocess env if needed
- Headless mode uses `codex exec` with `--sandbox workspace-write --ask-for-approval never`
- Model selection via `--model` flag (e.g., `gpt-5.4`, `gpt-5.2-codex`)
- Progress streams to stderr, final message to stdout (matches factory capture model)
- Install: `npm install -g @openai/codex`

**Codex dry-run mode:** Set `FACTORY_CODEX_DRY_RUN=1` to test Codex integration without spending tokens.

**Codex config profile example** (`~/.factory/config.toml`):
```toml
[credentials.codex]
FACTORY_RUNNER = "codex"
CODEX_API_KEY = "..."
```
Then run: `factory ceo /path/to/project --profile codex`

**OpenCode specifics:**
- Requires `OPENAI_API_KEY` environment variable
- The factory targets `opencode-ai/opencode` v0.x (uses `-p`, `-q`, `-c` flags). Install from source: `go install github.com/opencode-ai/opencode@latest`, or via the [GitHub release tarball](https://github.com/opencode-ai/opencode/releases)
- Do NOT use the `curl` installer at `opencode.ai/install` — it installs the `anomalyco/opencode` fork (v1.x) which has an incompatible CLI interface
- Dry-run mode: `FACTORY_OPENCODE_DRY_RUN=1`

**Important:** Target projects should add `.factory/` to their `.gitignore`. The factory writes experiment data, usage logs, and potentially sensitive auth files (`.factory/.bob_auth`) to this directory. These are project-local artifacts that should not be committed to version control.

## Running the factory

```bash
# Build — from idea, spec file, or GitHub URL
factory ceo "Build a weather CLI"               # Raw idea → ~/factory-projects/weather-cli/
factory ceo "Build a weather CLI" --dir my-app  # Explicit dir name override
factory ceo ~/ideas/spec.md                     # Spec file → new project
factory ceo https://github.com/user/repo        # Clone and improve
factory ceo "distributed eval runner" --mode design  # Brainstorm → build
factory ceo /path/to/project --mode design           # Discuss what to work on → improve
factory ceo /path/to/project --mode design --focus "auth"  # Discuss a specific topic
factory ceo "SWE-bench solver" --mode research            # Research ideation → build
factory ceo /path/to/factory --mode create --focus "mode description"  # Create a new factory mode
factory ceo /path/to/factory --mode create --focus "improve: add plateau detection"  # Update existing mode

# Improve — point at existing codebase
factory ceo /path/to/project                    # Single improvement cycle
factory run /path/to/project --loop --interval 1800  # Continuous heartbeat
factory tmux /path/to/project --loop            # In detached tmux session

# Focus — build exactly one thing
factory ceo /path/to/project --focus "dashboard UI"  # One item, one hypothesis, done
factory ceo /path/to/project --focus 42              # Target GitHub issue #42
factory ceo /path/to/project --focus "owner/repo#42" # Target issue by shorthand

# Founder — rapid prototyping (NOT for production)
factory ceo /path/to/project --mode founder                       # One fast hypothesis
factory ceo /path/to/project --mode founder --focus "auth flow"   # Targeted prototype
factory run /path/to/project --mode founder --loop --interval 300 # Rapid iteration

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

# Adversarial eval loops
factory adversarial-state /path/to/project           # Inspect adversarial loop state
factory adversarial-state /path/to/project --reset   # Reset to defaults

# Operations
factory dashboard --projects-dir ~/factory-projects    # Live web dashboard on :8420
factory export /path/to/project                 # Dump full project snapshot as JSON
factory checkpoint /path/to/project             # Save CEO state for crash recovery
factory resume /path/to/project                 # Resume from saved checkpoint
factory precheck /path --score-before 0.7 --score-after 0.85  # Hard precheck gate
factory review --verdict KEEP --pr 42           # Post structured review on GitHub PR
```

`factory run` / `factory ceo` spawn the CEO agent as a subprocess using the selected runner (`claude` by default, or `bob` with `--runner bob`). The CEO owns the full workflow: state detection, agent spawning, experiment lifecycle, and mandatory archival. The `--loop` flag adds a heartbeat wrapper with configurable interval and max cycles. `--mode meta` runs the full Improve loop on the factory itself, then ACE playbook evolution for all agent roles. `--focus` activates targeted mode: builds exactly one item and exits. Accepts backlog names (`--focus "eval reliability"`), issue numbers (`--focus 42`), issue URLs, or `owner/repo#N` shorthand. Issue refs are auto-detected and fetched via `gh`/`glab` CLI. Works in improve, research, and create modes; mutually exclusive with `--loop`. In create mode, `--focus` provides the mode description; use `--focus "mode_name: change description"` to update an existing registered mode instead of creating a new one. `--mode design` enters ideation mode. For new ideas (e.g. `factory ceo "distributed eval runner" --mode design`), the CEO researches the space via the Researcher, then iteratively refines the idea with the Strategist through user feedback, producing a phased build plan before building. For existing projects (e.g. `factory ceo /path/to/project --mode design`), the CEO studies the project (backlog, eval scores, open issues, history), presents findings, and discusses what to work on before transitioning to Improve mode. `--mode interactive` is accepted as a backward-compatible alias for `--mode design`. `--focus` is allowed on existing projects to seed the discussion topic. Incompatible with `--headless`. `--mode research` enters research ideation for new projects (e.g. `factory ceo "SWE-bench solver" --mode research`) — the Strategist collects research config (target metric, mutable/fixed surfaces, constraints) before building. For existing projects with `research_target` configured, runs the research improvement loop directly. Incompatible with `--headless` (for new projects) and `--prompt`. `--refine "<request>"` enters refinement mode — routes a single change request through the Refiner → Builder → full review pipeline. Mutually exclusive with `--mode`, `--prompt`, and `--focus`. Requires an existing project directory. In foreground mode, the CEO also enters the refinement loop automatically after completing a build/improve cycle, staying active for follow-up requests without `--refine`. `--mode founder` enters rapid prototyping mode — a stripped-down pipeline (Study → Strategist → Builder → health gate → record) with 2 agent calls and 1 test run. Skips research, code review, adversarial QA, and eval scoring. Designed for fast hypothesis iteration: test an idea, see if it works, pivot. Terminal mode — does not chain to other modes. Not for production use; run `--mode improve` afterward to harden what works. Compatible with `--focus` and `--loop`.

## Observability

**Events**: All agent invocations and cycle transitions are logged to `.factory/events.jsonl` as append-only structured events. The agent runner (`factory/agents/runner.py`) emits `agent.started`, `agent.completed`, `agent.failed`, and `agent.timeout` events automatically. The heartbeat loop emits `cycle.started` and `cycle.completed`.

**Dashboard**: `factory dashboard` starts a FastAPI server (default port 8420) that serves a live web UI with SSE-powered event streaming. It scans a projects directory for all `.factory/`-managed projects and shows real-time agent activity, experiment history, and project scores. Designed to run on an always-on machine.
