# The Factory: From Idea to Evolving Software

[![CI](https://github.com/akashgit/remote-factory/actions/workflows/ci.yml/badge.svg)](https://github.com/akashgit/remote-factory/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/akashgit/remote-factory/graph/badge.svg)](https://codecov.io/gh/akashgit/remote-factory)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Runner: Claude Code](https://img.shields.io/badge/runner-Claude_Code-7c3aed)](https://docs.anthropic.com/en/docs/claude-code)
[![Runner: Bob Shell](https://img.shields.io/badge/runner-Bob_Shell-f59e0b)](https://bob.ibm.com)

**Describe what you want. The Factory builds it, tests it, and keeps improving it — autonomously.** You give it a spec file, a rough idea, or an existing codebase. The Factory researches best practices, scaffolds the project, sets up evaluation, and runs a continuous improvement loop — measuring every change and keeping only what makes things better. The agents that do this work learn from every experiment and get sharper over time.

```bash
# Build — have a fleshed-out idea? Pass the file.
factory ceo ~/ideas/weather-dashboard.md

# Interactive — just starting to think about it? Brainstorm first.
factory ceo "let's create a pomodoro app" --mode interactive

# Research — iteratively improve a measurable metric
factory ceo "SWE-bench solver agent" --mode research

# Improve — point it at any codebase
factory ceo ~/my-project

# Focus — build exactly one thing
factory ceo ~/my-project --focus "add WebSocket support"
```

The CEO runs as a foreground Claude Code session — you can talk to it at any time, just like you would with Claude Code. Ask it what it's doing, steer it if something looks off, provide missing credentials, or redirect its focus mid-cycle. It's autonomous by default, collaborative when you want it to be.

Under the hood, the CEO orchestrates eight specialists — Researcher, Strategist, Builder, Reviewer, Evaluator, Archivist, Distiller, and itself — each running as an independent [Claude Code](https://docs.anthropic.com/en/docs/claude-code) subprocess. Every change is a hypothesis: scored before and after, kept only if it improves the score, archived as institutional memory. Failed experiments aren't wasted — they teach the agents what to avoid next time.

## What's New in v0.2.0

- **Bob Shell runner** — Alternative CLI backend via `--runner bob`. Includes dry-run mode, per-cycle/daily usage ceilings, and auth persistence for nested subagents
- **Interactive ideation** — `--mode interactive` launches a research → brainstorm → refine loop with the new Distiller agent before any code is written
- **Research ideation** — `--mode research` for metric-driven projects. Pass a raw idea and the Distiller collects research config (target metric, mutable/fixed surfaces, constraints) before building
- **Focused mode** — `--focus "add auth"` pins a single backlog item: one hypothesis, one experiment, done. Works in both improve and research modes
- **CEO completion guard** — Auto-resumes when the CEO exits prematurely. Cycle state persists across respawns with cross-cycle scoping to prevent stale experiment contamination
- **Unified backlog** — Replaces the old deferred-items system. The Strategist clears backlog items each cycle with convergence tracking
- **Session summaries** — End-of-cycle reports: what was built, what was deferred, what needs human input
- **Experiment checkpoint/resume** — CEO saves progress per-experiment for crash-resilient recovery
- **Auto-discovery** — Managed projects are auto-detected from the projects directory
- **Citation backfill** — Research grounding scores now extract and backfill citations from experiment history
- **1320 tests** — Up from 878 at initial release

See the [full changelog](CHANGELOG.md) for details.

## How It Works

```mermaid
graph LR
    A["🔍 Researcher<br><i>observe</i>"] --> B["🎯 Strategist<br><i>hypothesize</i>"]
    B --> C["🔨 Builder<br><i>implement</i>"]
    C --> D["📊 Evaluator<br><i>measure</i>"]
    D --> E{"CEO<br><i>decide</i>"}
    E -- "score ↑" --> F["✅ KEEP"]
    E -- "score ↓" --> G["↩️ REVERT"]
    F --> H["📝 Archivist<br><i>record</i>"]
    G --> H
    H -.-> A

    style E fill:#5c6bc0,color:#fff,stroke:#3949ab
    style F fill:#43a047,color:#fff,stroke:#2e7d32
    style G fill:#e53935,color:#fff,stroke:#c62828
```

Each cycle produces a measurable, auditable experiment. The Researcher observes the project, the Strategist generates ranked hypotheses using [FEEC priority](docs/architecture.md) (Fix > Exploit > Explore > Combine), the Builder implements one on an experiment branch, the Evaluator scores before/after, and the CEO decides keep or revert. The Archivist records every outcome for cross-project learning.

## Workflows

### Build — start from an idea

Give the Factory an idea and it builds a complete project from scratch: scaffolding, tests, eval, and iterative improvement.

```bash
# One-line idea
factory ceo "Build a REST API for bookmark management with tags and search"

# Detailed spec in a file
factory ceo ~/ideas/weather-dashboard.md

# Clone and improve a GitHub repo
factory ceo https://github.com/user/repo
```

The input is flexible — a raw string becomes the build spec, a `.md` file is read as a detailed spec, a GitHub URL is cloned and discovered. In all cases, the Factory auto-detects the right starting point.

### Improve — make an existing codebase better

Point the Factory at any codebase and it runs measured improvement cycles. Each cycle observes the project, hypothesizes changes, implements one, and keeps it only if the score goes up.

```bash
# Single improvement cycle
factory ceo ~/my-project

# Continuous background improvement
factory run ~/my-project --loop

# In a detached tmux session
factory tmux ~/my-project --loop
```

The Factory maintains a [backlog](docs/architecture.md) of work items. Each cycle, the Strategist picks items from the backlog, generates hypotheses, and the Builder implements them. Over time, the project gets better — and the agents learn what kinds of changes work for your codebase.

### Focus — build exactly one thing

When you know exactly what you want built, `--focus` pins a single backlog item and scopes the entire pipeline to it: one item, one hypothesis, one experiment, done.

```bash
factory ceo ~/my-project --focus "add authentication middleware"
factory ceo ~/my-project --focus "fix the CSV export bug"
factory ceo ~/my-project --focus "add structured logging"
```

If the item isn't already in the backlog, it gets added automatically. The Researcher scopes its research to the target, the Strategist generates exactly one hypothesis, the Builder implements it, and the cycle ends after the keep/revert decision. No other backlog items are touched.

`--focus` requires the project to already be built (improve or research mode). It's mutually exclusive with `--loop`.

### Research — iteratively improve a metric

For projects where the goal is to improve a measurable metric against a dataset — benchmarks, model tuning, prompt optimization — research mode runs a specialized loop with leakage guards and metric tracking:

```bash
# New research project — starts with ideation to collect config
factory ceo "SWE-bench solver agent" --mode research
factory ceo "prompt optimization for code review" --mode research

# Existing research project — runs the research improvement loop
factory ceo ~/my-research-project --mode research

# Focus on a specific research hypothesis
factory ceo ~/my-research-project --mode research --focus "try chain-of-thought prompting"
```

For new ideas, research mode enters an ideation phase (like `--mode interactive`) where the Distiller collects the research configuration: target metric, run command, mutable surfaces (files the Builder can edit), fixed surfaces (ground truth that must not be touched), and constraints. Once approved, the Factory builds the project and transitions to the research improvement loop.

Research mode requires foreground mode (incompatible with `--headless` for new projects).

### Interactive — brainstorm before building

When you have a rough idea but want to explore the space first, interactive mode runs a research → brainstorm → refine loop before any code is written:

```bash
factory ceo "distributed eval runner" --mode interactive
factory ceo "personal finance tracker" --mode interactive
```

The CEO spawns the Researcher to survey the landscape (similar projects, tech stacks, pitfalls), then the Distiller synthesizes a structured project spec. You review the draft, give feedback, and iterate until you approve — then the Factory proceeds to build it. No code is written until you sign off on the spec.

### Headless & continuous loop

By default, `factory ceo` launches an interactive Claude Code session — you can watch the agents work, steer decisions, and provide input. For unattended operation, two flags change that:

```bash
# Headless — pipe mode, no interaction (for scripting, cron jobs)
factory ceo ~/my-project --headless

# Loop — run improvement cycles continuously (default: every 30 min)
factory run ~/my-project --loop
factory run ~/my-project --loop --interval 3600        # every hour
factory run ~/my-project --loop --max-cycles 5         # stop after 5 cycles

# Detached tmux — loop in the background, come back later
factory tmux ~/my-project --loop
factory tmux-ls                                        # list active sessions
factory tmux-stop --path ~/my-project                  # stop a session
```

`--headless` uses `claude -p` (pipe mode) instead of a foreground session — useful for automation where no human is watching. `--loop` wraps the CEO in a heartbeat loop: run one cycle, sleep, repeat. Combine them with `factory tmux` to leave the Factory improving your project on an always-on machine. Each cycle is a full Researcher → Strategist → Builder → Evaluator → CEO decision pass; failed cycles don't stop the loop.

## Self-Evolving Agents

The factory doesn't just improve your project — it improves *itself*. Every keep/revert decision becomes training data for the next cycle.

This is powered by **ACE (Autonomous Context Engineering)** — inspired by Anthropic's work on [context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — a Reflect → Curate → Inject loop that evolves agent playbooks from real experiment outcomes:

```mermaid
graph LR
    A["Experiment Outcomes<br><i>kept or reverted</i>"] -->|Reflect| B["Generate<br>candidate bullets"]
    B -->|Curate| C["Merge & prune<br>playbooks"]
    C -->|Inject| D["Agent Prompts<br><i>auto-appended</i>"]
    D -.->|"next cycle"| A

    style A fill:#fff3e0,stroke:#ff8f00
    style D fill:#e8eaf6,stroke:#5c6bc0
```

Each agent accumulates behavioral rules — DOs and DON'Ts — with evidence counters. Rules that correlate with kept experiments get reinforced. Rules that correlate with reverts get pruned. The playbooks are human-readable markdown you can inspect and override.

```bash
# Run a full improvement cycle, then evolve all agent playbooks
factory ceo ~/my-project --mode meta
```

Meta mode is the factory's recursive self-improvement: improve the project, then improve the agents that improved the project. Over time, agents get sharper at the specific kinds of changes that work for *your* codebase. For active projects, run meta mode on a regular cadence — weekly is a good default, nightly if the factory is running many experiments per day.

Don't run it right after initial build or after every improve cycle; ACE needs enough experiment data (at least 5 across projects) to produce meaningful playbook updates. See [ACE Self-Improvement](docs/ace.md) for details.

## Quick Start

> **Active development** — The Factory is evolving rapidly. Always pull the latest before starting a session:
> ```bash
> cd remote-factory && git pull && uv sync
> ```

```bash
# Install from source (recommended — the factory evolves fast)
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory && uv sync && uv tool install -e .

# Register the CEO as a Claude Code agent
factory install

# Set up the Obsidian vault (highly recommended — gives the factory persistent memory)
export FACTORY_VAULT_PATH=~/factory-vault
factory vault-init

# Build something
factory ceo "Build a REST API for bookmark management with tags and search"
```

**Prerequisites:** Python 3.11+ and [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (installed and authenticated). See the [full setup guide](docs/setup.md).

**Why Obsidian?** The vault is the factory's long-term memory. Experiment history, cross-project insights, research notes, and agent learnings all live there. Without a vault, the factory still works but starts fresh every time. See [Obsidian setup](docs/setup.md#optional-obsidian-vault).

## Runners

The factory supports multiple CLI backends. By default, it uses **Claude Code** (`claude` CLI). **Bob Shell** (`bob` CLI) is available as an alternative.

### Selecting a Runner

```bash
# Via environment variable (affects all commands)
export FACTORY_RUNNER=bob

# Via CLI flag (per-command)
factory ceo ~/my-project --runner bob
factory run ~/my-project --loop --runner bob
```

### Bob Shell Setup

Bob Shell requires an API key:

```bash
# 1. Install Bob Shell (https://bob.ibm.com/docs/shell)
# 2. Set your API key
export BOBSHELL_API_KEY=your-key-here

# 3. Run with --runner bob
factory ceo ~/my-project --runner bob
```

The factory persists the API key to `.factory/.bob_auth` for nested agent spawns.

### Bob Shell Guardrails

Bob Shell lacks token telemetry, so the factory enforces invocation ceilings:

| Ceiling | Default | Environment Variable |
|---------|---------|---------------------|
| Per-cycle | 8 | `FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE` |

When a ceiling is exceeded, the factory aborts with an actionable error message.

**Dry-run mode** for testing (no API calls):

```bash
export FACTORY_BOB_DRY_RUN=1
factory ceo ~/my-project --runner bob  # Returns stub responses
```

See [CLAUDE.md](CLAUDE.md) for full runner implementation details.

## Architecture

Three layers, strict separation of concerns:

```mermaid
graph TB
    subgraph agents ["Specialist Agents"]
        R["Researcher"] ~~~ S["Strategist"] ~~~ BU["Builder"]
        RE["Reviewer"] ~~~ EV["Evaluator"] ~~~ AR["Archivist"]
        DI["Distiller"]
    end
    subgraph ceo ["CEO Agent"]
        C["Detect state → Route mode → Spawn agents → Keep/Revert → Archive"]
    end
    subgraph cli ["Python CLI"]
        T["eval · guard · store · discover · events · strategy"]
    end

    agents --> ceo --> cli

    style agents fill:#e8eaf6,stroke:#5c6bc0
    style ceo fill:#fff3e0,stroke:#ff8f00
    style cli fill:#e8f5e9,stroke:#43a047
```

The CEO detects your project's state and chooses the right mode automatically:

| State | What the CEO does |
|-------|------------------|
| No repo exists | **Build** — scaffold from your spec or prompt |
| Code exists, no `.factory/` | **Discover** — introspect project, generate eval dimensions |
| Factory initialized | **Improve** — run the experiment loop |
| Factory + `research_target` | **Research** — metric-driven improvement with leakage guards |

See [Architecture](docs/architecture.md) for the full technical deep-dive, including the eval system, FEEC strategy priority, and state machine.

## The Eval System

Every change is measured by a three-tier composite score:

```mermaid
graph LR
    subgraph hygiene ["Hygiene · 6 dims"]
        H1["tests · lint · types<br>coverage · guards · config"]
    end
    subgraph growth ["Growth · 5 dims"]
        G1["capability · diversity<br>observability · research<br>effectiveness"]
    end
    subgraph project ["Project · N dims"]
        P1["your custom metrics<br>benchmarks · latency<br>accuracy · win rate"]
    end

    hygiene --> M["⚖️ Weighted<br>Composite"]
    growth --> M
    project --> M
    M --> S{"score ≥<br>threshold?"}
    S -- "yes" --> K["✅ Keep"]
    S -- "no" --> R["↩️ Revert"]

    style hygiene fill:#e8eaf6,stroke:#5c6bc0
    style growth fill:#fff3e0,stroke:#ff8f00
    style project fill:#e8f5e9,stroke:#43a047
    style K fill:#43a047,color:#fff
    style R fill:#e53935,color:#fff
```

Default weight split is 50/50 hygiene/growth. When you define project-specific evals, it shifts to 30/20/50. Fully configurable via `factory.md`. See [Eval System](docs/eval.md).

## Project Configuration

Each managed project uses a `factory.md` file at its root. This tells the Factory what to improve, what to protect, and how to measure progress. The CEO auto-generates a starter version during discovery — you then refine it.

```markdown
## Goal
Build a fast, reliable REST API for user management.

## Scope
### Modifiable
- src/**
- tests/**

## Guards
- Do not delete existing tests
- Do not modify files outside scope
- Do not remove error handling

## Eval
### Command
pytest --tb=short -q

### Threshold
0.8
```

**What each section does:**

| Section | Purpose |
|---------|---------|
| **Goal** | One sentence that guides what hypotheses the Strategist generates |
| **Scope** | Glob patterns for files the Factory may edit — anything outside triggers a guard violation |
| **Guards** | Inviolable rules — violations force a revert regardless of eval score |
| **Eval** | How to run the project's tests; threshold is the minimum score to keep a change |

For advanced use cases you can also configure: custom eval dimensions (benchmark accuracy, latency), smoke tests (e2e health checks), hypothesis budgets (how many changes per cycle), target branches (stage work away from main), and eval weight distribution. See the [Configuration Reference](docs/configuration.md).

## CLI Reference

```bash
# Core workflow
factory ceo <path|url|idea>       # Launch the CEO agent
factory run <path> --loop         # Continuous heartbeat mode
factory tmux <path> --loop        # In detached tmux session

# Agents
factory agent <role> --task "..." --project <path>

# Evaluation
factory eval <path>               # Run evals, print composite score
factory precheck <path>           # Hard precheck gate (4 checks)
factory guard <path>              # Check guard rules

# Experiments
factory begin <path> --hypothesis "..."
factory finalize <path> --id N --verdict keep
factory history <path>
factory diff <path> --exp1 N --exp2 M
factory explain <path> --exp N

# Analysis
factory study <path>              # Analyze code + write observations
factory insights <path>           # Cross-project patterns
factory ace <path>                # ACE playbook evolution

# Backlog
factory backlog-list <path>       # List pending backlog items
factory backlog-add <path> "..."  # Add a new item to the backlog
factory backlog-remove <path> "..." # Remove a completed backlog item

# Operations
factory dashboard                 # Live web dashboard on :8420
factory detect <path>             # Print project state
factory discover <path>           # Introspect + generate eval profile
factory export <path>             # Full project snapshot as JSON
factory checkpoint <path>         # Save CEO state for crash recovery
factory resume <path>             # Resume from checkpoint
```

See `factory --help` for the complete list.

## Observability

- **Event log**: All agent invocations logged to `.factory/events.jsonl` as structured events
- **Live dashboard**: `factory dashboard` — FastAPI server with SSE-powered real-time UI showing agent activity, experiment history, and scores across all projects

## Documentation

| Doc | What's in it |
|-----|-------------|
| [Setup Guide](docs/setup.md) | Full installation, authentication, environment setup |
| [Architecture](docs/architecture.md) | Three-layer system, agent roles, state machine, data flow |
| [Eval System](docs/eval.md) | Hygiene/growth/project tiers, scoring, guards, precheck |
| [Configuration](docs/configuration.md) | `factory.md` reference — all sections and options |
| [ACE Self-Improvement](docs/ace.md) | How the factory evolves its own agent playbooks |
| [Contributing](docs/contributing.md) | Dev setup, code style, testing, PR workflow |

## Development

```bash
uv sync --all-groups              # Install all deps including dev
uv run pytest -v                  # 1320 tests
uv run ruff check .               # Lint
uv run mypy factory/              # Type check
```

## License

[MIT](LICENSE) — Akash Srivastava
