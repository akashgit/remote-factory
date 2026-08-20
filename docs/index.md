<p align="center">
  <img src="assets/logo-light.png#only-light" alt="re:factory" width="480">
  <img src="assets/logo-dark.png#only-dark" alt="re:factory" width="480">
</p>

<p align="center" markdown>
[![CI](https://img.shields.io/github/actions/workflow/status/akashgit/remote-factory/ci.yml?style=flat-square&label=CI)](https://github.com/akashgit/remote-factory/actions/workflows/ci.yml) [![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/downloads/) [![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](https://github.com/akashgit/remote-factory/blob/main/LICENSE) [![Runner: Claude Code](https://img.shields.io/badge/runner-Claude%20Code-cc785c?style=flat-square&logo=claude&logoColor=white)](https://docs.anthropic.com/en/docs/claude-code)
</p>


**Describe what you want — re:factory designs and builds it.** Brainstorm an idea from scratch, refine a plan for an existing project, or create entirely new factory modes.

```bash
# Design — brainstorm an idea, refine it, then build
factory ceo "distributed eval runner" --mode design

# Create — build new factory modes and pipelines
factory ceo /path/to/factory --mode create --focus "PR validation pipeline"

# Build — have a fleshed-out idea? Pass the file.
factory ceo ~/ideas/weather-dashboard.md

# Improve — point it at any codebase
factory ceo ~/my-project

# Focus — build exactly one thing
factory ceo ~/my-project --focus "add WebSocket support"
```

All state is local — per-project in `.factory/` (add to `.gitignore`), global in `~/.factory/`. See [Architecture](architecture.md) for the full deep-dive.

---

## How It Works

re:factory defines every workflow as a **Pydantic graph** — a directed acyclic graph (DAG) where each node is an agent, a shell command, a gate check, or a fork/join for parallelism. The same graph definition produces **three execution modes**:

### 1. Headless Executor

`factory workflow run <name> /path` — the `WorkflowExecutor` walks the DAG deterministically, running each node in topological order with no human interaction. Used for unattended runs, CI/CD pipelines, and scripted automation.

### 2. Interactive CEO

`factory ceo /path --mode <name>` — `skill_export.py` converts the workflow graph into a SKILL.md prose playbook under `skills/workflow-*/`. At runtime, the CEO agent reads the appropriate SKILL.md and follows it step by step, orchestrating specialist agents — Researcher, Strategist, Builder, Health Checker, Code Reviewer, Adversarial Tester, Archivist, and Failure Analyst — each running as an independent [Claude Code](https://docs.anthropic.com/en/docs/claude-code) subprocess. Unlike the headless executor, the CEO can review agent outputs, redirect failing agents, and apply judgment at gate points.

### 3. Outer Loop — Evolutionary Workflow Search

`factory outer-loop` — instead of *executing* a workflow, the outer loop *evolves* workflow topologies via MAP-Elites quality-diversity search. Starting from a seed workflow, it mutates structure (adding/removing nodes, changing edges, tweaking prompts), evaluates each candidate by running a full CEO cycle, and selects for higher fitness. This is how re:factory improves its own pipelines.

### The graph is the source of truth

For example, an **email summarizer agent** might be a simple 3-node workflow: a Researcher reads the inbox → a Strategist prioritizes by urgency → a Builder drafts the summary. A **custom research agent** might fork three parallel researchers (domain, competitors, prior art) → join their findings → pass through a coverage gate → synthesize a final report. The graph structure is the same — what changes is the nodes, their prompts, and the edges between them. All three execution modes operate on the same underlying graph definition.

The two primary modes for getting started:

- **Design mode** (`--mode design`): The entry point for new ideas and existing projects alike. Researches the space, drafts a structured plan via the Strategist, iterates with you until it's right, then builds. Use this when you want to think before you code.
- **Create mode** (`--mode create`): Builds new workflow graphs themselves — new factory modes, new pipelines, new agent topologies. Point it at the factory repo and describe what mode you want. It researches existing patterns, synthesizes a workflow spec, gets your approval, then implements the full graph definition, skill export, CLI wiring, and tests.

---

## Design Mode

### Design — brainstorm before building

Design mode is the primary way to use re:factory. It researches the space, drafts a structured plan via the Strategist, and lets you iterate on it before any code is written.

**From a raw idea** — describe what you want and refine it into a buildable spec:

```bash
factory ceo "distributed eval runner" --mode design
factory ceo "Build a REST API for bookmark management" --mode design
```

**From a spec file** — for longer, more detailed descriptions, write your idea to a `.md` file and pass the path:

> **Tip:** For detailed ideas with multiple paragraphs, requirements, or research notes, use a spec file instead of a quoted string. There's no length limit on file content.

```bash
factory ceo ~/ideas/weather-dashboard.md --mode design
factory ceo ~/ideas/my-app-spec.md --mode design
```

**On an existing project** — study the backlog, eval scores, open issues, and experiment history, then discuss what to work on before executing:

```bash
factory ceo ~/factory-projects/my-app --mode design
```

**Seed the conversation with a topic** — use `--focus` to start the discussion around a specific area:

```bash
factory ceo ~/factory-projects/my-app --mode design --focus "auth layer"
factory ceo ~/my-app --mode design --focus 42                       # GitHub issue
factory ceo ~/my-app --mode design --focus "owner/repo#42"          # Issue shorthand
```

---

## Create Your Own Factory/Mode

Create mode lets you build new factory modes — new workflows, new pipelines, new factories. Pass a description via `--focus` to tell the CEO what mode to create. It's fully interactive — the CEO researches existing patterns, synthesizes a workflow spec, gets your approval, then implements everything: workflow definition, SKILL.md, CLI wiring, and tests.

```bash
factory ceo /path/to/factory --mode create --focus "a mode that validates PRs with multi-stage checks"
```

To update an existing mode, prefix `--focus` with the mode name and a colon. The name before the colon is matched against registered workflows — if it matches, the CEO enters update mode instead of creating a new one:

```bash
factory ceo /path/to/factory --mode create --focus "improve: add plateau detection after 3 consecutive reverts"
factory ceo /path/to/factory --mode create --focus "build: add a code review gate after the builder"
```

Without a colon, `--focus` always creates a new mode.

The pipeline: **3 parallel researchers** (existing patterns, intent analysis, best practices) → **Strategist** synthesizes a workflow spec → **you approve** (like design mode) → **Builder** implements → **QA** verifies end-to-end → **PR**.

Point it at the factory repo itself to extend re:factory with custom pipelines.

---

## Available Workflows

Beyond the core Design and Create modes, re:factory ships with a growing set of workflows — both built-in and community-contributed. Each is a complete graph definition with its own agent topology, gates, and iteration strategy.

### Built-in Workflows

| Workflow | What it does |
|----------|-------------|
| **frontend-design** | Feature-to-UI pipeline — forks 5 design researchers in parallel, joins findings, then runs design audit → spec → build → render → deep QA |
| **parallel-improve** | Forks N hypotheses into isolated git worktrees, runs experiments concurrently, and selects the best result |
| **deep-research** | Decomposes a topic into research directions, executes each with internal iteration, and checks coverage |
| **deep-qa** | Multi-stage quality assurance — health check, code review, and adversarial testing in parallel |
| **study** | Graph-powered codebase analysis — builds a dependency graph, explores it, and produces a combined study report |

### Community-Contributed Benchmarks

These benchmark workflows live in `factory/workflow/contributed/` and follow a standard 4-node pipeline pattern (study → solver → gate → merge). See [Contributing Benchmarks](contributing-benchmarks.md) for how to add your own.

| Workflow | What it solves |
|----------|---------------|
| **swebench** | GitHub issues from the SWE-bench dataset in containerized evaluation |
| **featurebench** | New feature implementations in Python codebases with explicit interface specs |
| **legacybench** | Bugs in legacy code — COBOL, Fortran, C, Java 7, Assembly |
| **devopsgym** | Build/configuration tasks — Maven, Gradle, Go modules, Make, Docker, CI/CD |
| **terminalbench** | Real-world terminal engineering tasks — compiling legacy software, scientific computing, system configuration |
| **programbench** | Adversarial discovery verification with builder → reviewer loops |
| **tomswe** | Preference-aware coding tasks with embedded user profiles (Theory of Mind) |
| **salitrap** | Commonsense reasoning — identifying salience traps in scenarios with numerical distractors |

Run any workflow with `factory ceo /path --mode <name>` or use Create mode to build your own.

---

## Quick Start

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/#installation), and [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (installed and authenticated).

### Quick Install

```bash
uv tool install git+https://github.com/akashgit/remote-factory.git
```

### Development Install

```bash
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync
uv tool install -e .
```

Then start with one of the two main workflows:

```bash
# Design — brainstorm an idea, refine it, then build
factory ceo "my idea" --mode design

# Improve an existing project — use design mode with a focus area
factory ceo /path/to/project --mode design --focus "issue # or area to improve"
```

See the [full setup guide](setup.md) for authentication, environment variables, and justification for why we install globally.

---

## Self-Evolving Agents

| I want to… | Command |
|---|---|
| **Start from a raw idea** | `factory ceo "my idea" --mode design` |
| **Improve an existing project** | `factory ceo /path/to/project --mode design --focus "issue # or area to improve"` |
| **Create a new factory mode** | `factory ceo /path/to/factory --mode create --focus "mode description"` |
| **Update an existing mode** | `factory ceo /path/to/factory --mode create --focus "improve: add plateau detection"` |

re:factory doesn't just improve your project — it improves *itself*. Every keep/revert decision becomes training data for the next cycle.

This is powered by **ACE (Autonomous Context Engineering)** — inspired by Anthropic's work on [context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — a Reflect → Curate → Inject loop that evolves agent playbooks from real experiment outcomes.

Each agent accumulates behavioral rules — DOs and DON'Ts — with evidence counters. Rules that correlate with kept experiments get reinforced. Rules that correlate with reverts get pruned.

See [ACE Playbook Evolution](ace.md) for the playbook mechanics.

---

## Eval System

Every change is measured by a composite score across three tiers:

| Tier | What it measures | Examples |
|------|-----------------|---------|
| **Hygiene** (6 dimensions) | Code quality basics | Tests, lint, type checking, coverage, guards, config |
| **Growth** (5 dimensions) | Capability evolution | API surface area, experiment diversity, observability, research effectiveness |
| **Project** (user-defined) | Domain-specific metrics | Benchmark accuracy, latency, win rate |

On first run, `factory discover` auto-detects your project's language and framework to generate the eval profile. The weighted composite of all dimensions determines whether each experiment is kept or reverted. See [Eval System](eval.md) for scoring details, weights, and guards.

---

## Outer Loop — Evolve Workflow Topologies

```bash
factory outer-loop calibrate ~/my-factory \
  --benchmark featurebench \
  --population-size 3 \
  --project-dir /path/to/benchmark-instance \
  --test-command "pytest tests/ -v"

factory ceo ~/my-factory --mode outer-loop --headless
```

The outer loop evolves the factory's own workflow DAGs against benchmarks. Starting from a simple seed (e.g. builder-only), it mutates workflow structure (adding nodes, changing edges, tweaking prompts), evaluates each candidate on a real benchmark instance, and selects for higher test pass rates. See the [Outer Loop guide](outer-loop.md) for full architecture and CLI reference.

---

## Built with re:factory

| Project | What it does |
|---------|-------------|
| **SWE-bench solver** | Autonomous agent that resolves GitHub issues from the SWE-bench dataset, iteratively improved via failure analysis |
| **HMMT math solver** | Multi-agent team (Explorer, Theorist, Computationalist, Critic, Synthesizer) that solved HMMT Feb 2025 Combinatorics Problem 7 |
| **Text/Sketch → CAD** | Converts natural language and hand-drawn sketches into executable CadQuery code for 3D model generation |
| **HLS design space explorer** | Per-function AI agents explore HLS pragma/code variants in parallel, an ILP solver finds the optimal combination, then global expert agents apply cross-function optimizations |
| **Pluck** | iOS app that extracts structured data from screenshots, links, and shared content using on-device AI |
| **Group chat digest** | Turns iMessage group chats into weekly family newsletters with AI-curated highlights and photo selection |
| **re:factory itself** | re:factory runs on itself — its own agent playbooks are evolved from its own experiment outcomes |

Built something with re:factory? [Open a PR](https://github.com/akashgit/remote-factory/pulls) to add it here.

---

## CLI Quick Reference

```bash
# Design — brainstorm and build
factory ceo "idea" --mode design                              # Design from a raw idea
factory ceo ~/ideas/spec.md --mode design                     # Design from a spec file
factory ceo <path> --mode design                              # Design improvements for existing project
factory ceo <path> --mode design --focus "topic"              # Seed with a specific topic

# Create — extend the factory
factory ceo <path> --mode create --focus "description"        # Create a new factory mode
factory ceo <path> --mode create --focus "mode: change"       # Update an existing mode
```

See `factory --help` for the complete list.

---

## Runners

re:factory supports multiple CLI backends. Default is Claude Code — switch with `--runner` or `FACTORY_RUNNER`:

```bash
# Direct
CODEX_API_KEY="..." factory ceo /path --runner codex
BOBSHELL_API_KEY="..." factory ceo /path --runner bob

# Via config.toml profile (persistent)
factory ceo /path --profile codex
```

Configure profiles in `~/.factory/config.toml`:

```toml
[credentials.codex]
FACTORY_RUNNER = "codex"
CODEX_API_KEY = "..."

[credentials.bob]
FACTORY_RUNNER = "bob"
BOBSHELL_API_KEY = "..."
```

Run `factory config show` to see resolved config, or `factory config edit` to open the file. See [Setup Guide](setup.md) for full details.

---

## LLM Tracing (LangFuse)

LangFuse provides LLM observability and tracing — track agent invocations, token usage, and execution flow across all factory runs.

### Quick Start

```bash
# Start LangFuse services
scripts/langfuse-setup start

# Set the env vars the factory needs
export LANGFUSE_HOST=http://localhost:3000
export LANGFUSE_BASE_URL=http://localhost:3000
export LANGFUSE_PUBLIC_KEY=pk-lf-dev-local-key
export LANGFUSE_SECRET_KEY=sk-lf-dev-local-key
export TELEMETRY_PLATFORM=langfuse
```

The dev credentials above match the docker-compose setup. Add them to your `~/.bashrc` or `~/.zshrc` to persist across sessions.

### Viewing Traces

1. Start LangFuse: `scripts/langfuse-setup start`
2. Run the factory: `factory ceo /path/to/project`
3. Open `http://localhost:3000` in your browser
4. Login: `dev@localhost.local` / `devpassword123`

### CLI Commands

```bash
scripts/langfuse-setup start    # Start LangFuse services
scripts/langfuse-setup stop     # Stop services
scripts/langfuse-setup status   # Show status and credentials
```

### Requirements

- **Docker** or **Podman** — any of `docker compose`, `docker-compose`, or `podman-compose` works

### Disabling Tracing

To disable tracing without stopping LangFuse:
```bash
export LANGFUSE_TRACING_ENABLED=false
```

For LLM connection setup, trace structure details, and troubleshooting, see [`infra/langfuse/README.md`](https://github.com/akashgit/remote-factory/blob/main/infra/langfuse/README.md).

---

## Install as a Claude Code Plugin

re:factory is also distributed as a fully-bundled [Claude Code plugin](https://docs.claude.com/en/docs/claude-code/plugins) — agents, skills, and slash commands packaged together. A GitHub Actions workflow rebuilds the `plugins` branch of this repo on every push to `main`, so it always tracks the latest generated artifacts.

From inside Claude Code:

```text
/plugin marketplace add akashgit/remote-factory#plugins
/plugin install factory@remote-factory
/reload-plugins
```

Once installed, the plugin exposes:

- The `/factory:implement` slash command (entry point for the multi-agent pipeline).
- Namespaced subagents — invoke with `factory:ceo`, `factory:researcher`, `factory:builder`, etc.
- The bundled skills under `.agents/skills/` (e.g. `pipeline-subagents`, `implement`).

The plugin still shells out to the `factory` CLI for the heavy lifting, so you'll need the `factory` package installed globally as described in [Quick Start](#quick-start).

To update later: `/plugin marketplace update remote-factory`. To remove: `/plugin uninstall factory@remote-factory`.

---

## Plugin Agents

If you'd rather skip the marketplace and just register the specialist agents as standalone Claude Code (or Codex) subagents, use the built-in installer:

```bash
factory install                   # Install all 9 agents to ~/.claude/agents/
factory install --runner codex    # Or install Codex TOML agents to ~/.codex/agents/
claude --agent factory-ceo "improve this project"
claude --agent factory-researcher "study the auth system"
```

This path only ships the agent prompts (no skills, no slash commands) and is independent of the plugin marketplace install above.

---

## Verified Skill Generation

Workflow graphs (Pydantic definitions) are converted to SKILL.md prose files that the CEO follows at runtime. This conversion goes through a verified pipeline to prevent information loss:

```
Workflow (Pydantic) → templatize → review agent → guard → split
                         │              │           │        │
                    {{slot::default}}   opus    structural   SKILL.md +
                    + annotations     refines    diff check  annotations.yaml
```

The pipeline produces two artifacts per workflow:
- **SKILL.md** — clean prose the CEO reads at runtime
- **SKILL.annotations.yaml** — structured metadata per node for programmatic verification

Regenerate all skills after changing workflow definitions:

```bash
factory workflow export-skills
```

A regression test (`test_annotations_match_source`) runs in CI to catch drift between workflow definitions and exported skills.

---

## Documentation

| Doc | What's in it |
|-----|-------------|
| [Setup Guide](setup.md) | Installation, authentication, environment variables |
| [Getting Started](getting-started.md) | Lifecycle walkthrough, research mode details, factory.md config |
| [Architecture](architecture.md) | Three-layer system, agent roles, state machine, data flow |
| [Eval System](eval.md) | Hygiene/growth/project tiers, scoring, guards, precheck |
| [Configuration](configuration.md) | `factory.md` reference — all sections and options |
| [ACE Self-Improvement](ace.md) | How re:factory evolves its own agent playbooks |
| [Contributing](contributing.md) | Dev setup, code style, testing, PR workflow |
| [Contributing Benchmarks](contributing-benchmarks.md) | How to add new benchmarks: workflow structure, Harbor setup, CI integration |

## Development

```bash
uv sync --all-groups              # Install all deps including dev
pytest -v                         # Full test suite
ruff check .                      # Lint
mypy factory/                     # Type check
```

## License

[MIT](https://github.com/akashgit/remote-factory/blob/main/LICENSE) — Akash Srivastava
