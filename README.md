# The Factory: A Self-Evolving Harness for Agentic Software Development

[![CI](https://github.com/akashgit/remote-factory/actions/workflows/ci.yml/badge.svg)](https://github.com/akashgit/remote-factory/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**The Factory gets better the more you use it.** It takes any project — a repo, an idea, a raw prompt — and runs a structured multi-agent loop that measures and improves it. Every experiment outcome feeds back into the agents themselves, so they learn what works for *your* codebase and get sharper over time.

It wraps [Claude Code](https://docs.anthropic.com/en/docs/claude-code) with a CEO agent that orchestrates six specialists (Researcher, Strategist, Builder, Reviewer, Evaluator, Archivist), each running as an independent subprocess. Every change is a hypothesis — scored before and after, kept only if it improves the score, and archived as institutional memory. Failed experiments aren't wasted: they teach the agents what to avoid next time.

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

Meta mode is the factory's recursive self-improvement: improve the project, then improve the agents that improved the project. Over time, agents get sharper at the specific kinds of changes that work for *your* codebase. See [ACE Self-Improvement](docs/ace.md) for details.

## Quick Start

```bash
# Install (pick one)
pip install git+https://github.com/akashgit/remote-factory.git@v0.1.1    # from release
# OR
git clone https://github.com/akashgit/remote-factory.git && cd remote-factory && uv sync && uv tool install -e .

# Register the CEO as a Claude Code agent
factory install

# Run on any project (interactive — you see everything, can redirect mid-run)
factory ceo ~/my-project

# Or build something new from a prompt
factory ceo --prompt "Build a CLI that converts CSV to JSON"
```

**Prerequisites:** Python 3.11+ and [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (installed and authenticated). The Factory spawns Claude Code as subprocesses — it doesn't call the Claude API directly. See the [full setup guide](docs/setup.md).

**Highly recommended:** Install and configure [Obsidian](https://obsidian.md/) with a factory vault (`factory vault-init`). The vault gives the Factory persistent memory across projects — experiment history, cross-project insights, and research notes all live there. Without it, the Factory still works but loses its long-term learning capability. See [Obsidian setup](docs/setup.md#optional-obsidian-vault).

## What Can It Do?

| Input | What happens |
|-------|-------------|
| `factory ceo ~/my-project` | Discovers eval dimensions, then runs improvement cycles |
| `factory ceo https://github.com/user/repo` | Clones the repo, then improves it |
| `factory ceo --prompt "Build a weather CLI"` | Scaffolds a new project from scratch |
| `factory ceo ~/my-project --focus "auth"` | Narrows improvements to a specific area |
| `factory ceo ~/my-project --mode meta` | Improves the factory's own agent playbooks |
| `factory run ~/my-project --loop` | Continuous heartbeat — runs every 30 min |

## Architecture

Three layers, strict separation of concerns:

```mermaid
graph TB
    subgraph agents ["Specialist Agents"]
        R["Researcher"] ~~~ S["Strategist"] ~~~ BU["Builder"]
        RE["Reviewer"] ~~~ EV["Evaluator"] ~~~ AR["Archivist"]
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
factory ceo <path|url|prompt>     # Launch the CEO agent
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
uv run pytest -v                  # 878 tests
uv run ruff check .               # Lint
uv run mypy factory/              # Type check
```

## License

[MIT](LICENSE) — Akash Srivastava
