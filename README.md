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

**A self-evolving, stateful, decomposed meta-harness.** Describe what you want — re:factory builds it, tests it, and keeps improving it, autonomously. A CEO agent orchestrates eight specialists that observe, hypothesize, build, review, measure, and learn from every experiment. Runs with [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Bob Shell](https://bob.ibm.com), and [OpenAI Codex](https://openai.com/index/codex/).

All state is local — per-project in `.factory/` (add to `.gitignore`), global in `~/.factory/`. See [Architecture](docs/architecture.md) for the full deep-dive.

---

## Quick Start

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/), and [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (installed and authenticated).

```bash
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync
```

Then just run:

```bash
uv run factory
```

The **welcome wizard** launches automatically — a conversational agent that asks what you want to do, classifies your input (an idea, a file path, a GitHub URL, or a description), and presents the right command. No flags to memorize. Paste an idea and the wizard handles the rest.

You can also skip the wizard and call commands directly:

```bash
uv run factory ceo "Build a personal homepage with a blog" --mode interactive
```

See the [full setup guide](docs/setup.md) for authentication and environment variables.

---

## What Do You Want to Do?

| I want to… | Command |
|---|---|
| **Start from a raw idea** | `uv run factory ceo "my idea" --mode interactive` |
| **Build from a spec or repo** | `uv run factory ceo spec.md` |
| **Improve an existing project** | `uv run factory ceo /path/to/project` |
| **Fix or add one thing** | `uv run factory ceo /path --focus "add dark mode"` |
| **Target a GitHub issue** | `uv run factory ceo /path --focus 42` |
| **Contribute to an upstream repo** | `uv run factory ceo https://github.com/user/repo --clean-pr` |
| **Optimize a metric (research)** | `uv run factory ceo "build a harness to solve HMMT Feb 2026 C7" --mode research` |

---

## Interactive Workflow

Use interactive mode when you want to brainstorm before building. Start a conversation with the CEO to refine an idea, then build:

```bash
# From a raw idea — discuss and refine into a buildable spec
uv run factory ceo "distributed task runner" --mode interactive

# From a spec file — read and discuss before building
uv run factory ceo ~/ideas/my-app-spec.md --mode interactive
```

Interactive mode also works on existing projects. The CEO studies the backlog, eval scores, open issues, and experiment history, then discusses what to work on before executing:

```bash
uv run factory ceo ~/factory-projects/my-app --mode interactive

# Seed the conversation with a topic
uv run factory ceo ~/factory-projects/my-app --mode interactive --focus "auth layer"
```

---

## Build Workflow

When you already have a spec file, a GitHub repo, or a clear description, re:factory builds directly — no interactive step needed:

```bash
uv run factory ceo ~/ideas/spec.md
uv run factory ceo https://github.com/user/repo
uv run factory ceo "Build a personal homepage with a blog"
```

The pipeline: **Researcher** surveys best practices → **Strategist** creates a plan → **Builder** implements and commits → **E2E gate** confirms it runs. Override the output directory with `--dir my-site`. (If you start with a raw idea via `--mode interactive`, the CEO refines it into a spec first, then transitions into this same build pipeline automatically.)

After the first build, a backlog appears at `.factory/strategy/backlog.md` — deferred features that feed future improvement cycles. Manage it with `uv run factory backlog-list`, `uv run factory backlog-add`, and `uv run factory backlog-remove`.

---

## Improve + Focus Workflow

Point re:factory at an existing project and it enters Improve mode automatically:

```bash
uv run factory ceo ~/factory-projects/my-app
```

Each cycle: **observe** → **hypothesize** → **build** → **review** → **measure** → **decide** (keep or revert) → **archive**. The Strategist picks work from the backlog using FEEC priority (Fix > Exploit > Explore > Combine).

When you know exactly what you want, `--focus` pins a single target — one hypothesis, one experiment, done:

```bash
uv run factory ceo ~/my-app --focus "add dark mode toggle"
uv run factory ceo ~/my-app --focus 42                       # GitHub issue
uv run factory ceo ~/my-app --focus "owner/repo#42"          # Issue shorthand
```

Other ways to steer: file GitHub issues (the Strategist reads them), add to the backlog manually, or pass a spec file with `--prompt`.

---

## Post-Cycle Refinement

After a build or improve cycle finishes in foreground mode, the CEO stays active — it doesn't exit. Ask for changes directly:

> "Fix the typo in the header"
> "Add error handling to the upload endpoint"
> "Make the tests more thorough"

Each request runs through the full experiment pipeline: the **Refiner** scopes it → **Builder** implements → review + eval + E2E gate → keep/revert verdict. No shortcuts — every refinement is a tracked experiment with its own PR.

You can also invoke refinements directly with `--refine`:

```bash
uv run factory ceo ~/my-app --refine "add rate limiting to the API"
```

There's no cap on refinements. Advisory warnings appear at 5 and 10 to flag context growth, but the user decides when to stop.

---

## Clean PR Mode

When contributing factory-managed code to an upstream repository, you typically don't want eval scripts, benchmarks, `.factory/` data, or eval test files in the PR. Clean PR Mode strips these non-essential artifacts from the commit before pushing, keeping only the production code.

```bash
# Enable via CLI flag
uv run factory ceo https://github.com/user/repo --clean-pr

# Strip artifacts from an existing experiment
uv run factory clean-pr ~/my-project --exp 3
```

Configure in `factory.md` for persistent use:

```markdown
## Clean PR
- clean_pr: true
- clean_pr_include: ["src/**", "lib/**"]
- clean_pr_exclude: ["src/internal/**"]
```

Default excludes: `eval/score.py`, `benchmarks/**`, `tests/eval_*`, `.factory/**`. Resolution precedence: CLI flag > `config.json` > default (`false`). The welcome wizard auto-suggests `--clean-pr` when the input is a GitHub URL.

---

## Auto-Research Mode

Auto-Research mode optimizes a **measurable metric** against a dataset — benchmarks, model tuning, prompt optimization, solver agents. re:factory is a meta-harness: give it a research objective and it builds the evaluation harness, then iteratively improves both the system under test *and* the harness itself.

```bash
uv run factory ceo "build a harness to solve SWE-bench lite" --mode research
```

The CEO collects your research target (metric, run command), mutable surfaces (files the Builder can change), and fixed surfaces (ground truth — never touched). Each cycle runs: **baseline** → **failure analysis** → **research** → **hypothesize** → **build** → **re-measure** → **keep/revert**. The metric ratchets forward — it can never go below the previous best.

### Inner loop + outer loop

re:factory operates at two levels, like a researcher who both runs experiments and redesigns the experimental apparatus:

**Inner loop (auto-research):** Build a solver, run it, analyze failures, tweak prompts and logic, re-run. This is the [Karpathy-style](https://www.youtube.com/watch?v=hM_h0UA7upI) pattern — a fixed harness iterating toward a target metric.

**Outer loop (meta-harness):** When inner-loop improvements plateau, re:factory restructures the harness itself — adding new agents, changing the pipeline architecture, introducing A/B strategy frameworks. The system under test evolves, not just its parameters.

Configure these in `factory.md` to control automatic loop transitions:

```markdown
## Inner Loop
- runs_per_cycle: 5
- aggregate: mean
- plateau_threshold: 3
- max_inner_runs_per_cycle: 10

## Outer Loop Surfaces
- max_outer_cycles: 5
- inner: prompts/*.md
- outer: src/**/*.py
```

re:factory runs the harness N times per cycle, aggregates scores, and when improvements plateau for the configured threshold, automatically expands the scope to outer surfaces. See the [Configuration Reference](docs/configuration.md#inner-loop) for field details.

### Example: HMMT math competition solver

re:factory built a multi-agent system to solve [HMMT February 2026 Combinatorics Problem 7](https://www.hmmt.org/) (decagonal prism plane partitions, answer: 1574). Here's what actually happened:

**Round 1 — inner loop (prompt-level fixes):**

re:factory created a 5-agent pipeline (Explorer → Theorist → Computationalist → Critic → Synthesizer) with computational tools (LP-based separability checking, brute-force enumeration). The pipeline ran multiple times:

- Runs 1-2: Agents spent all output tokens on thinking, returned 0 chars. re:factory fixed token limits.
- Runs 3-6: Explorer and Computationalist found 1574, but the Synthesizer "corrected" it to 1572. Classic synthesis failure — the agent added interpretation that degraded a correct upstream answer.
- re:factory experiment 001: Added a consensus override guard, competition math conventions, and mandatory verification to the Synthesizer prompt. The pipeline started producing 1574, but still hit 1572 in ~50% of runs.

**Round 2 — outer loop (architectural restructuring):**

Prompt-level fixes couldn't make the Synthesizer reliable. re:factory's Strategist recognized the plateau and generated an architectural hypothesis: instead of one pipeline with one prompt, create **12 distinct prompt strategies** and test them as A/B experiments.

re:factory added `agents/strategies.py` (1055 lines), three new agent roles (Auditor, Debater, Judge), and modified the orchestrator to support strategy selection. Then it ran all 12 strategies:

| Strategy | Answer | Result |
|---|---|---|
| raw-output-only | 1574 | PASS |
| geometric-verification | 1574 | PASS |
| problem-reformulation | 1574 | PASS |
| counterfactual-contrast | 1574 | PASS |
| bias-inoculation | 1572 | FAIL |
| consistency-enforcement | 1572 | FAIL |
| deferred-interpretation | 1572 | FAIL |
| adversarial-debate | 1572 | FAIL |

**Key discovery:** Strategies that give the Synthesizer *concrete evidence* (immutable tool output, geometric proof, reframed problem text) override the bias. Softer interventions (warnings, procedural rules, debate) don't. This is a finding about LLM behavior that emerged from re:factory's own experimentation loop — not something a human specified.

### Continuous optimization

Once the project is set up, wrap it in a loop for continuous optimization — each cycle is a full experiment pass:

```bash
uv run factory ceo ~/my-solver --loop
uv run factory ceo ~/my-solver --loop --interval 900    # Custom interval
uv run factory tmux ~/my-solver --loop                  # Detached tmux session
```

re:factory auto-detects the research target in `factory.md` and enters research mode. Failed experiments don't stop the loop — re:factory learns from them and moves on.

| Project | Metric | What re:factory optimizes |
|---------|--------|---------------------------|
| SWE-bench solver | resolve rate | Agent logic, prompts, localization strategies |
| HMMT math solver | solve rate | Agent prompts, pipeline architecture, strategy selection |
| Text/Sketch → CAD | query accuracy | Query builder, schema mapping, entity resolution |

See [Getting Started — Research Mode](docs/getting-started.md#research-mode-in-detail) for phase tables, leakage guards, and progression details.

---

## Eval System

Every change is measured by an 11-dimension composite score across three tiers: **Hygiene** (tests, lint, types, coverage), **Growth** (API surface, experiment diversity, observability), and **Project** (user-defined domain metrics). On first run, `uv run factory discover` auto-detects your project's language and framework to generate the eval profile. See [Eval System](docs/eval.md) for scoring details, weights, and guards.

---

## Self-Evolving Agents (ACE)

re:factory improves itself. Every keep/revert decision becomes training data — **ACE (Autonomous Context Engineering)** runs a Reflect → Curate → Inject loop that evolves agent playbooks from real experiment outcomes. Rules that correlate with kept experiments get reinforced; rules from reverts get pruned. Run `uv run factory ceo /path --mode meta` on a regular cadence. See [ACE Self-Improvement](docs/ace.md).

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
| **[OpenSkies Airline Corpus](https://github.com/lukeinglis/OpenSkiesAirline)** | 85-document fictional airline corpus for RAG/fine-tuning evaluation with cross-document consistency validation | Interactive + Improve |
| **re:factory itself** | Runs on itself in meta mode — agent playbooks are evolved from its own experiment outcomes | Meta |

Built something with re:factory? Open a PR to add it here.

---

## CLI Quick Reference

```bash
# Core workflow
uv run factory ceo <path|url|idea>              # Build or improve
uv run factory ceo <path> --mode interactive    # Discuss, then execute
uv run factory ceo <path> --focus "..."         # One target, one experiment
uv run factory ceo <path> --refine "..."        # Single targeted refinement
uv run factory ceo <path> --loop                # Continuous loop (research projects)
uv run factory tmux <path> --loop               # Loop in detached tmux session

# Agents & analysis
uv run factory agent <role> --task "..." --project <path>
uv run factory eval <path>                      # Run evals
uv run factory precheck <path>                  # Hard precheck gate
uv run factory study <path>                     # Analyze code
uv run factory diff <path> --exp1 N --exp2 M    # Compare experiments
uv run factory history <path>                   # Experiment history

# Backlog
uv run factory backlog-list <path>
uv run factory backlog-add <path> "..."
uv run factory backlog-remove <path> "..."

# Token usage & cost
uv run factory usage <path>                     # Per-agent token breakdown
uv run factory usage <path> --json              # Machine-readable output

# Operations
uv run factory dashboard                        # Live web dashboard on :8420
uv run factory discover <path>                  # Auto-detect eval profile
uv run factory config show                      # Show resolved config
uv run factory refine-status <path>              # Refinement state + regrounding
uv run factory clean-pr <path> --exp N          # Strip artifacts from experiment PR
uv run factory tmux-ls                          # List active tmux sessions
uv run factory tmux-stop --path <path>          # Stop a tmux session
```

See `uv run factory --help` for the complete list. re:factory supports crash-resilient resume — the CEO reconstructs context from the event log and `.factory/` state on restart.

---

## Runners

re:factory supports multiple CLI backends. Default is Claude Code — switch with `--runner` or `FACTORY_RUNNER`:

```bash
# Direct
CODEX_API_KEY="..." uv run factory ceo /path --runner codex
BOBSHELL_API_KEY="..." uv run factory ceo /path --runner bob

# Via config.toml profile (persistent)
uv run factory ceo /path --profile codex
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

Run `uv run factory config show` to see resolved config, or `uv run factory config edit` to open the file. See [Setup Guide](docs/setup.md) for full details.

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

The plugin still shells out to the `factory` CLI for the heavy lifting, so you'll need `uv` and the `factory` package installed locally as described in [Quick Start](#quick-start).

To update later: `/plugin marketplace update remote-factory`. To remove: `/plugin uninstall factory@remote-factory`.

---

## Plugin Agents

If you'd rather skip the marketplace and just register the specialist agents as standalone Claude Code (or Codex) subagents, use the built-in installer:

```bash
uv run factory install                   # Install all 9 agents to ~/.claude/agents/
uv run factory install --runner codex    # Or install Codex TOML agents to ~/.codex/agents/
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

## Development

```bash
uv sync --all-groups              # Install all deps including dev
uv run pytest -v                  # Full test suite
uv run ruff check .               # Lint
uv run mypy factory/              # Type check
```

## License

[MIT](LICENSE) — Akash Srivastava
