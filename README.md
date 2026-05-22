# The Factory

[![CI](https://github.com/akashgit/remote-factory/actions/workflows/ci.yml/badge.svg)](https://github.com/akashgit/remote-factory/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/akashgit/remote-factory/graph/badge.svg)](https://codecov.io/gh/akashgit/remote-factory)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Describe what you want. The Factory builds it, tests it, and keeps improving it — autonomously.** A CEO agent orchestrates eight specialists — each running as an independent [Claude Code](https://docs.anthropic.com/en/docs/claude-code) subprocess.

![Architecture](docs/diagrams/architecture.svg)

All state is local — per-project in `.factory/` (add to `.gitignore`), global in `~/.factory/`. See [Architecture](docs/architecture.md) for the full deep-dive.

---

## Quick Start

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/), and [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (installed and authenticated).

```bash
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync
uv run factory ceo "Build a personal homepage with a blog"
```

Optionally install as a global CLI: `uv tool install -e .` — then bare `factory` works anywhere. This README uses `factory` throughout; prepend `uv run` if you haven't installed.

See the [full setup guide](docs/setup.md) for authentication and environment variables.

---

## What Do You Want to Do?

| I want to… | Command |
|---|---|
| **Brainstorm or explore an idea** | `factory ceo "my idea" --mode interactive` |
| **Build from a spec** | `factory ceo spec.md` |
| **Improve an existing project** | `factory ceo /path/to/project` |
| **Fix or add one thing** | `factory ceo /path --focus "add dark mode"` |
| **Target a GitHub issue** | `factory ceo /path --focus 42` |
| **Optimize a metric (research)** | `factory ceo "solver agent" --mode research` |
| **Run continuously** | `factory run /path --loop` |

---

## Interactive Workflow

Use interactive mode when you want to brainstorm before building. Start a conversation with the CEO to refine an idea, then build:

```bash
# From a raw idea — discuss and refine into a buildable spec
factory ceo "distributed task runner" --mode interactive

# From a spec file — read and discuss before building
factory ceo ~/ideas/my-app-spec.md --mode interactive
```

Interactive mode also works on existing projects. The CEO studies the backlog, eval scores, open issues, and experiment history, then discusses what to work on before executing:

```bash
factory ceo ~/factory-projects/my-app --mode interactive

# Seed the conversation with a topic
factory ceo ~/factory-projects/my-app --mode interactive --focus "auth layer"
```

---

## Build Workflow

When your spec is ready, hand it to the Factory and let it build:

```bash
factory ceo "Build a personal homepage with a blog"
factory ceo ~/ideas/spec.md
factory ceo https://github.com/user/repo
```

The pipeline: **Researcher** surveys best practices → **Strategist** creates a plan → **Builder** implements and commits → **E2E gate** confirms it runs. The input is flexible — a raw string, spec file, or GitHub URL. Override the output directory with `--dir my-site`.

After the first build, a backlog appears at `.factory/strategy/backlog.md` — deferred features that feed future improvement cycles. Manage it with `factory backlog-list`, `factory backlog-add`, and `factory backlog-remove`.

---

## Improve + Focus Workflow

Point the Factory at an existing project and it enters Improve mode automatically:

```bash
factory ceo ~/factory-projects/my-app
```

Each cycle: **observe** → **hypothesize** → **build** → **review** → **measure** → **decide** (keep or revert) → **archive**. The Strategist picks work from the backlog using FEEC priority (Fix > Exploit > Explore > Combine).

When you know exactly what you want, `--focus` pins a single target — one hypothesis, one experiment, done:

```bash
factory ceo ~/my-app --focus "add dark mode toggle"
factory ceo ~/my-app --focus 42                       # GitHub issue
factory ceo ~/my-app --focus "owner/repo#42"          # Issue shorthand
```

Other ways to steer: file GitHub issues (the Strategist reads them), add to the backlog manually, or pass a spec file with `--prompt`.

For unattended operation:

```bash
factory run ~/my-app --loop                   # Continuous, 30 min default
factory run ~/my-app --loop --interval 900    # Custom interval
factory tmux ~/my-app --loop                  # Detached tmux session
```

---

## Research Mode

Research mode optimizes a **measurable metric** against a dataset — benchmarks, model tuning, prompt optimization, solver agents. The Factory is a meta-harness: give it a research objective and it builds the evaluation harness, then iteratively improves the system under test.

```bash
factory ceo "SWE-bench solver agent" --mode research
```

The CEO collects your research target (metric, run command), mutable surfaces (files the Builder can change), and fixed surfaces (ground truth — never touched). Each cycle runs: **baseline** → **failure analysis** → **research** → **hypothesize** → **build** → **re-measure** → **keep/revert**. The metric ratchets forward — it can never go below the previous best.

| Project | Metric | What the factory optimizes |
|---------|--------|---------------------------|
| SWE-bench solver | resolve rate | Agent logic, prompts, localization strategies |
| Math reasoning | solve rate | Chain-of-thought templates, tool call patterns |
| Text/Sketch → CAD | query accuracy | Query builder, schema mapping, entity resolution |

See [Getting Started — Research Mode](docs/getting-started.md#research-mode-in-detail) for phase tables, leakage guards, and progression details.

---

## Eval System

Every change is measured by an 11-dimension composite score across three tiers: **Hygiene** (tests, lint, types, coverage), **Growth** (API surface, experiment diversity, observability), and **Project** (user-defined domain metrics). On first run, `factory discover` auto-detects your project's language and framework to generate the eval profile. See [Eval System](docs/eval.md) for scoring details, weights, and guards.

---

## Self-Evolving Agents (ACE)

The Factory improves itself. Every keep/revert decision becomes training data — **ACE (Autonomous Context Engineering)** runs a Reflect → Curate → Inject loop that evolves agent playbooks from real experiment outcomes. Rules that correlate with kept experiments get reinforced; rules from reverts get pruned. Run `factory ceo /path --mode meta` on a regular cadence. See [ACE Self-Improvement](docs/ace.md).

---

## Built with the Factory

| Project | What it does | Mode |
|---------|-------------|------|
| **SWE-bench solver** | Autonomous agent that resolves GitHub issues, improved via failure analysis | Research |
| **HMMT math solver** | Multi-agent team that solved HMMT Feb 2025 Combinatorics Problem 7 | Research |
| **Text/Sketch → CAD** | Natural language and sketches to executable CadQuery Python code for 3D models | Research |
| **HLS design space explorer** | Per-function AI agents + ILP solver for HLS optimization — 92% execution time reduction | Build |
| **Pluck** | iOS app that extracts structured data from screenshots using on-device AI | Build + Improve |
| **[SDG Hub](https://github.com/Red-Hat-AI-Innovation-Team/sdg_hub)** | Agent-maintained open-source framework for synthetic data generation | Build + Improve |
| **The Factory itself** | Runs on itself in meta mode — agent playbooks are evolved from its own experiment outcomes | Meta |

Built something with the Factory? Open a PR to add it here.

---

## CLI Quick Reference

```bash
# Core workflow
factory ceo <path|url|idea>              # Build or improve
factory ceo <path> --mode interactive    # Discuss, then execute
factory ceo <path> --focus "..."         # One target, one experiment
factory run <path> --loop                # Continuous heartbeat
factory tmux <path> --loop               # In detached tmux session

# Agents & analysis
factory agent <role> --task "..." --project <path>
factory eval <path>                      # Run evals
factory precheck <path>                  # Hard precheck gate
factory study <path>                     # Analyze code
factory diff <path> --exp1 N --exp2 M    # Compare experiments
factory history <path>                   # Experiment history

# Backlog
factory backlog-list <path>
factory backlog-add <path> "..."
factory backlog-remove <path> "..."

# Operations
factory dashboard                        # Live web dashboard on :8420
factory discover <path>                  # Auto-detect eval profile
factory config show                      # Show resolved config
factory tmux-ls                          # List active tmux sessions
factory tmux-stop --path <path>          # Stop a tmux session
```

See `factory --help` for the complete list. The factory supports crash-resilient resume — the CEO reconstructs context from the event log and `.factory/` state on restart.

---

## Plugin Agents

Every factory agent is available as a standalone Claude Code subagent:

```bash
factory install                          # Install all 9 agents to ~/.claude/agents/
claude --agent factory-ceo "improve this project"
claude --agent factory-researcher "study the auth system"
```

---

## Documentation

| Doc | What's in it |
|-----|-------------|
| [Setup Guide](docs/setup.md) | Installation, authentication, environment variables |
| [Getting Started](docs/getting-started.md) | Lifecycle walkthrough, research mode details, factory.md config |
| [Architecture](docs/architecture.md) | Three-layer system, agent roles, state machine, data flow |
| [Eval System](docs/eval.md) | Hygiene/growth/project tiers, scoring, guards, precheck |
| [Configuration](docs/configuration.md) | `factory.md` reference — all sections and options |
| [ACE Self-Improvement](docs/ace.md) | How the factory evolves its own agent playbooks |
| [Contributing](docs/contributing.md) | Dev setup, code style, testing, PR workflow |

## Development

```bash
uv sync --all-groups              # Install all deps including dev
uv run pytest -v                  # Full test suite
uv run ruff check .               # Lint
uv run mypy factory/              # Type check
```

## License

[MIT](LICENSE) — Akash Srivastava
