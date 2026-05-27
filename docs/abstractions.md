# Core Abstractions

This document describes what the Factory *is* — the two primitives everything else derives from. For how they're implemented, see [Architecture](architecture.md).

## What It Is (One Sentence)

An autonomous loop that proposes code changes, measures them, and keeps or reverts — then uses its own outcome history to get better at proposing changes.

## The Two Primitives

### 1. The Loop

```
Hypothesize → Implement → Measure → Keep/Revert → Learn
```

Every code change is an **experiment** with a hypothesis, before/after measurement, and a binary verdict. The experiment — not a task, not a prompt — is the atomic unit. Reverts aren't failures; they're data.

The loop operates at two apertures:

- **Inner loop**: Constrain what can change (prompts, parameters, config). Iterate within a fixed architecture.
- **Outer loop**: When inner loop plateaus, widen the aperture. Restructure the architecture itself.

These aren't two separate systems. They're the same loop with a wider or narrower scope of what's allowed to change. The `mutable_surfaces` / `fixed_surfaces` config is the aperture dial. Plateau detection auto-widens it.

### 2. The Guardrails

The loop needs something to prevent it from drifting, regressing, or cheating:

- **Eval** — a multi-dimensional fitness function that scores every change on a continuous 0–1 scale (not binary pass/fail).
- **Precheck** — non-overridable gates: no score regression, no scope violation, no repeating reverted experiments, no leaking test answers.
- **Auto-generated** — the factory discovers what to measure from the project itself. You don't write the eval; the factory introspects your project and generates one.

Without guardrails, the loop is an AI coding agent that occasionally checks if tests pass. With them, the loop is self-correcting — it literally cannot merge a regression.

## Where Everything Fits

Every module in the repo serves one of these roles:

| Role | What it does | Modules |
|------|-------------|---------|
| **Loop: State** | Track experiments, history, project state | `store.py`, `models.py`, `state.py`, `events.py`, `checkpoint.py` |
| **Loop: Hypothesize** | Decide what to try next | `strategy.py` (FEEC ranking, anti-patterns, stuck detection) |
| **Loop: Implement** | Execute the hypothesis | `agents/runner.py`, `runners/*`, `worktree.py` |
| **Loop: Learn** | Improve from outcomes | `ace/reflector.py`, `ace/curator.py`, `ace/injector.py` |
| **Guardrails: Measure** | Score every change | `eval/runner.py`, `eval/hygiene.py`, `eval/growth.py`, `eval/scorer.py` |
| **Guardrails: Gate** | Block regressions | `precheck.py`, `eval/guards.py`, `research/leakage.py` |
| **Guardrails: Discover** | Auto-generate evals | `discovery/introspect.py`, `discovery/profile.py`, `discovery/generate.py` |
| **Orchestration** | Wire the loop together | `cli.py`, `ceo_completion.py`, `user_config.py`, `agents/prompts/*.md` |
| **Cross-project** | Transfer learning across projects | `registry.py`, `insights.py` |
| **Visualization** | Render loop state (optional) | `dashboard/*`, `visualizer/*`, `obsidian/*`, `miro/*` |
| **Integration** | Connect to external systems (optional) | `notify/*`, `issue.py`, `review.py`, `mcp_server.py` |

## Module Map

### Core: The Loop

| Module | Lines | What it does | Key implementation choice |
|--------|-------|-------------|--------------------------|
| `store.py` | 633 | Experiment lifecycle: `begin()` → `save_eval()` → `finalize()`. Append-only history. | Flat files (TSV + JSON per experiment), not a database. File-lock for concurrency. |
| `models.py` | 546 | Every data type: `ExperimentRecord`, `FactoryConfig`, `CompositeScore`. | Strict Pydantic v2 with `extra="forbid"`. Types are the contract between stages. |
| `strategy.py` | 405 | FEEC ranking, stuck detection, anti-pattern similarity, plateau detection. | Keyword-based categorization. Jaccard similarity for anti-patterns. Could be learned. |
| `state.py` | 88 | Detect project state: 5-state FSM driving which mode the loop enters. | Simple git + file existence checks. |
| `events.py` | 92 | Append-only JSONL event log. The loop's heartbeat. | One-liner writes. No schema enforcement. |
| `worktree.py` | 167 | Git worktree per experiment for branch isolation. | Each experiment gets its own working copy. Prevents cross-experiment interference. |
| `checkpoint.py` | 88 | Save/restore loop state for crash recovery. | JSON dump of completed/pending phases. |

### Core: The Guardrails

| Module | Lines | What it does | Key implementation choice |
|--------|-------|-------------|--------------------------|
| `eval/runner.py` | 328 | Orchestrate 3-tier eval: hygiene + growth + project. Normalize weights. | Subprocess execution. This is the fitness function. |
| `eval/hygiene.py` | 538 | 6 mandatory dimensions: tests, lint, types, coverage, guards, config. | Auto-detects tooling (pytest/jest, ruff/eslint, mypy/tsc). Runs as subprocesses. |
| `eval/growth.py` | 511 | 5 mandatory dimensions: capability surface, diversity, observability, research grounding, effectiveness. | AST-based code analysis. Reads experiment history. No LLM needed. |
| `eval/scorer.py` | 46 | Weighted sum → composite score. | `total = sum(r.score * r.weight)`. Trivial math; the power is in what feeds it. |
| `eval/guards.py` | 215 | Scope guards: did the change touch files outside declared scope? | Git diff + fnmatch. Hard fail on violation. |
| `precheck.py` | 412 | Non-overridable gate: 7 independent checks, ANY failure = mandatory revert. | CEO cannot override. This is the integrity guarantee. |
| `discovery/introspect.py` | 305 | Auto-detect project language, framework, tooling. | File-based heuristics (pyproject.toml → Python). No LLM. |
| `discovery/profile.py` | 149 | Introspection → EvalProfile (list of eval dimensions). | Rule-based mapping. |
| `discovery/generate.py` | 208 | EvalProfile → `eval/score.py` script. | Template-based code generation. |
| `research/leakage.py` | 408 | Ground truth leakage detection (research mode). | Token fingerprinting. Scans for direct values AND negation hints. |

### Core: Learning (ACE)

| Module | Lines | What it does | Key implementation choice |
|--------|-------|-------------|--------------------------|
| `ace/reflector.py` | 877 | Parse experiment outcomes → candidate playbook bullets. | Deterministic pattern extraction. No LLM. Correlates categories with keep/revert rates. |
| `ace/curator.py` | 149 | Merge bullets into playbooks, update helpful/harmful counters. | Fuzzy match existing bullets. Prune high-harmful rules. |
| `ace/injector.py` | 45 | Prepend playbook to agent prompts at invocation. | String concatenation. Simple but critical — how learning reaches agents. |
| `ace/models.py` | 126 | `Playbook`, `PlaybookItem` with counters. | YAML frontmatter + markdown. Human-readable. |
| `ace/paths.py` | 54 | Playbook file locations. | `~/.factory/playbooks/<role>.md` — user-local, not per-project. Cross-project learning. |

### Core: Execution Engine

| Module | Lines | What it does | Key implementation choice |
|--------|-------|-------------|--------------------------|
| `agents/runner.py` | 313 | Resolve prompt → spawn subprocess → capture output → save review. | Two-tier prompt lookup (project override → factory default) + ACE playbook injection. |
| `agents/prompts/ceo.md` | 2868 | The loop's full protocol: state machine, review gates, sacred rules. | The loop IS this prompt. 2900 lines of structured protocol in markdown. |
| `agents/prompts/*.md` | ~1600 | 10 specialist agent instructions. | Each agent has a focused role. Surprisingly small individual prompts. |
| `runners/protocol.py` | 71 | Runner interface: `headless()` method. | One method: prompt+task in, stdout+exitcode out. |
| `runners/claude.py` | 125 | Claude Code backend (default). | Spawns `claude` CLI as subprocess. |
| `runners/bob.py` | 382 | Bob Shell backend. | Alternative runner. Has token ceiling guardrails. |
| `runners/codex.py` | 183 | OpenAI Codex backend. | `codex exec` with workspace-write sandbox. |
| `cli.py` | 4006 | All CLI commands, wizard, CEO task building. | Monolithic. `_build_ceo_task()` is where config becomes a CEO prompt. |
| `user_config.py` | 276 | `~/.factory/config.toml` with 5-tier precedence. | CLI > env > profile > config.toml > default. |

### Core: Cross-Project Intelligence

| Module | Lines | What it does | Key implementation choice |
|--------|-------|-------------|--------------------------|
| `registry.py` | 150 | Global project registry. | Self-registration on first `begin()`. Lives at `~/.factory/registry.json`. |
| `insights.py` | 336 | Aggregate outcomes across all projects. | Reads all registries. Computes category stats, winning/losing patterns. |

### Optional: Observation & Analysis

| Module | Lines | What it does |
|--------|-------|-------------|
| `study.py` | 1097 | Codebase analysis: observability coverage, module stats, backlog synthesis. |
| `analysis.py` | 244 | Experiment comparison (`factory diff`) and FEEC analysis (`factory explain`). |
| `summary.py` | 227 | End-of-cycle session summary. |
| `report.py` | 186 | Performance report generation for ACE consumption. |
| `research/runner.py` | 390 | Research run infrastructure: execute commands, parse results, manage artifacts. |
| `ceo_completion.py` | 520 | Auto-resume when CEO exits prematurely. |
| `profile.py` | 232 | User profiling from experiment history (opt-in). |
| `discovery/eval_spec.py` | 217 | Auto-generate eval_spec from project profile. |

### Optional: Visualization & Output

| Module | Lines | What it does |
|--------|-------|-------------|
| `dashboard/app.py` | 836 | FastAPI web dashboard with SSE event streaming. |
| `visualizer/state.py` | 359 | Infer live pipeline state from event log. |
| `obsidian/notes.py` | 570 | Write experiment notes to Obsidian vault. |
| `obsidian/templates.py` | 73 | Obsidian note templates. |
| `digest.py` | 244 | Vault activity summarizer. |

### Optional: Integrations

| Module | Lines | What it does |
|--------|-------|-------------|
| `review.py` | 144 | Post structured reviews on GitHub PRs. |
| `issue.py` | 188 | Fetch GitHub/GitLab issues as build specs. |
| `notify/telegram.py` | 90 | Telegram digest notifications. |
| `mcp_server.py` | 188 | Expose factory as MCP tools for other Claude Code sessions. |
| `agents/plugin.py` | 111 | Generate Claude Code subagent files. |
| `messages.py` | 129 | User → CEO message queue. |
| `refine_state.py` | 130 | Post-cycle refinement tracking. |
| `backfill_archive.py` | 164 | Backfill missing archive notes. |
| `clean_pr.py` | 213 | Strip non-essential artifacts from PRs. |
| `runners/usage.py` | 166 | Bob Shell token ceiling tracking. |
| `runners/_stream.py` | 99 | Output stream processing. |

## By the Numbers

```
Total:      57 modules, ~19,300 lines Python, ~4,500 lines prompts

Core:       ~10,500 lines (54%)  — the loop, guardrails, learning, execution
Optional:   ~8,800 lines (46%)   — visualization, integrations, observation, utilities
```

The core is surprisingly small. The loop, the eval, the precheck, and the agent execution engine — the things that make the system work — are about half the codebase. The rest is reach.

## Key Implementation Choices (Debatable)

These are decisions that could go differently. The abstractions (loop + guardrails) are stable; these are where experimentation makes sense.

| Choice | Current implementation | Alternatives worth exploring |
|--------|----------------------|------------------------------|
| **How the loop hypothesizes** | LLM agent (Strategist) + keyword FEEC ranking | Bandit algorithms, learned priority from outcome data, retrieval-augmented |
| **How guardrails are generated** | File-based heuristics (detect pyproject.toml → pytest) | LLM-generated evals, user-guided, learned dimension importance |
| **The fitness function** | 11 fixed dimensions, configurable weights | Fully user-defined, adaptive weights, learned dimension importance |
| **Agent decomposition** | 8 named specialists with static roles | Fewer (3: plan/build/verify), more (dynamic), or none (single agent) |
| **Learning mechanism** | Text-rule playbooks (DO/DON'T bullets) | Embedding retrieval over past experiments, fine-tuning, in-context examples |
| **Runner abstraction** | Subprocess spawning of CLI tools | Direct API calls, SDK integration, self-hosted models |
| **The CEO protocol** | 2900-line markdown prompt with embedded state machine | Shorter prompt + code-side orchestration, compiled state machine |
| **Aperture control** | Manual surfaces + auto-plateau-detection | Fully adaptive (start narrow, auto-widen as confidence grows) |
| **Loop memory** | Flat files (TSV + JSON in `.factory/`) | SQLite, experiment tracker (MLflow/W&B), vector store |
| **Anti-pattern detection** | Jaccard similarity on hypothesis text | Semantic similarity (embeddings), structural diff comparison |

The biggest architectural bet: **the CEO prompt IS the loop.** The entire state machine, review protocol, and decision logic lives in a 2900-line markdown file fed to an LLM. The Python layer is pure tools — it doesn't make decisions. This is either the system's greatest strength (easy to evolve, runner-independent, self-documenting) or its greatest liability (fragile, context-window-dependent, hard to test deterministically).
