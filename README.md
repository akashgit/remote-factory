<p align="center">
  <img src="docs/assets/refactory_logo.png" alt="re:factory" width="480">
</p>


[![CI](https://github.com/akashgit/remote-factory/actions/workflows/ci.yml/badge.svg)](https://github.com/akashgit/remote-factory/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/akashgit/remote-factory/graph/badge.svg)](https://codecov.io/gh/akashgit/remote-factory)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Runner: Claude Code](https://img.shields.io/badge/runner-Claude_Code-7c3aed)](https://docs.anthropic.com/en/docs/claude-code)
[![Runner: Bob Shell](https://img.shields.io/badge/runner-Bob_Shell-f59e0b)](https://bob.ibm.com)
[![Runner: OpenAI Codex](https://img.shields.io/badge/runner-OpenAI_Codex-10a37f)](https://openai.com/index/codex/)
[![Docs](https://img.shields.io/badge/docs-akashgit.github.io-blue)](https://akashgit.github.io/remote-factory/)

**Describe what you want — re:factory builds it, tests it, and keeps improving it.** Design an idea from scratch or point at an existing project for continuous improvement. Runs with [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Bob Shell](https://bob.ibm.com), and [OpenAI Codex](https://openai.com/index/codex/).

All state is local — per-project in `.factory/` (add to `.gitignore`), global in `~/.factory/`. See [Architecture](docs/architecture.md) for the full deep-dive.

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

# Improve — point at an existing project for continuous improvement
factory ceo /path/to/project --mode improve --focus "issue # or whatever you want to improve or fix"

# Co-improve — if you want to iterate on the implementation plan before implementation starts for an improvement
factory ceo /path/to/project --mode design --focus "issue # or whatever you want to improve or fix"
```

See the [full setup guide](docs/setup.md) for authentication, environment variables, and justification for why we install globally.

---

## What Do You Want to Do?

| I want to… | Command |
|---|---|
| **Start from a raw idea** | `factory ceo "my idea" --mode design` |
| **Improve an existing project** | `factory ceo /path/to/project --mode improve --focus "issue number or whatever you want to improve or fix ` |
| **Co-improve an existing project** | `factory ceo /path/to/project --mode design --focus "description of whatever you want to improve or fix ` |
| **Create a new factory mode** | `factory ceo /path/to/factory --mode create --focus "mode description"` |
| **Update an existing mode** | `factory ceo /path/to/factory --mode create --focus "improve: add plateau detection"` |

---

## Design Workflow

Use design mode when you want to brainstorm before building. Start a conversation with the CEO to refine an idea, then build:

```bash
# From a raw idea — discuss and refine into a buildable spec
factory ceo "distributed task runner" --mode design

# From a spec file — read and discuss before building
factory ceo ~/ideas/my-app-spec.md --mode design
```

Design mode also works on existing projects. The CEO studies the backlog, eval scores, open issues, and experiment history, then discusses what to work on before executing:

```bash
factory ceo ~/factory-projects/my-app --mode design

# Seed the conversation with a topic
factory ceo ~/factory-projects/my-app --mode design --focus "auth layer"
```

You can also pass a spec file or URL directly — `factory ceo spec.md` — and re:factory builds without the design conversation.

---

## Improve Workflow

Improve mode is re:factory's continuous improvement loop for existing projects. Point it at a codebase and it autonomously observes the project state, generates hypotheses for improvements, builds and tests changes, and keeps or reverts each experiment based on eval scores.

```bash
factory ceo ~/factory-projects/my-app --mode improve
```

Each cycle: **observe** → **hypothesize** → **build** → **review** → **measure** → **decide** (keep or revert) → **archive**. The Strategist picks work from the backlog using FEEC priority (Fix > Exploit > Explore > Combine).

When you know exactly what you want, `--focus` pins a single target — one hypothesis, one experiment, done:

```bash
factory ceo ~/my-app --mode improve --focus "add dark mode toggle"
factory ceo ~/my-app --mode improve --focus 42                       # GitHub issue
factory ceo ~/my-app --mode improve --focus "owner/repo#42"          # Issue shorthand
```

---

## Post-Cycle Refinement

After a build or improve cycle finishes in foreground mode, the CEO stays active — it doesn't exit. Ask for changes directly:

> "Fix the typo in the header"
> "Add error handling to the upload endpoint"
> "Make the tests more thorough"

Each request runs through the full experiment pipeline: the **Refiner** scopes it → **Builder** implements → review + eval + E2E gate → keep/revert verdict. No shortcuts — every refinement is a tracked experiment with its own PR.

You can also invoke refinements directly with `--refine`:

```bash
factory ceo ~/my-app --refine "add rate limiting to the API"
```

There's no cap on refinements. Advisory warnings appear at 5 and 10 to flag context growth, but the user decides when to stop.

---

## Create New Modes

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

## Eval System

Every change is measured by an 11-dimension composite score across three tiers: **Hygiene** (tests, lint, types, coverage), **Growth** (API surface, experiment diversity, observability), and **Project** (user-defined domain metrics). On first run, `factory discover` auto-detects your project's language and framework to generate the eval profile. See [Eval System](docs/eval.md) for scoring details, weights, and guards.

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

## Built with re:factory

| Project | What it does | Mode |
|---------|-------------|------|
| **SWE-bench solver** | Autonomous agent that resolves GitHub issues, improved via failure analysis | Research |
| **HMMT math solver** | Multi-agent team that solved HMMT Feb 2025 Combinatorics Problem 7 | Research |
| **Text/Sketch → CAD** | Natural language and sketches to executable CadQuery Python code for 3D models | Research |
| **HLS design space explorer** | Per-function AI agents + ILP solver for HLS optimization — 92% execution time reduction | Build |
| **Pluck** | iOS app that extracts structured data from screenshots using on-device AI | Build + Improve |
| **[SDG Hub](https://github.com/Red-Hat-AI-Innovation-Team/sdg_hub)** | Agent-maintained open-source framework for synthetic data generation | Build + Improve |
| **[OpenSkies Airline Corpus](https://github.com/lukeinglis/OpenSkiesAirline)** | 85-document fictional airline corpus for RAG/fine-tuning evaluation with cross-document consistency validation | Design + Improve |
| **re:factory itself** | Runs on itself — continuously improved via its own experiment outcomes | Meta |

Built something with re:factory? Open a PR to add it here.

---

## CLI Quick Reference

```bash
# Core workflow
factory ceo "idea" --mode design         # Design from a raw idea
factory ceo <path> --mode improve        # Improve an existing project
factory ceo <path> --refine "..."        # Single targeted refinement
factory ceo <path> --mode create --focus "description"  # Create a new factory mode
factory ceo <path> --mode create --focus "mode: change"  # Update an existing mode
factory ceo <path> --loop                # Continuous improvement loop
factory tmux <path> --loop               # Loop in detached tmux session
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

Run `factory config show` to see resolved config, or `factory config edit` to open the file. See [Setup Guide](docs/setup.md) for full details.

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

For LLM connection setup, trace structure details, and troubleshooting, see [`infra/langfuse/README.md`](infra/langfuse/README.md).

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

## Documentation

| Doc | What's in it |
|-----|-------------|
| [Setup Guide](docs/setup.md) | Installation, authentication, environment variables |
| [Getting Started](docs/getting-started.md) | Lifecycle walkthrough, research mode details, factory.md config |
| [Architecture](docs/architecture.md) | Three-layer system, agent roles, state machine, data flow |
| [Eval System](docs/eval.md) | Hygiene/growth/project tiers, scoring, guards, precheck |
| [Configuration](docs/configuration.md) | `factory.md` reference — all sections and options |
| [ACE Self-Improvement](docs/ace.md) | How re:factory evolves its own agent playbooks |
| [Contributing](docs/contributing.md) | Dev setup, code style, testing, PR workflow |
| [Contributing Benchmarks](docs/contributing-benchmarks.md) | How to add new benchmarks: workflow structure, Harbor setup, CI integration |

## Development

```bash
uv sync --all-groups              # Install all deps including dev
uv run pytest -v                  # Full test suite
uv run ruff check .               # Lint
uv run mypy factory/              # Type check
```

## License

[MIT](LICENSE) — Akash Srivastava
