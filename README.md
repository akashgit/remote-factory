# The Factory

**Autonomous multi-agent software evolution.** Point it at any codebase — or just describe what you want to build — and a team of AI agents will continuously discover, improve, and evolve it.

The Factory wraps [Claude Code](https://docs.anthropic.com/en/docs/claude-code) with a structured experiment loop: a CEO agent orchestrates six specialists (Researcher, Strategist, Builder, Reviewer, Evaluator, Archivist), each running as an independent subprocess. Every change is a hypothesis — measured before and after, kept or reverted based on eval scores, and archived for institutional memory.

```
  ┏━╸┏━┓┏━╸╺┳╸┏━┓┏━┓╻ ╻
  ┣╸ ┣━┫┃   ┃ ┃ ┃┣┳┛┗┳┛
  ╹  ╹ ╹┗━╸ ╹ ┗━┛╹┗╸ ╹
```

## How It Works

```
You → factory ceo ~/my-project
         │
         ▼
   ┌─────────────┐     ┌────────────┐     ┌─────────┐
   │  Researcher  │────▶│ Strategist │────▶│ Builder │
   │  observe     │     │ hypothesize│     │implement│
   └─────────────┘     └────────────┘     └─────────┘
                                                │
   ┌─────────────┐     ┌────────────┐           │
   │  Archivist   │◀────│ Evaluator  │◀──────────┘
   │  record      │     │  measure   │
   └─────────────┘     └────────────┘
         │
         ▼
   Score improved? → KEEP and merge
   Score regressed? → REVERT and learn
```

Each cycle produces a measurable, auditable experiment. The factory learns from its own decisions via [ACE playbook evolution](docs/ace.md) — successful patterns get reinforced, failed ones get suppressed.

## Quick Start

```bash
# Install
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync
uv tool install -e .
factory install

# Run on any project (interactive — you see everything, can redirect mid-run)
factory ceo ~/my-project

# Or build something new from a prompt
factory ceo --prompt "Build a CLI that converts CSV to JSON"
```

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/), [Claude Code](https://docs.anthropic.com/en/docs/claude-code), and a Claude API key (Anthropic API or Google Vertex AI). See the [full setup guide](docs/setup.md).

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

```
┌──────────────────────────────────────────────────────────┐
│  Specialist Agents (claude -p subprocesses)              │
│  Researcher · Strategist · Builder · Reviewer            │
│  Evaluator · Archivist                                   │
├──────────────────────────────────────────────────────────┤
│  CEO Agent (interactive orchestrator)                    │
│  Detects state → routes mode → spawns specialists        │
│  Makes keep/revert decisions · Ensures archival          │
├──────────────────────────────────────────────────────────┤
│  Python CLI (factory/)                                   │
│  Pure tools: eval, guard, store, discover, events        │
│  No decisions — just data and measurement                │
└──────────────────────────────────────────────────────────┘
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

| Tier | What it measures | Examples |
|------|-----------------|---------|
| **Hygiene** (6 dimensions) | Code quality basics | Tests, lint, type checking, coverage |
| **Growth** (5 dimensions) | Capability evolution | API surface area, experiment diversity, observability |
| **Project** (user-defined) | Domain-specific metrics | Benchmark accuracy, latency, win rate |

Default weight split is 50/50 hygiene/growth. When you define project-specific evals, it shifts to 30/20/50. Fully configurable via `factory.md`. See [Eval System](docs/eval.md).

## Project Configuration

Each managed project uses a `factory.md` file at its root:

```markdown
## Goal
One sentence describing what the project should achieve.

## Scope
### Modifiable
- src/**
- tests/**

## Guards
- Do not delete existing tests
- Do not modify files outside scope

## Eval
### Command
pytest --tb=short -q

### Threshold
0.8
```

The CEO auto-generates this during discovery. See [Configuration Reference](docs/configuration.md) for all options including custom eval dimensions, smoke tests, hypothesis budgets, and target branches.

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
- **Telegram notifications**: Optional push notifications for cycle completions

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
