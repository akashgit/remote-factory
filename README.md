# Remote Factory

Domain-agnostic multi-agent software evolution loop. Automatically discovers what to measure in any project, then coordinates specialized agents to continuously improve it — not just fixing what's broken, but growing new capabilities informed by research.

## What is it?

The factory is a Python CLI + orchestration system that:

1. **Discovers** what to measure in a project (tests, lint, type checking, coverage, observability)
2. **Evaluates** the project with a dual-axis composite score (hygiene + growth)
3. **Hypothesizes** improvements using FEEC priority ranking and cross-project insights
4. **Implements** changes via builder agents (Claude subprocesses)
5. **Guards** against scope violations and regressions
6. **Archives** decisions and patterns to an Obsidian vault

## Installation

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended package manager)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (for running agents)
- Google Cloud SDK with Vertex AI access (for Claude API)
- tmux (optional, for long-running sessions over SSH)

### Quick install

```bash
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync
```

This installs the `factory` CLI as an entry point. Verify with:

```bash
factory --help
```

### Environment variables

All Claude access goes through Google Vertex AI. Add to your shell config:

```bash
export CLAUDE_CODE_USE_VERTEX=1
export CLOUD_ML_REGION=your-region
export ANTHROPIC_VERTEX_PROJECT_ID=<your-gcp-project-id>
```

Optional variables:

```bash
export OBSIDIAN_VAULT_PATH=~/factory-vault   # Obsidian vault for archival
export TELEGRAM_BOT_TOKEN=<token>                       # Telegram notifications
export TELEGRAM_CHAT_ID=<chat-id>
```

### Claude Code setup

The factory's brain is a **skill** (`SKILL.md`) and its specialist agents live in `.claude/agents/`. Claude Code auto-discovers both when you run it from the repo directory — no manual registration needed.

1. **Install Claude Code** — follow [the official guide](https://docs.anthropic.com/en/docs/claude-code). On macOS:

   ```bash
   npm install -g @anthropic-ai/claude-code
   ```

2. **Verify skill discovery** — from the repo root, launch Claude Code and type `/factory`. If the skill loads, you're set.

3. **Agent definitions** live in `.claude/agents/` and are also auto-discovered. Currently shipped:

   | File | Agent | Model |
   |------|-------|-------|
   | `.claude/agents/researcher.md` | Deep research (web search, vault reads) | Sonnet |

   The remaining agents (strategist, builder, reviewer, evaluator, archivist) are orchestrated inline by the `/factory` skill via their prompts in `factory/agents/prompts/`.

### Running on a new machine

```bash
# 1. Install tooling
npm install -g @anthropic-ai/claude-code   # Claude Code CLI
curl -LsSf https://astral.sh/uv/install.sh | sh  # uv (if not installed)

# 2. Clone and install
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync

# 3. Set up Vertex AI auth
gcloud auth login
gcloud auth application-default login
gcloud config set project <your-gcp-project-id>

# 4. Export env vars (add to ~/.zshrc or ~/.bashrc)
export CLAUDE_CODE_USE_VERTEX=1
export CLOUD_ML_REGION=your-region
export ANTHROPIC_VERTEX_PROJECT_ID=<your-gcp-project-id>

# 5. Verify the CLI
factory detect /path/to/some/project

# 6. Verify Claude Code + skill
cd remote-factory
claude   # then type /factory inside the session
```

### Optional: Obsidian vault

The factory archives experiment history and cross-project knowledge to an Obsidian vault. Initialize it on a new machine with:

```bash
factory vault-init
```

This creates `~/factory-vault/` with the expected directory structure. If you use Obsidian, point it at this vault.

## Architecture

Three layers work together:

**Python CLI** (`factory/`) -- the engine. Commands like `factory detect`, `factory eval`, `factory study`. Pure tools that don't make decisions.

**Skill** (`SKILL.md`) -- the brain. An orchestration protocol loaded into Claude's context via `/factory`. Defines the workflow: observe, hypothesize, build, eval, keep/revert.

**Agents** -- 6 specialist Claude subprocesses spawned by the orchestrator:

| Agent | Role |
|-------|------|
| **Researcher** | Analyze code, find gaps, search for best practices |
| **Strategist** | Generate ranked hypotheses from observations and scores (FEEC priority) |
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

## Eval System

The eval system uses a **dual-axis** composite score. Hygiene dimensions (project-specific, discovered automatically) and growth dimensions (universal, injected by the runner) each contribute 50% to the final score. This prevents the factory from stagnating on polish — it must also grow.

### Hygiene dimensions (50%)

Discovered per-project using a 3-tier resolution:

| Tier | Source | Confidence |
|------|--------|------------|
| Explicit | User wrote `eval/score.py` | 1.0 |
| Discovered | Factory finds pytest, ruff, mypy in project config | 0.8 |
| Researched | Factory infers from project type + best practices | 0.5 |
| Fallback | Basic checks: does it build? does it import? | 0.2 |

Auto-generated evals enter `EVALS_PENDING_REVIEW` state -- they cannot drive the improvement loop until a human approves them.

Built-in hygiene dimensions:

| Dimension | What it measures |
|-----------|-----------------|
| tests | Test suite passes |
| lint | Linter passes (ruff, eslint, clippy) |
| type_check | Type checker passes (mypy, tsc) |
| coverage | Test coverage |

### Growth dimensions (50%)

Universal dimensions that measure the factory's ability to evolve the project, not just maintain it:

| Dimension | Weight | What it measures |
|-----------|--------|-----------------|
| Capability surface | 28% | Breadth of modules, public functions, and CLI entry points |
| Experiment diversity | 22% | Spread of hypothesis categories; penalizes repetition and dominance |
| Observability | 20% | Logging coverage, structured logging, request tracing |
| Research grounding | 16% | Whether improvements are informed by vault sources and research |
| Factory effectiveness | 14% | Keep rate, positive deltas, multi-project reach |

Growth dimensions are computed by `factory/eval/growth.py` and merged by the eval runner at a 50/50 split with hygiene scores.

### FEEC priority heuristic

The strategist ranks hypotheses using Fix -> Exploit -> Explore -> Combine priority:

1. **Fix** — repair broken things (tests, lint, type errors)
2. **Exploit** — improve what's already working (coverage, observability)
3. **Explore** — add new capabilities (new modules, features, integrations)
4. **Combine** — cross-cutting improvements (refactors, cross-project patterns)

Stuck detection: 3+ consecutive reverts in the same category triggers a forced category shift.

### Cross-project insights

`factory insights` analyzes experiment histories across multiple projects to identify winning and losing strategies, category-level patterns, and cross-project opportunities.

## Observability

The factory treats observability as a first-class concern. It is analyzed during **study** and scored during **eval**.

**Study phase** (`factory study`): deep observability coverage analysis -- identifies uninstrumented files, detects logging frameworks, and generates recommendations.

**Eval phase**: the observability growth dimension scores the project on:

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| Function coverage | 40% | Fraction of functions with log statements |
| Structured logging | 25% | Whether structlog/pino/winston/slog is used |
| Request tracing | 20% | Whether request ID / correlation ID patterns exist |
| Log density | 15% | Log statements per function |

**Strategy phase**: if observability score is below 0.5, the strategist MUST generate an observability improvement hypothesis as HIGH priority.

## Usage

```bash
# Use the factory skill in Claude Code
/factory

# Or run commands directly
factory detect ~/factory-projects/my-project
factory eval ~/factory-projects/my-project
```

### CLI commands

| Command | Description |
|---------|-------------|
| `factory home` | Print factory installation root (used by SKILL.md for portable paths) |
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
| `factory insights <path>` | Cross-project analysis of experiment histories |
| `factory digest` | Summarize recent factory activity across projects |
| `factory archive <path>` | Write experiment notes to Obsidian vault |
| `factory vault-init` | Create the factory Obsidian vault |
| `factory notify <path>` | Send Telegram digest |
| `factory run <path>` | Run a full factory cycle (for cron/automation) |
| `factory tmux <path>` | Launch factory in a detached tmux session |
| `factory tmux-ls` | List running factory tmux sessions |
| `factory tmux-stop` | Stop factory tmux session(s) |

## Project structure

```
factory/
├── models.py              # Pydantic v2 models (config, eval, experiments)
├── state.py               # State machine (5 states)
├── store.py               # .factory/ filesystem store
├── cli.py                 # CLI entry point (argparse subcommands)
├── study.py               # Interaction log analysis + observability coverage
├── insights.py            # Cross-project pattern analysis
├── digest.py              # Activity summarization across projects
├── strategy.py            # FEEC priority heuristic for hypothesis ranking
├── discovery/
│   ├── introspect.py      # Project introspection (language, framework, tools)
│   ├── profile.py         # Build eval profile from project metadata
│   └── generate.py        # Generate eval/score.py from profile
├── eval/
│   ├── runner.py          # Run eval commands, merge hygiene + growth scores
│   ├── growth.py          # Universal growth dimensions (5 dimensions)
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

## Obsidian integration

The factory uses a dedicated Obsidian vault (`~/factory-vault/`) for institutional memory:

```
~/factory-vault/
├── 00-Factory/          # Cross-project knowledge (Dashboard, Patterns)
├── 10-Projects/{name}/  # Per-project notes (Experiments, Strategies)
├── 20-Knowledge/        # Concepts and external sources
├── _templates/          # Note templates
└── MEMORY.md            # Thin pointer index for agent orientation
```

Initialize the vault with:

```bash
factory vault-init
```

## Running in tmux

For SSH sessions or long-running factory jobs, launch in tmux so the factory survives disconnects:

```bash
# Launch factory on a project (detached)
factory tmux ~/factory-projects/cloud-gateway --loop --interval 1800

# With max cycles
factory tmux ~/factory-projects/cloud-gateway --loop --max-cycles 5

# Attach to watch progress
factory tmux ~/factory-projects/cloud-gateway --attach

# List running factory sessions
factory tmux-ls

# Stop a session
factory tmux-stop --session factory-cloud-gateway

# Stop all factory sessions
factory tmux-stop
```

The session is named `factory-<project-name>` by default (e.g., `factory-cloud-gateway`). Vertex AI env vars are automatically set inside the tmux session.

## Development

```bash
uv sync              # Install dependencies (including dev group)
pytest -v            # Run tests
ruff check .         # Lint
mypy factory/        # Type check
pytest --cov         # Coverage report
```

### Style

- Python 3.11+ (use `X | Y` unions, not `Union[X, Y]`)
- Snake_case everywhere
- 100 char line length (enforced by ruff)
- All Pydantic models use `ConfigDict(strict=True)`
- Async/await by default

## Predecessor

This is v2 of the software factory. v1 (`software-factory`) was SEO-coupled with manual eval setup. v2 is domain-agnostic with auto-discovery, a dual-axis eval system, and a specialized agent topology.
