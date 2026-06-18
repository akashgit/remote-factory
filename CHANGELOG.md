# Changelog

## Unreleased

### Features

- **Post-cycle refinement loop** — After build/improve cycles complete in foreground mode, the CEO stays active and routes follow-up requests through the Refiner → Builder → full review pipeline. New `--refine` flag for direct refinement entry. Three CLI commands (`refine-status`, `refine-begin`, `refine-complete`) provide identity regrounding and state tracking. No hard cap on refinements; advisory warnings at 5 and 10
- **Refiner agent** — New specialist that classifies refinement requests into tiers (T1: prompt/config, T2: code changes, T3: architectural — requires `--focus`) and scopes the implementation for the Builder
- **Inner/outer loop controls** — Configure multi-run aggregation, plateau detection, and automatic scope expansion for research mode via `## Inner Loop` and `## Outer Loop Surfaces` in `factory.md`
- **User profiling** — LLM-driven user profiling system for personalized agent behavior
- **Review counter fix** — Separate review iteration counters for 2d-review and 2h-final stages to fix counter starvation bug
- **Distiller spec depth enforcement** — Grounding protocol, 3-sentence minimum, quantitative CEO review gate

## v0.2.0 (2026-04-29)

### Features

- **Bob Shell runner** — Alternative CLI backend via `--runner bob` or `FACTORY_RUNNER=bob`. Protocol-based runner abstraction (`factory/runners/`) with dry-run mode (`FACTORY_BOB_DRY_RUN=1`), per-cycle and per-day invocation ceilings, auth persistence for nested subagents, and streaming output with role-prefixed lines
- **CEO completion guard** — Auto-resumes when the CEO exits before all planned work is complete. Cycle state persists in `.factory/state/cycle.json` across respawns, with cross-cycle scoping (timestamps in `results.tsv`) to prevent stale experiment contamination. Configurable max respawns via `FACTORY_CEO_MAX_RESPAWNS`, 24-hour staleness threshold, and budget-aware re-spawn gating
- **Interactive ideation mode** — `factory ceo "idea" --mode interactive` launches a research → brainstorm → refine loop before any code is written. The new Distiller agent synthesizes research and user feedback into a structured project spec (`idea.md`). Up to 5 refinement iterations with targeted follow-up research
- **Focused mode** — `--focus "target"` pins a single backlog item and scopes the entire pipeline: one item, one hypothesis, one experiment, done. Requires improve mode, mutually exclusive with `--loop`
- **Unified backlog** — Replaces the old deferred-items system with `.factory/strategy/backlog.md`. The Strategist clears backlog items each cycle with convergence tracking — new items are capped, backlog must shrink not grow. CLI: `factory backlog-list`, `factory backlog-add`, `factory backlog-remove`
- **Session summaries** — End-of-cycle reports via `factory summary`: what was built (kept experiments with score deltas), what was deferred, what needs human input. Written to `.factory/reviews/session-summary.md`
- **Experiment checkpoint/resume** — Per-experiment CEO state saved via `factory checkpoint` for crash-resilient recovery. Includes completed agents, pending agents, hypothesis state, and completed experiment IDs
- **Auto-discovery** — Managed projects auto-detected from projects directory. `factory study` scans sibling projects for cross-project insights
- **Citation backfill** — Research grounding scores now extract and backfill citations from experiment history for more accurate `research_grounding` dimension scoring
- **Model override** — `--model` flag and `FACTORY_MODEL` env var for controlling which model agent subprocesses use

### Fixes

- **CEO no longer auto-merges PRs** — Leaves merge for human review, matching the no-merge policy (#125)
- **Guard check ignores auto-generated lock files** — `uv.lock`, `package-lock.json`, etc. no longer trigger scope violations
- **Interactive mode path resolution** — Idea strings are no longer treated as file paths (#125)
- **Reviewer prompt** — Corrected from "merge" to "approve PR" to match the no-merge policy
- **Discover auto-chains** — Discovery mode proceeds directly to improve mode instead of stopping
- **Deferred item persistence** — Deferred items survive strategy rewrites across cycles
- **Calendar-time estimate stripping** — CEO gate rejects agent outputs containing time estimates like "8-10 weeks"

### Quality

- **1144 tests** — Up from 878 at v0.1.0 (30% increase)
- **Codecov** — Coverage reporting integrated into CI

## v0.1.1 (2026-04-24)

### Fixes

- **Vault path**: Removed all hardcoded vault path references — vault path is now resolved exclusively via `$FACTORY_VAULT_PATH` env var
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
- **Universal Input** — accepts directories, GitHub URLs, idea file paths, or raw text prompts

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
