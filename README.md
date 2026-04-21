# Remote Factory

Domain-agnostic multi-agent software evolution loop. Give it any project — a directory, a GitHub URL, an idea from your Obsidian vault, or just a prompt — and it will discover what to measure, then continuously improve it with autonomous agents.

```
  ┏━╸┏━┓┏━╸╺┳╸┏━┓┏━┓╻ ╻
  ┣╸ ┣━┫┃   ┃ ┃ ┃┣┳┛┗┳┛
  ╹  ╹ ╹┗━╸ ╹ ┗━┛╹┗╸ ╹
  Multi-Agent Software Evolution
```

## Quick Start

```bash
# Install
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync
uv tool install -e .          # puts `factory` on your PATH
factory install               # registers CEO as a Claude Code agent

# Run on anything (interactive — you can see and interact with the CEO)
factory ceo ~/my-project                              # existing project
factory ceo https://github.com/user/repo              # GitHub repo
factory ceo "Locals Know"                             # Obsidian vault idea
factory ceo --prompt "Build a CLI that converts CSV to JSON"  # raw prompt
factory ceo ~/my-project --focus "eval reliability"   # narrow focus

# Or from within any Claude Code session
claude --agent factory-ceo                            # launches CEO interactively
```

## Installation

### 1. Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | System or [pyenv](https://github.com/pyenv/pyenv) |
| [uv](https://docs.astral.sh/uv/) | Latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Latest | `npm install -g @anthropic-ai/claude-code` |
| Google Cloud SDK | Latest | [Install guide](https://cloud.google.com/sdk/docs/install) |
| tmux | Any | `brew install tmux` (optional, for long-running sessions) |
| Node.js | 18+ | Required for Claude Code and MCP servers |

### 2. Clone and Install

```bash
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync

# Install the `factory` command globally (recommended)
uv tool install -e .
```

Verify the CLI:

```bash
factory --help
```

If you skip `uv tool install`, prefix all commands with `uv run python -m factory` instead of `factory`.

### 3. Claude API Access (Vertex AI)

The factory uses Claude via Google Vertex AI. Set up authentication:

```bash
# Authenticate with Google Cloud
gcloud auth login
gcloud auth application-default login
gcloud config set project <your-gcp-project-id>
```

Add to your `~/.zshrc` or `~/.bashrc`:

```bash
export CLAUDE_CODE_USE_VERTEX=1
export CLOUD_ML_REGION=us-east5
export ANTHROPIC_VERTEX_PROJECT_ID=<your-gcp-project-id>
```

If using the Anthropic API directly instead of Vertex, set `ANTHROPIC_API_KEY` and omit the Vertex variables.

### 4. Install the CEO Agent

Register the Factory CEO as a Claude Code agent so you can launch it from anywhere:

```bash
factory install
```

This writes `~/.claude/agents/factory-ceo.md`. Now you can:

```bash
# From any terminal — launches interactive CEO session
factory ceo ~/my-project

# From within any Claude Code session
claude --agent factory-ceo
# Or just ask Claude: "use the factory-ceo agent"
```

Re-run `factory install` after updating the factory to pick up CEO prompt changes.

### 5. Claude Code Agents

The factory uses two layers of agents:

| Agent | How it runs | When |
|-------|-------------|------|
| **CEO** (`factory-ceo`) | Interactive foreground session (`--append-system-prompt`) | User launches via `factory ceo` or `claude --agent factory-ceo` |
| **Specialists** (researcher, strategist, builder, reviewer, evaluator, archivist) | Non-interactive subprocesses (`claude -p`) | CEO spawns them via `factory agent <role>` |

Specialist prompts live in `factory/agents/prompts/`. Project-specific overrides go in `<project>/.factory/agents/<role>.md`.

### 6. MCP Servers

The factory uses MCP (Model Context Protocol) servers for extended capabilities. Configuration is in `.mcp.json` at the project root:

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest"]
    }
  }
}
```

**Playwright MCP** enables browser automation for UI testing and visual verification. Claude Code auto-discovers `.mcp.json` — no manual setup required.

To add MCP servers to a target project, create a `.mcp.json` in its root. The factory's Builder agent will use available MCP tools when working on that project.

### 7. Optional: Obsidian Vault

The factory archives experiment history and cross-project knowledge to an Obsidian vault:

```bash
factory vault-init
```

This creates `~/obsidian-vaults/factory/` with the expected directory structure. Configure a custom path:

```bash
export OBSIDIAN_VAULT_PATH=~/my-vault
```

### 8. Optional: Telegram Notifications

```bash
export TELEGRAM_BOT_TOKEN=<token>
export TELEGRAM_CHAT_ID=<chat-id>
```

## Running on a New Machine

Complete setup from scratch:

```bash
# 1. Install tooling
curl -LsSf https://astral.sh/uv/install.sh | sh         # uv
npm install -g @anthropic-ai/claude-code                  # Claude Code

# 2. Clone and install
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync
uv tool install -e .           # puts `factory` on your PATH

# 3. Authenticate with Google Cloud
gcloud auth login
gcloud auth application-default login
gcloud config set project <your-gcp-project-id>

# 4. Set environment variables (add to ~/.zshrc)
echo 'export CLAUDE_CODE_USE_VERTEX=1' >> ~/.zshrc
echo 'export CLOUD_ML_REGION=us-east5' >> ~/.zshrc
echo 'export ANTHROPIC_VERTEX_PROJECT_ID=<your-gcp-project-id>' >> ~/.zshrc
source ~/.zshrc

# 5. Install CLI + CEO agent
uv tool install -e .
factory install                # registers CEO as Claude Code agent

# 6. Initialize vault (optional)
factory vault-init

# 7. Verify
factory --help
factory detect /path/to/any/project
```

## Usage

### Universal Input

`factory run` and `factory ceo` accept any input:

```bash
# Existing directory
factory run ~/cursor-projects/my-app

# GitHub URL (clones automatically)
factory run https://github.com/user/repo

# Obsidian vault idea (fuzzy-matches against Ideas/ folder)
factory run "Locals Know"
factory run "kalshi"

# Raw prompt (creates a new project)
factory run "Build a prediction market bot with the Kalshi API"
```

When matching a vault idea, the factory reads the full idea note, creates a repo in `~/cursor-projects/`, and passes the idea content as the project specification to the CEO agent.

### Modes

```bash
factory run ~/my-project                    # improve (default) — full improvement loop
factory run ~/my-project --mode discover    # discover — introspect + generate evals only
factory run ~/my-project --mode meta        # meta — improve + ACE playbook evolution
factory run ~/my-project --focus "auth"     # narrow improvement to a specific area
```

### CEO Agent

The CEO is the dedicated orchestrator. By default it runs **interactively** — you see everything it does and can ask questions or redirect mid-run:

```bash
factory ceo ~/my-project                    # interactive (default)
factory ceo ~/my-project --mode meta        # improve + ACE playbook evolution
factory ceo ~/my-project --focus "dashboard" # narrow focus to specific area
factory ceo ~/my-project --headless         # non-interactive (for scripting)
factory ceo --prompt "Build a weather CLI"  # build from a prompt
```

From within Claude Code:

```bash
claude --agent factory-ceo                  # interactive CEO session
```

### Continuous Operation

```bash
# Heartbeat loop (runs every 30 min)
factory run ~/my-project --loop --interval 1800

# With max cycles
factory run ~/my-project --loop --max-cycles 10

# In a detached tmux session (survives SSH disconnects)
factory tmux ~/my-project --loop
factory tmux ~/my-project --loop --attach   # attach immediately

# Manage tmux sessions
factory tmux-ls                             # list running sessions
factory tmux-stop                           # stop all sessions
factory tmux-stop --session factory-my-app  # stop specific session
```

### Live Dashboard

Real-time web UI showing agent activity, experiment history, and project scores across all factory-managed projects:

```bash
factory dashboard                                    # default: port 8420
factory dashboard --port 9000 --host 127.0.0.1       # custom binding
factory dashboard --projects-dir ~/my-projects       # custom projects directory
```

The dashboard uses SSE (Server-Sent Events) to stream live events from all `.factory/events.jsonl` files. Designed to run on an always-on machine.

### Invoking Specialist Agents Directly

```bash
factory agent researcher --task "Analyze test coverage gaps" --project ~/my-app
factory agent builder --task "Add input validation to /api/users" --project ~/my-app
factory agent evaluator --task "Run evals and report scores" --project ~/my-app
```

### Other Commands

```bash
# Project management
factory install                             # register CEO as Claude Code agent
factory detect <path>                       # print project state
factory discover <path>                     # introspect + generate eval profile
factory init <path>                         # create .factory/ from factory.md
factory status <path>                       # print project status summary
factory export <path>                       # export full project snapshot as JSON

# Evaluation & guards
factory eval <path>                         # run evals, print composite score
factory guard <path> --baseline <sha>       # check guard rules

# Experiment lifecycle
factory begin <path> --hypothesis "..."     # start experiment
factory finalize <path> --id N --verdict keep  # finalize experiment
factory history <path>                      # print experiment history
factory diff <path> --exp1 N --exp2 M       # compare two experiments side-by-side
factory explain <path> --exp N              # explain experiment with FEEC analysis

# Analysis & knowledge
factory study <path>                        # analyze code + write observations
factory insights <path>                     # cross-project analysis
factory ace <path>                          # run ACE self-improvement on playbooks
factory digest                              # summarize recent activity
factory archive <path>                      # write to Obsidian vault
factory notify <path>                       # send Telegram digest

# Crash resilience
factory checkpoint <path>                   # save CEO checkpoint for resume
factory resume <path>                       # load checkpoint and resume
```

## Architecture

Three-layer system with a CEO agent as the orchestrator:

```
┌─────────────────────────────────────────────────────────┐
│  Layer 3: Specialist Agents                             │
│  ┌──────────┐ ┌───────────┐ ┌─────────┐ ┌──────────┐   │
│  │Researcher│ │Strategist │ │ Builder │ │ Reviewer │   │
│  └──────────┘ └───────────┘ └─────────┘ └──────────┘   │
│  ┌──────────┐ ┌───────────┐                             │
│  │Evaluator │ │ Archivist │  <- claude -p subprocesses  │
│  └──────────┘ └───────────┘                             │
├─────────────────────────────────────────────────────────┤
│  Layer 2: CEO Agent  (factory/agents/prompts/ceo.md)    │
│  State machine: Discover → Build → Improve → Archive    │
│  Spawns specialists via: factory agent <role> --task ... │
├─────────────────────────────────────────────────────────┤
│  Layer 1: Python CLI  (factory/)                        │
│  Pure tools: eval, guard, store, discover, events       │
│  No decisions — just data                               │
└─────────────────────────────────────────────────────────┘
```

### Agents

| Agent | Role | Prompt |
|-------|------|--------|
| **CEO** | Orchestrator — state machine, keep/revert decisions, mandatory archival | `factory/agents/prompts/ceo.md` |
| **Researcher** | Observe code, find gaps, search for best practices | `factory/agents/prompts/researcher.md` |
| **Strategist** | Generate ranked hypotheses using FEEC priority | `factory/agents/prompts/strategist.md` |
| **Builder** | Implement a single change, open PR | `factory/agents/prompts/builder.md` |
| **Reviewer** | Guard rules + code review | `factory/agents/prompts/reviewer.md` |
| **Evaluator** | Run evals, compare before/after scores | `factory/agents/prompts/evaluator.md` |
| **Archivist** | Write learnings to Obsidian vault | `factory/agents/prompts/archivist.md` |

Agent prompts are resolved via two-tier lookup: project-specific override (`<project>/.factory/agents/<role>.md`) takes priority over factory default. Evolved playbooks from ACE are auto-injected.

### State Machine

The CEO detects project state and routes to the appropriate mode:

| State | Condition | Mode |
|-------|-----------|------|
| `no_repo` | No git repo | Build — scaffold from spec |
| `incomplete` | Repo exists, missing structure | Build — complete scaffold |
| `no_factory` | Code exists, no `.factory/` | Discover — introspect + generate evals |
| `evals_pending_review` | Evals generated, not reviewed | Review — human approval gate |
| `has_factory` | Everything initialized | Improve — run experiment loop |

### Eval System

Dual-axis composite score — hygiene (50%) + growth (50%):

**Hygiene** (6 mandatory dimensions, 50%): tests, lint, type_check, coverage, guard_patterns, config_parser.

**Growth** (5 mandatory dimensions, 50%): capability_surface, experiment_diversity, observability, research_grounding, factory_effectiveness.

### FEEC Priority

Hypotheses ranked: **Fix** (broken things) > **Exploit** (improve working things) > **Explore** (new capabilities) > **Combine** (cross-cutting improvements). Stuck detection after 3+ consecutive same-category reverts.

### ACE Self-Improvement

The factory evolves its own agent playbooks via the ACE (Autonomous Context Engineering) loop:

1. **Reflect** — Analyze experiment outcomes across all projects, generate candidate playbook bullets for all 7 agent roles
2. **Curate** — Merge candidates with existing playbooks, prune duplicates, cap size
3. **Inject** — Auto-inject evolved playbooks into agent prompts at runtime

Run manually: `factory ace <path>` or via CEO: `factory run <path> --mode meta`.

### Observability

**Events**: All agent invocations and cycle transitions are logged to `.factory/events.jsonl`. The agent runner emits `agent.started`, `agent.completed`, `agent.failed`, `agent.timeout` automatically. The heartbeat loop emits `cycle.started`, `cycle.completed`.

**Dashboard**: `factory dashboard` starts a FastAPI server with SSE-powered real-time UI.

## Project Configuration

Each managed project needs a `factory.md` at its root (the CEO auto-generates this during Discover mode):

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

### `.factory/` Directory

Generated by the factory — do not edit manually:

```
.factory/
├── config.json           # Parsed from factory.md
├── eval_profile.json     # Discovered eval dimensions
├── results.tsv           # Append-only experiment history
├── events.jsonl          # Event log (agent activity, cycle transitions)
├── experiments/
│   └── 001/              # Per-experiment artifacts
│       ├── hypothesis.md
│       ├── eval_before.json
│       ├── eval_after.json
│       ├── changes.diff
│       └── verdict.json
├── strategy/
│   ├── current.md
│   ├── observations.md
│   └── insights.md
├── reviews/              # Agent output capture + CEO review verdicts
│   ├── <role>-latest.md
│   └── ceo-verdict-<role>.md
└── agents/               # Per-project agent prompt overrides
```

## Project Structure

```
remote-factory/
├── SKILL.md                    # Claude Code skill (auto-discovered)
├── CLAUDE.md                   # Development guide for Claude Code
├── factory.md                  # Factory's own config
├── pyproject.toml              # Dependencies + entry points
├── .mcp.json                   # MCP server config (Playwright)
├── .claude/
│   └── agents/
│       └── researcher.md       # Claude Code agent definition
├── factory/
│   ├── cli.py                  # CLI entry point (argparse subcommands)
│   ├── models.py               # Pydantic v2 models
│   ├── state.py                # State machine (5 states)
│   ├── store.py                # .factory/ filesystem store
│   ├── events.py               # Event system (JSONL append-only log)
│   ├── strategy.py             # FEEC priority heuristic
│   ├── study.py                # Interaction log analysis
│   ├── insights.py             # Cross-project pattern analysis
│   ├── digest.py               # Activity summarization
│   ├── checkpoint.py           # CEO checkpoint save/load for crash resilience
│   ├── analysis.py             # Experiment comparison (diff, explain)
│   ├── agents/
│   │   ├── runner.py           # Agent subprocess spawner + event emission
│   │   ├── prompts/            # Agent role prompts (7 roles)
│   │   └── playbooks/          # ACE-evolved playbooks (auto-updated)
│   ├── ace/
│   │   ├── reflector.py        # Generate playbook candidates from experiments
│   │   ├── curator.py          # Merge + prune playbook items
│   │   ├── injector.py         # Inject playbooks into prompts at runtime
│   │   └── models.py           # Playbook data models
│   ├── dashboard/
│   │   ├── app.py              # FastAPI server + SSE
│   │   └── static/index.html   # Live dashboard UI (sparklines, radar, charts)
│   ├── discovery/
│   │   ├── introspect.py       # Project language/framework detection
│   │   ├── profile.py          # Build eval profile
│   │   └── generate.py         # Generate eval/score.py
│   ├── eval/
│   │   ├── runner.py           # Run evals, merge scores (50/50 hygiene/growth)
│   │   ├── hygiene.py          # 6 mandatory hygiene dimensions
│   │   ├── growth.py           # 5 universal growth dimensions
│   │   ├── scorer.py           # Composite score computation
│   │   └── guards.py           # Guard rule enforcement
│   ├── obsidian/
│   │   ├── notes.py            # Vault integration
│   │   └── templates.py        # Note templates for archivist
│   └── notify/
│       └── telegram.py         # Telegram notifications
└── tests/                      # 701 tests
```

## Development

```bash
uv sync --all-groups         # Install all deps including dev
uv run pytest -v             # Run tests (701 passing)
uv run ruff check .          # Lint
uv run mypy factory/         # Type check
uv run pytest --cov          # Coverage (~84%)
```

### Code Style

- Python 3.11+ — use `X | Y` unions, not `Union[X, Y]`
- Snake_case everywhere, 100 char line length
- All Pydantic models use `ConfigDict(strict=True, extra="forbid")`
- Async/await by default, structured logging via `structlog`
