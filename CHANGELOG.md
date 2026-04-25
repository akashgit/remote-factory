# Changelog

## v0.1.1 (2026-04-24)

### Fixes

- **Vault path**: Removed all hardcoded `~/factory-vault/` references — vault path is now resolved exclusively via `$FACTORY_VAULT_PATH` env var
- **Mypy**: Fixed type errors in `factory/eval/hygiene.py` and `factory/dashboard/app.py`
- **CI-safe tests**: `test_rewards_vault_sources` and related growth tests no longer depend on local filesystem state

### Docs & CI

- **GitHub Actions CI** — pytest (3.11/3.12/3.13 matrix), ruff, mypy; runs on PRs only
- **MkDocs Material** — hosted docs at [akashgit.github.io/remote-factory](https://akashgit.github.io/remote-factory/), auto-deployed on push
- **Mermaid diagrams** — README uses native GitHub-rendered Mermaid instead of external SVGs
- **Self-evolving messaging** — README title and intro emphasize the factory's learning loop
- **Obsidian recommendation** — docs highlight vault setup for persistent cross-project learning

## v0.1.0 (2026-04-24)

Initial public release.

### Core

- **CEO Agent** — dedicated orchestrator with 5-state machine (no_repo, incomplete, no_factory, evals_pending_review, has_factory), automatic mode routing, and mandatory archival
- **7 Specialist Agents** — Researcher, Strategist, Builder, Reviewer, Evaluator, Archivist, each running as independent Claude Code subprocesses
- **Experiment Loop** — every change is a hypothesis: measured before/after, kept or reverted based on composite eval score
- **Universal Input** — accepts directories, GitHub URLs, Obsidian vault ideas, or raw text prompts

### Eval System

- **Three-tier composite scoring** — hygiene (6 dimensions), growth (5 dimensions), and user-defined project eval
- **Configurable weight distribution** — default 50/50, shifts to 30/20/50 with project eval
- **Hard precheck gate** — 4 non-overridable checks (score direction, scope, anti-pattern, smoke test)
- **Guard rules** — scope enforcement and eval immutability

### Strategy

- **FEEC priority** — Fix > Exploit > Explore > Combine hypothesis ranking
- **Stuck detection** — forces category rotation after 3+ consecutive same-category reverts
- **Structured hypothesis budget** — reserved fix/growth/flex slots, configurable per-run

### Self-Improvement

- **ACE (Autonomous Context Engineering)** — Reflect/Curate/Inject loop that evolves agent playbooks from real experiment outcomes
- **Cross-project learning** — patterns from one project inform behavior on others
- **Helpful/harmful counters** — evidence-based rule reinforcement and pruning

### Operations

- **Live dashboard** — FastAPI server with SSE-powered real-time UI (port 8420)
- **Continuous mode** — heartbeat loop with configurable interval and max cycles
- **tmux integration** — detached sessions that survive SSH disconnects
- **Crash resilience** — CEO checkpoint save/load for resume after failures
- **Structured PR reviews** — score tables, guard results, and code notes posted on GitHub PRs

### Integrations

- **Obsidian vault** — optional archival of experiment history and cross-project knowledge
- **MCP servers** — Playwright for UI testing, extensible per-project
- **Claude Code agent registration** — `factory install` for seamless integration

### CLI

30+ subcommands including: `ceo`, `run`, `agent`, `eval`, `precheck`, `guard`, `begin`, `finalize`, `history`, `diff`, `explain`, `study`, `insights`, `ace`, `dashboard`, `detect`, `discover`, `export`, `checkpoint`, `resume`, `tmux`, `digest`, `archive`, `notify`, `review`, `install`, `vault-init`, `self-update`.

### Quality

- 878 tests with pytest
- Type checking with mypy
- Linting with ruff
- Strict Pydantic v2 models throughout
