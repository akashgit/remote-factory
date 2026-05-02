# The Factory: From Idea to Evolving Software

[![CI](https://github.com/akashgit/remote-factory/actions/workflows/ci.yml/badge.svg)](https://github.com/akashgit/remote-factory/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/akashgit/remote-factory/graph/badge.svg)](https://codecov.io/gh/akashgit/remote-factory)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Runner: Claude Code](https://img.shields.io/badge/runner-Claude_Code-7c3aed)](https://docs.anthropic.com/en/docs/claude-code)
[![Runner: Bob Shell](https://img.shields.io/badge/runner-Bob_Shell-f59e0b)](https://bob.ibm.com)

**Describe what you want. The Factory builds it, tests it, and keeps improving it — autonomously.** You give it a spec file, a rough idea, or an existing codebase. The Factory researches best practices, scaffolds the project, sets up evaluation, and runs a continuous improvement loop — measuring every change and keeping only what makes things better.

---

## Quick Start

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/), and [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (installed and authenticated).

```bash
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync
```

That's it. You're ready to go. Every command runs from the **factory repo directory** — you pass the target project as an argument.

```bash
# Option A: run directly (no install needed)
uv run python -m factory ceo "Build a personal homepage with a blog"

# Option B: install as a CLI tool, then use `factory` anywhere
uv tool install -e .
factory ceo "Build a personal homepage with a blog"
```

Both forms are equivalent. This README uses `uv run python -m factory` throughout so you can copy-paste without installing first. If you've installed the CLI, just replace `uv run python -m factory` with `factory`.

**Optional: set up the vault** for persistent cross-project memory:

```bash
export FACTORY_VAULT_PATH=~/factory-vault
uv run python -m factory vault-init
```

See the [full setup guide](docs/setup.md) for authentication, MCP servers, and tmux configuration.

---

## Architecture

A CEO agent orchestrates eight specialists — Researcher, Strategist, Builder, Reviewer, Evaluator, Archivist, Distiller, and Failure Analyst — each running as an independent [Claude Code](https://docs.anthropic.com/en/docs/claude-code) subprocess.

![Architecture](docs/diagrams/architecture.svg)

The Python CLI provides measurement and storage tools (Layer 1). The CEO agent makes all workflow decisions (Layer 2). Specialist agents execute narrow tasks (Layer 3). See [Architecture](docs/architecture.md) for the full technical deep-dive.

The CEO detects your project's state and routes to the right mode automatically:

![State Machine](docs/diagrams/state-machine.svg)

---

## Walkthrough: Build → Improve → Steer

Here's a complete workflow — from a one-line idea to a continuously improving project.

### Step 1: Build from an idea

```bash
uv run python -m factory ceo "Build a personal homepage with a blog and projects section"
```

The Factory creates a project directory at `~/factory-projects/build-a-personal-homepage-...`, initializes a git repo, and launches Build mode. Here's what happens:

1. The **Researcher** surveys similar projects, tech stacks, and architecture patterns
2. The **Strategist** creates a phased implementation plan (scaffold first, then features)
3. The **Builder** implements each phase, committing code and opening PRs
4. An **E2E verification gate** confirms the project actually runs end-to-end

The input is flexible — a raw string, a spec file (`~/ideas/spec.md`), or a GitHub URL (`https://github.com/user/repo`) all work.

### Step 2: The backlog appears

After the first build, the Factory creates a backlog — `.factory/strategy/backlog.md`. This is a work queue that feeds all future improvement. It contains:

- Features deferred during build (things that need API keys, external services, or manual setup)
- Everything that *could* be built was built — only human-blocked items remain

```bash
# Check what's in the backlog
uv run python -m factory backlog-list ~/factory-projects/build-a-personal-homepage-...
```

### Step 3: Improve it

Point the factory at the project again. It detects the existing `.factory/` directory and enters Improve mode:

```bash
uv run python -m factory ceo ~/factory-projects/build-a-personal-homepage-...
```

Each improvement cycle:

1. **Observe** — the Researcher analyzes the codebase and searches for best practices
2. **Hypothesize** — the Strategist picks items from the backlog using FEEC priority (Fix > Exploit > Explore > Combine)
3. **Build** — the Builder implements one hypothesis on an experiment branch
4. **Guard** — the Reviewer checks for violations and code quality
5. **Measure** — the Evaluator scores before and after using the three-tier eval
6. **Decide** — the CEO runs a hard precheck gate, then keeps (score went up) or reverts (score went down)
7. **Record** — the Archivist records the outcome for future learning

![Experiment Lifecycle — Observe & Plan](docs/diagrams/lifecycle-observe.svg)

![Experiment Lifecycle — Execute](docs/diagrams/lifecycle-execute.svg)

### Step 4: Focus on something specific

When you know exactly what you want, `--focus` pins a single target — one hypothesis, one experiment, done:

```bash
uv run python -m factory ceo ~/factory-projects/build-a-personal-homepage-... --focus "add dark mode toggle"
```

The entire pipeline scopes to that target: the Researcher focuses its research, the Strategist generates exactly one hypothesis, and after the keep/revert decision the cycle ends.

Other ways to steer:

```bash
# Add an item to the backlog manually
uv run python -m factory backlog-add ~/factory-projects/... "add RSS feed for the blog"

# File a GitHub issue — the Strategist reads open issues
gh issue create --title "Add contact form" --body "Simple form with email notification"

# Pass a spec file to guide the next build phase
uv run python -m factory ceo ~/factory-projects/... --prompt ~/ideas/performance-spec.md
```

### Step 5: Run it continuously

For unattended operation, wrap the CEO in a heartbeat loop:

```bash
# Continuous improvement — one cycle every 30 min (default)
uv run python -m factory run ~/factory-projects/... --loop

# Custom interval
uv run python -m factory run ~/factory-projects/... --loop --interval 900

# In a detached tmux session — come back later
uv run python -m factory tmux ~/factory-projects/... --loop
uv run python -m factory tmux-ls                    # list active sessions
uv run python -m factory tmux-stop --path ~/factory-projects/...  # stop a session
```

Each cycle is a full observe → hypothesize → build → measure → decide pass. Failed experiments don't stop the loop — the factory learns from them and moves on.

---

## Walkthrough: Research Mode

Research mode is for projects where the goal is to improve a **measurable metric** against a dataset — benchmarks, model tuning, prompt optimization, solver agents.

The factory isn't just a software builder — it's a **harness creator**. Give it a research objective and it builds the evaluation harness, then iteratively improves the system under test.

### Step 1: Start with a research idea

```bash
uv run python -m factory ceo "SWE-bench solver agent" --mode research
```

Research ideation works like interactive mode but collects additional configuration:

- **Research Target** — the metric to improve, the command to run, where results are written
- **Mutable Surfaces** — files the Builder is allowed to modify (e.g., `src/agent.py`, `prompts/*.md`)
- **Fixed Surfaces** — ground truth and eval infrastructure that must never be touched (e.g., `eval/`, `data/ground_truth.json`)
- **Constraints** — additional rules (e.g., "do not use GPT-4 for cost reasons")

You review and approve the spec, then the Factory builds the project and transitions to the research loop.

### Step 2: The research loop

Each cycle runs seven phases:

| Phase | Agent | What happens |
|-------|-------|-------------|
| **R0** | Evaluator | Run `run_command`, record baseline metric |
| **R1** | Failure Analyst | Classify failures by root cause, aggregate into categories |
| **R1.5** | Researcher | Search for targeted solutions to dominant failure patterns |
| **R2** | Strategist | Generate 1-3 hypotheses targeting dominant failure modes |
| **R3** | Builder | Implement hypothesis, modifying only mutable surfaces |
| **R4** | Evaluator | Re-run `run_command`, extract new metric |
| **R5** | CEO | Keep if metric improved monotonically; revert otherwise |

Here's what progression looks like on a real project:

| Cycle | Metric | Failure Mode Targeted | Verdict | Best |
|-------|--------|----------------------|---------|------|
| 000 | 0.18 | — (baseline) | — | 0.18 |
| 001 | 0.22 | FILE_NOT_FOUND — searched wrong directories | KEEP | 0.22 |
| 002 | 0.24 | SYNTAX_ERROR — indentation bugs in patches | KEEP | 0.24 |
| 003 | 0.21 | TIMEOUT — overly broad search strategy | REVERT | 0.24 |
| 004 | 0.27 | INCOMPLETE_EDIT — partial file modifications | KEEP | 0.27 |

Cycle 003 regressed below the previous best (0.24), so it was automatically reverted. The metric ratchets forward — it can never go below the previous best.

### Step 3: Leakage guards

Research mode enforces three layers of ground truth protection to prevent the Builder from "cheating":

| Guard | What it detects |
|-------|----------------|
| **Token overlap** | Distinctive tokens from fixed surfaces appearing in hypothesis/diff text |
| **Negation hints** | Patterns like "do NOT use X" that encode answers by exclusion |
| **Specific values** | Numeric literals or quoted strings from ground truth appearing in code |

These checks run at three hard gates: strategy review, builder review, and precheck. A leakage detection triggers automatic redirect or revert.

![Decision Phase](docs/diagrams/lifecycle-decide.svg)

### The factory as a meta-harness

The factory itself is an outer optimization loop. It creates inner harnesses for any research objective:

| Project | Metric | What the factory optimizes |
|---------|--------|---------------------------|
| **SWE-bench solver** | resolve rate | Agent logic, prompts, localization strategies |
| **Math reasoning** | solve rate | Chain-of-thought templates, tool call patterns |
| **CAD query optimization** | query accuracy | Query builder, schema mapping, entity resolution |
| **Prompt engineering** | task accuracy | System prompts, few-shot examples, output parsing |

```bash
# Start a new research project from an idea
uv run python -m factory ceo "prompt optimizer for code review" --mode research

# Run the research loop on an existing project
uv run python -m factory ceo ~/my-solver --mode research

# Focus on a specific hypothesis
uv run python -m factory ceo ~/my-solver --focus "try chain-of-thought prompting"

# Continuous research loop
uv run python -m factory run ~/my-solver --mode research --loop
```

See [Getting Started — Research Mode](docs/getting-started.md#research-mode-in-detail) for the full picture.

---

## The Eval System

Every change is measured by a three-tier composite score:

![Eval System](docs/diagrams/eval-system.svg)

| Tier | What it measures | Examples |
|------|-----------------|---------|
| **Hygiene** (6 dimensions) | Code quality basics | Tests, lint, type checking, coverage |
| **Growth** (5 dimensions) | Capability evolution | API surface area, experiment diversity, observability |
| **Project** (user-defined) | Domain-specific metrics | Benchmark accuracy, latency, win rate |

Default weight split is 50/50 hygiene/growth. When you define project-specific evals, it shifts to 30/20/50. Fully configurable via `factory.md`. See [Eval System](docs/eval.md).

---

## Self-Evolving Agents

The factory doesn't just improve your project — it improves *itself*. Every keep/revert decision becomes training data for the next cycle.

This is powered by **ACE (Autonomous Context Engineering)** — a Reflect → Curate → Inject loop that evolves agent playbooks from real experiment outcomes. Each agent accumulates behavioral rules (DOs and DON'Ts) with evidence counters. Rules that correlate with kept experiments get reinforced; rules from reverts get pruned.

![Research & Self-Improvement](docs/diagrams/dataflow-research.svg)

```bash
# Run a full improvement cycle, then evolve all agent playbooks
uv run python -m factory ceo ~/my-project --mode meta
```

Run meta mode on a regular cadence — weekly is a good default. It needs at least 5 experiments across projects to produce meaningful playbook updates. See [ACE Self-Improvement](docs/ace.md) for details.

---

## Runners

The factory supports multiple CLI backends. By default it uses **Claude Code** (`claude` CLI). **Bob Shell** (`bob` CLI) is available as an alternative:

```bash
# Via environment variable
export FACTORY_RUNNER=bob

# Via CLI flag
uv run python -m factory ceo ~/my-project --runner bob
```

Bob Shell requires `BOBSHELL_API_KEY` and enforces per-cycle invocation ceilings (default: 8). Set `FACTORY_BOB_DRY_RUN=1` to test without API calls.

---

## CLI Reference

```bash
# Core workflow
factory ceo <path|url|idea>             # Launch the CEO agent
factory run <path> --loop               # Continuous heartbeat mode
factory tmux <path> --loop              # In detached tmux session

# Agents
factory agent <role> --task "..." --project <path>

# Evaluation & guards
factory eval <path>                     # Run evals, print composite score
factory precheck <path>                 # Hard precheck gate (4 checks)
factory guard <path>                    # Check guard rules

# Experiments
factory begin <path> --hypothesis "..."
factory finalize <path> --id N --verdict keep
factory history <path>
factory diff <path> --exp1 N --exp2 M
factory explain <path> --exp N

# Analysis
factory study <path>                    # Analyze code + write observations
factory insights <path>                 # Cross-project patterns
factory ace <path>                      # ACE playbook evolution

# Backlog
factory backlog-list <path>
factory backlog-add <path> "..."
factory backlog-remove <path> "..."

# Operations
factory dashboard                       # Live web dashboard on :8420
factory detect <path>                   # Print project state
factory discover <path>                 # Introspect + generate eval profile
factory export <path>                   # Full project snapshot as JSON
factory checkpoint <path>               # Save CEO state for crash recovery
factory resume <path>                   # Resume from checkpoint
```

See `factory --help` for the complete list.

---

## Documentation

| Doc | What's in it |
|-----|-------------|
| [Setup Guide](docs/setup.md) | Full installation, authentication, environment setup |
| [Getting Started](docs/getting-started.md) | Lifecycle walkthrough, research mode details, factory.md configuration |
| [Architecture](docs/architecture.md) | Three-layer system, agent roles, state machine, data flow diagrams |
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
