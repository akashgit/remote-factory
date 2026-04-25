---
hide:
  - navigation
---

# The Factory

**A self-evolving harness for agentic software development — it gets better the more you use it.**

The Factory takes any project — a repo, an idea, a raw prompt — and runs a structured multi-agent loop that measures and improves it. Every experiment outcome feeds back into the agents themselves, so they learn what works for *your* codebase and get sharper over time.

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

## Quick Start

```bash
# Install
pip install git+https://github.com/akashgit/remote-factory.git@v0.1.1

# Register the CEO as a Claude Code agent
factory install

# Run on any project
factory ceo ~/my-project

# Or build something new from a prompt
factory ceo --prompt "Build a CLI that converts CSV to JSON"
```

**Prerequisites:** Python 3.11+ and [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (installed and authenticated).

**Highly recommended:** Install [Obsidian](https://obsidian.md/) and run `factory vault-init`. The vault gives the Factory persistent memory — experiment history, cross-project insights, and research notes. Without it, the Factory still works but loses its long-term learning capability.

## Self-Evolving Agents

The factory doesn't just improve your project — it improves *itself*. Every keep/revert decision becomes training data for the next cycle.

This is powered by **ACE (Autonomous Context Engineering)** — inspired by Anthropic's work on [context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — a Reflect → Curate → Inject loop that evolves agent playbooks from real experiment outcomes.

```mermaid
graph LR
    A["Experiment Outcomes<br><i>kept or reverted</i>"] -->|Reflect| B["Generate<br>candidate bullets"]
    B -->|Curate| C["Merge & prune<br>playbooks"]
    C -->|Inject| D["Agent Prompts<br><i>auto-appended</i>"]
    D -.->|"next cycle"| A

    style A fill:#fff3e0,stroke:#ff8f00
    style D fill:#e8eaf6,stroke:#5c6bc0
```

Each agent accumulates behavioral rules — DOs and DON'Ts — with evidence counters. Rules that correlate with kept experiments get reinforced. Rules that correlate with reverts get pruned.

```bash
# Run a full improvement cycle, then evolve all agent playbooks
factory ceo ~/my-project --mode meta
```

See [ACE Self-Improvement](ace.md) for details.

## Architecture

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

## The Eval System

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

| Tier | What it measures | Examples |
|------|-----------------|---------|
| **Hygiene** (6 dimensions) | Code quality basics | Tests, lint, type checking, coverage |
| **Growth** (5 dimensions) | Capability evolution | API surface area, experiment diversity, observability |
| **Project** (user-defined) | Domain-specific metrics | Benchmark accuracy, latency, win rate |

## What Can It Do?

| Input | What happens |
|-------|-------------|
| `factory ceo ~/my-project` | Discovers eval dimensions, then runs improvement cycles |
| `factory ceo https://github.com/user/repo` | Clones the repo, then improves it |
| `factory ceo --prompt "Build a weather CLI"` | Scaffolds a new project from scratch |
| `factory ceo ~/my-project --focus "auth"` | Narrows improvements to a specific area |
| `factory ceo ~/my-project --mode meta` | Improves the factory's own agent playbooks |
| `factory run ~/my-project --loop` | Continuous heartbeat — runs every 30 min |

## License

[MIT](https://github.com/akashgit/remote-factory/blob/main/LICENSE) — Akash Srivastava
