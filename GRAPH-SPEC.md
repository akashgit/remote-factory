# GRAPH-SPEC.md — Behavioral Specification: Remote Factory

> **Spec revision:** 2026-06-30
> **Status:** Annotated behavioral specification (RFC 2119 normative language)

---

## Table of Contents

- [1 Problem Statement](#1-problem-statement)
- [2 Goals and Non-Goals](#2-goals-and-non-goals)
- [3 Project Identity](#3-project-identity)
- [4 Technical Stack](#4-technical-stack)
- [5 Architecture Overview](#5-architecture-overview)
- [6 Domain Model](#6-domain-model)
- [7 State Machines and Lifecycles](#7-state-machines-and-lifecycles)
- [8 Module Specifications](#8-module-specifications)
- [9 Shared Contracts](#9-shared-contracts)
- [10 Configuration Specification](#10-configuration-specification)
- [11 Entry Points](#11-entry-points)
- [12 Failure Model and Recovery](#12-failure-model-and-recovery)
- [13 Security and Safety](#13-security-and-safety)
- [14 Test and Validation Matrix](#14-test-and-validation-matrix)
- [15 Extension Points](#15-extension-points)
- [16 Implementation Checklist](#16-implementation-checklist)
- [Appendix A: Reference Algorithms](#appendix-a-reference-algorithms)

---

## 1 Problem Statement

Software projects require continuous improvement: better test coverage, fewer
lint violations, stronger typing, richer capabilities.  Manual improvement cycles
are slow, context-heavy, and hard to sustain.

Remote Factory automates this loop.  It observes a project's current state,
hypothesizes improvements via the FEEC priority heuristic, implements them
through specialist AI agents, verifies results through multi-gate review, and
archives learnings for future cycles.  The system MUST operate without human
intervention in headless mode and MUST support interactive refinement when a
human is present.

---

## 2 Goals and Non-Goals

### 2.1 Goals

1. **Autonomous improvement** — The factory MUST detect project state, select the
   appropriate workflow mode, and execute a full observe-hypothesize-build-review
   cycle without human input in headless mode.
2. **Measurable progress** — Every experiment MUST produce a before/after
   composite score.  The keep/revert decision MUST be based on eval deltas and
   guard compliance.
3. **Safety** — Nine Sacred Rules (see [13.1](#131-sacred-rules-ceo)) MUST be
   enforced.  Fixed surfaces MUST NOT be modified.  Eval harness MUST NOT be
   tampered with mid-experiment.
4. **Cross-project learning** — The ACE pipeline MUST evolve agent playbooks
   based on statistical patterns across all managed projects.
5. **Multi-runner support** — The factory MUST support pluggable CLI backends
   (Claude Code, Bob Shell, Codex, OpenCode) via the Runner protocol.

### 2.2 Non-Goals

1. Direct API calls to LLM providers — the factory delegates to CLI wrappers.
2. Real-time collaboration — the factory is a batch/loop system, not an IDE.
3. Automatic deployment — the factory produces commits and PRs, not deployments.

### 2.3 Design Philosophy

- **Tools don't decide; agents do.** Layer 1 (CLI) and Layer 2 (Workflow Engine)
  are pure tools.  Layer 3 (CEO) and Layer 4 (Specialists) make decisions.
- **Fail loud at boundaries.** Subprocess failures MUST be captured, not
  propagated.  Domain errors MUST use specific exception types.
- **Append-only history.** `results.tsv` and `events.jsonl` are append-only.
  Experiments are never deleted, only verdicted.

---

## 3 Project Identity

| Field           | Value |
|-----------------|-------|
| Name            | `remote-factory` |
| Type            | CLI tool + multi-agent orchestration framework |
| Language        | Python 3.11+ |
| Entry point     | `factory/cli.py` -> `factory.cli:main` |
| Package manager | `uv` |
| License         | Proprietary |

---

## 4 Technical Stack

| Component       | Technology | Constraint |
|-----------------|------------|------------|
| Runtime         | Python 3.11+ | MUST use `X \| Y` unions, not `Union[X, Y]` |
| Data models     | Pydantic v2 | All models MUST use `ConfigDict(strict=True, extra="forbid")` |
| Logging         | structlog | Module-level `log = structlog.get_logger()` |
| Async           | asyncio | Library functions async by default; CLI wraps with `asyncio.run()` |
| Linting         | ruff | 100-char line length |
| Type checking   | mypy | Strict mode on `factory/` |
| Testing         | pytest + pytest-asyncio | `asyncio_mode = "auto"` |
| File locking    | filelock | Used for TSV append and experiment ID allocation |
| Workflows       | NetworkX (validation only) | Graph validation delegates to `nx.DiGraph` |

---

## 5 Architecture Overview

### 5.1 Layer Model

```
Layer 1: CLI (factory/cli.py)
   Pure dispatcher. cmd_* handlers, argparse, heartbeat loop.
   MUST NOT contain business logic or make agent decisions.

Layer 2: Workflow Graph Engine (factory/workflow/)
   Directed graphs of typed nodes. Deterministic execution.
   MUST produce identical results given identical inputs and agent responses.

Layer 3: CEO Agent (ceo.md + skills/workflow-*/SKILL.md)
   Orchestrator. Owns state detection, agent spawning, review gates.
   MUST enforce Sacred Rules. MUST NOT implement code directly.

Layer 4: Specialist Agents (factory/agents/)
   Eight roles: researcher, strategist, builder, qa, archivist,
   failure_analyst, refiner, refactory.
   Each MUST operate within its role boundary.
```

### 5.2 Data Flow — Improve Cycle

```
CLI(cli.py)
  -> detect_state(state.py)           -> ProjectState enum
  -> ExperimentStore(store.py)        -> read FactoryConfig
  -> study_project(study.py)          -> .factory/strategy/observations.md
  -> invoke_agent("researcher")       -> .factory/strategy/research-local.md
  -> CEO gate(gate_research)          -> PROCEED | RELOOP | ABORT
  -> invoke_agent("strategist")       -> .factory/strategy/current.md
  -> CEO gate(gate_strategy)          -> PROCEED | RELOOP | ABORT
  -> ExperimentStore.begin(hypothesis)-> experiment ID (filelock-guarded)
  -> invoke_agent("builder")          -> code changes + PR
  -> CEO gate(gate_build)             -> PROCEED | RELOOP | ABORT
  -> invoke_agent("qa")               -> .factory/reviews/qa-latest.md
  -> CEO gate(gate_qa)                -> PROCEED | RELOOP(max 3) | ABORT
  -> precheck(precheck.py)            -> PROCEED | HALT
  -> ExperimentStore.finalize()       -> verdict + results.tsv (filelock)
  -> invoke_agent("archivist")        -> .factory/archive/ (non-blocking)
  -> events.py                        -> .factory/events.jsonl
```

### 5.3 Eval Pipeline

```
run_eval(eval/runner.py)
  |-- Hygiene Tier (eval/hygiene.py)           50% weight (default)
  |     |-- tests: pytest/go test/npm test/cargo test
  |     |-- lint: ruff/go vet/eslint/cargo clippy
  |     |-- type_check: mypy/tsc
  |     |-- coverage: pytest-cov
  |     |-- config_parser: validate factory.md parsing
  |     +-- architecture: sentrux
  |
  |-- Growth Tier (eval/growth.py)             50% weight (default)
  |     |-- capability_surface: module/function counting
  |     |-- experiment_diversity: bigram Jaccard similarity
  |     |-- observability: structured logging analysis
  |     |-- research_grounding: source/doc counting
  |     |-- factory_effectiveness: keep rate
  |     +-- spec_compliance: GRAPH-SPEC.md conformance
  |
  |-- Project Eval (subprocess)                0% weight (until configured)
  |-- Guards (eval/guards.py)                  pass/fail, no score
  +-- Scorer (eval/scorer.py)                  -> CompositeScore
        _merge_all: weighted average across tiers
        _normalize_tier: within-tier weight overrides
```

Weight distribution shifts to 30/20/50 (hygiene/growth/project) when project
evals are configured.

---

## 6 Domain Model

### 6.1 Core Aggregates

#### ProjectState (Value Object)

```
NO_REPO -> REPO_INCOMPLETE -> NO_FACTORY -> EVALS_PENDING_REVIEW -> HAS_FACTORY
```

An enum with 5 values.  Detection order in `state.py` is strict priority:
1. No `.git/` -> `NO_REPO`
2. Open plan issues (via `gh issue list --label plan`) -> `REPO_INCOMPLETE`
3. No `.factory/config.json` -> `NO_FACTORY`
4. `eval_profile.json` exists with `human_reviewed=false` -> `EVALS_PENDING_REVIEW`
5. Otherwise -> `HAS_FACTORY`

#### FactoryConfig (Entity)

The machine-readable project configuration.  Parsed from `factory.md` via
section-name mapping.  All fields MUST satisfy `ConfigDict(strict=True,
extra="forbid")`.

Key fields: `goal`, `scope[]`, `guards[]`, `eval_command`, `eval_threshold`,
`constraints[]`, `target_branch`, `research_target`, `mutable_surfaces[]`,
`fixed_surfaces[]`, `cost_budget`, `clean_pr`, `test_timeout`.

#### ExperimentRecord (Entity)

One row in `results.tsv`.  Fields: `id`, `timestamp`, `hypothesis`,
`change_summary`, `issue_number`, `pr_number`, `score_before`, `score_after`,
`delta`, `verdict` (keep|revert|error), `cost_usd`, `notes`,
`research_citations[]`.

#### CompositeScore (Value Object)

Aggregated eval result.  `total` = weighted sum of `EvalResult[]`.
`passed = (no guard_violations) AND (total >= threshold)`.

### 6.2 Supporting Entities

| Entity | Kind | Invariants |
|--------|------|------------|
| `EvalProfile` | Pydantic | `dimensions[].weight` MUST sum to 1.0. `human_reviewed` gates state transition. |
| `Hypothesis` | Pydantic | MUST have `description`, `rationale`, `expected_impact`, `target_files[]`. |
| `AgentVerdict` | Pydantic | `verdict` MUST be one of PROCEED, REDIRECT, ABORT. |
| `Workflow` | Pydantic | Graph MUST be validated via NetworkX. Every Builder MUST have a reachable QA. |
| `Verdict` | ADT | RELOOP MUST have `target`. HALT MUST have `reason`. |
| `CycleState` | Pydantic | Preserves mode across CEO respawns. Stored at `.factory/state/cycle.json`. |
| `RefinementEntry` | Pydantic | Sequence auto-increments. Only last entry MAY be completed. |
| `ProjectEntry` | Pydantic | Global registry at `~/.factory/registry.json`. Self-registration on `begin()`. |
| `ResearchTarget` | Pydantic | Defines objective, metric, run_command, result_path for research mode. |
| `Playbook` | Pydantic (ACE) | Max 15 items after curation. Net-negative items MUST be pruned. |
| `RunResult` | Pydantic | Status MUST be one of PASS, FAIL, ERROR, TIMEOUT. |

### 6.3 Agent Roles

| Role | Responsibility | Default Model |
|------|---------------|---------------|
| `researcher` | Observe, analyze, web-search | sonnet |
| `strategist` | Hypothesize, prioritize (FEEC) | opus |
| `builder` | Implement, test, commit, PR | opus |
| `qa` | Health check, code review, adversarial QA | opus |
| `archivist` | Record learnings, maintain archive | haiku |
| `failure_analyst` | Classify research run failures | opus |
| `refiner` | Classify and scope refinement requests | opus |
| `ceo` | Orchestrate, review-gate, enforce rules | opus |

---

## 7 State Machines and Lifecycles

### 7.1 Project Lifecycle

```
NO_REPO --[git init + scaffold]--> REPO_INCOMPLETE --[close plan issues]--> NO_FACTORY
                                                                               |
                                                                          [discover]
                                                                               v
                                     HAS_FACTORY <--[human_reviewed=true]-- EVALS_PENDING_REVIEW
```

- `detect_state()` MUST evaluate conditions in the priority order specified in
  [6.1](#61-core-aggregates).
- The `gh issue list` subprocess MUST timeout after 15 seconds; timeout is
  treated as "no open issues."

### 7.2 Experiment Lifecycle

```
begin(hypothesis)
  |
  v
ACTIVE --[save_eval("before")]--> EVAL_BEFORE --[build]--> EVAL_AFTER
                                                               |
                                                  +------------+------------+
                                                  v            v            v
                                                keep        revert        error
```

- `begin()` MUST allocate the experiment ID under `FileLock`.
- `begin()` MUST register the project in the global registry.
- `finalize()` MUST append to `results.tsv` under `FileLock`.
- `finalize()` MUST auto-compute delta as `score_after - score_before`.
- Registry update failures during `begin()` and `finalize()` MUST be swallowed
  (non-critical side effect).

### 7.3 Workflow Verdict ADT

```
Gate evaluation
  |-- Verdict.proceed()      -> follow forward edge
  |-- Verdict.reloop(target, feedback, max_iterations) -> jump to target node
  +-- Verdict.halt(reason)   -> stop execution
```

- A RELOOP verdict MUST specify a `target` node.
- A HALT verdict MUST specify a `reason`.
- Exceeding `max_iterations` on reloop MUST automatically halt the workflow.
- Unrecognized gate output SHOULD fall back to PROCEED.

### 7.4 CEO Review Gate

```
Agent output --> CEO Review --+--> PROCEED  (continue pipeline)
                              +--> REDIRECT (reloop with feedback)
                              +--> ABORT    (halt workflow)
```

- Every agent output MUST pass through a CEO review gate before the next
  pipeline stage (except non-blocking archivist nodes).

### 7.5 FEEC Priority Heuristic

```
FIX (0) > EXPLOIT (1) > EXPLORE (2) > COMBINE (3)
```

- Classification uses keyword matching: FIX keywords (`fix`, `bug`, `crash`,
  `fail`, `broken`, `error`), EXPLOIT (`improve`, `increase`, `enhance`,
  `optimize`, `refactor`), COMBINE (`combine`, `merge`, `integrate`, `unify`),
  EXPLORE (default fallback).
- Stuck detection triggers after 3+ consecutive reverts in the same category.

### 7.6 Research Run Status

```
execute_run()
  |-- PASS    (exit 0, metric parsed successfully)
  |-- FAIL    (nonzero exit code)
  |-- ERROR   (result file parse failure)
  +-- TIMEOUT (exceeded deadline, process group killed via SIGKILL)
```

- Timeout MUST kill the entire process group (not just the lead process).
- The run MUST always save artifacts (stdout.log, stderr.log, summary.json)
  regardless of status.

### 7.7 Mode Selection

```
CEO --[detect state + flags]--+--> build     (NO_REPO | REPO_INCOMPLETE)
                              +--> design    (NO_REPO + interactive)
                              +--> discover  (NO_FACTORY)
                              +--> review    (EVALS_PENDING_REVIEW)
                              +--> improve   (HAS_FACTORY)
                              +--> research  (HAS_FACTORY + research_target)
                              +--> meta      (HAS_FACTORY + mode=meta)
                              +--> refine    (HAS_FACTORY + --refine)
                              +--> qa        (HAS_FACTORY + mode=qa + --pr)
                              +--> create    (mode=create)
```

### 7.8 Refinement Lifecycle

```
begin_refinement(request) --> ACTIVE --[build+review]--> complete_refinement(verdict)
                                                              |
                                                         keep | revert
```

- CEO identity re-anchoring MUST occur at sequence 5 and 10.
- Only the last refinement entry MAY be completed.
- Entries >= 5 SHOULD trigger context-window advisory; >= 10 SHOULD trigger
  fresh-session advisory.

---

## 8 Module Specifications

### 8.1 `factory/state.py` — Project State Detection

**Exports:** `detect_state(project_path: Path) -> ProjectState`

**Behavioral contract:**
- MUST evaluate conditions in strict priority order: NO_REPO > REPO_INCOMPLETE
  > NO_FACTORY > EVALS_PENDING_REVIEW > HAS_FACTORY.
- The `gh issue list` subprocess MUST timeout after 15 seconds.
- Subprocess timeouts and `FileNotFoundError` on `gh` MUST be swallowed
  (treated as "check failed = skip").
- JSON parse errors in `eval_profile.json` MUST be swallowed.
- MUST NOT mutate any state.  Pure detection function.

### 8.2 `factory/store.py` — Experiment Filesystem Store

**Exports:** `ExperimentStore` class.

**Behavioral contract:**
- `init()` MUST be idempotent for TSV creation (existing TSV preserved).
- `begin()` MUST allocate experiment IDs under `FileLock(.factory/.store.lock)`.
- `begin()` MUST register the project in the global registry; registration
  failures MUST be swallowed.
- `finalize()` MUST append to `results.tsv` under the same `FileLock`.
- `finalize()` MUST auto-compute `delta = score_after - score_before`.
- `read_config()` MUST raise `FileNotFoundError` if `.factory/config.json` is
  missing, and `ValueError` if JSON is invalid or fails Pydantic validation.
- `reparse_config()` MUST re-read `factory.md` from project root.
- `load_history()` MUST return an empty list if `results.tsv` is missing.
  Invalid verdict values MUST be coerced to `"error"`.

### 8.3 `factory/eval/runner.py` — Eval Runner

**Exports:** `run_eval(eval_command, project_path, threshold, ...) -> CompositeScore`

**Behavioral contract:**
- MUST merge results from three tiers: hygiene (mandatory), growth (mandatory),
  project eval (optional).
- MUST strip `VIRTUAL_ENV` from subprocess env to avoid tool isolation issues.
- Subprocess timeouts MUST kill the process and return empty results (never raise).
- Scores MUST be clamped to `[0.0, 1.0]`.
- SHOULD write `.factory/last_eval.json` if `.factory/` exists; `OSError` on
  write MUST be swallowed.
- MUST NOT propagate subprocess exceptions.

### 8.4 `factory/eval/scorer.py` — Composite Score Computation

**Exports:** `compute_composite(results, guard_violations, threshold) -> CompositeScore`

**Behavioral contract:**
- MUST be a pure function with no side effects.
- Weights MUST be auto-normalized to sum to 1.0.
- `passed` = `(no guard_violations) AND (total >= threshold)`.
- Empty results MUST yield `total=0.0`.

### 8.5 `factory/eval/guards.py` — Safety Guard Checks

**Exports:** `check_all()`, `check_scope()`, `check_fixed_surfaces()`,
`check_eval_immutable()`, `check_git_clean()`, `check_experiment_branch()`,
`snapshot_eval_tree()`.

**Behavioral contract:**
- `check_all()` MUST run all individual guards and return a list of violation
  strings (empty = all pass).
- `check_scope()` MUST support fnmatch patterns with `**` globbing.
- `check_fixed_surfaces()` MUST reject any modification to files matching
  `fixed_surfaces` patterns.
- Guard functions run `git` subprocesses with `check=True`; git failures
  propagate as `CalledProcessError`.

### 8.6 `factory/eval/hygiene.py` — Hygiene Tier

**Exports:** `compute_hygiene_results(project_path, test_timeout) -> list[dict]`

**Behavioral contract:**
- MUST always return exactly 6 result dicts (tests, lint, type_check, coverage,
  config_parser, architecture).
- Undetected tools MUST score 0.5 (neutral), not 0.0.
- Individual dimension exceptions MUST be caught and return `score=0.0,
  passed=False`.
- Tests and coverage MUST share a single subprocess run.

### 8.7 `factory/eval/growth.py` — Growth Tier

**Exports:** `compute_growth_results(project_path) -> list[dict]`

**Behavioral contract:**
- MUST return 6 result dicts (capability_surface, experiment_diversity,
  observability, research_grounding, factory_effectiveness, spec_compliance).
- Dimensions with insufficient data (< 3 experiments) MUST return 0.5 (neutral).
- Stale `spec_results.json` (> 24h) MUST return neutral.
- All dimensions MUST catch `Exception` and return `score=0.0` on failure.

### 8.8 `factory/agents/runner.py` — Agent Invocation

**Exports:** `invoke_agent()`, `invoke_agents_parallel()`, `resolve_prompt()`.

**Behavioral contract:**
- `resolve_prompt()` MUST use two-tier lookup: project override
  (`.factory/agents/<role>.md`) then factory default
  (`factory/agents/prompts/<role>.md`).  MUST raise `FileNotFoundError` if
  neither exists.
- `resolve_prompt()` MUST auto-inject ACE playbook via `ace/injector.py`.
- `invoke_agent()` MUST emit `agent.started`, `agent.completed` or
  `agent.failed`/`agent.timeout` events to `.factory/events.jsonl`.
- `invoke_agent()` MUST save output to `.factory/reviews/<role>-latest.md`.
- MUST raise `ConsecutiveAgentFailureError` after 2+ consecutive failures
  (when failure tracking is enabled).
- Parallel invocations MUST disable per-agent failure tracking to avoid races;
  batch-level failure check applies instead.
- Telemetry errors MUST always be swallowed.

### 8.9 `factory/strategy.py` — FEEC Priority Heuristic

**Exports:** `categorize_hypothesis()`, `rank_hypotheses()`, `detect_stuck()`,
`detect_plateau()`, `hypothesis_similarity()`, `find_anti_patterns()`,
`format_tiered_history()`.

**Behavioral contract:**
- All functions MUST be pure (no side effects beyond logging).
- `categorize_hypothesis()` MUST use keyword-first matching in priority order:
  FIX > EXPLOIT > COMBINE > EXPLORE.
- `detect_stuck()` MUST return True when the last N consecutive reverts share
  a category.  A "keep" verdict MUST reset the streak.
- `hypothesis_similarity()` MUST use Jaccard similarity on word tokens (3+ chars).
- `format_tiered_history()` MUST compress to 3 tiers: Tier 1 (last 3, full),
  Tier 2 (4-10, one-line), Tier 3 (11+, aggregate stats).

### 8.10 `factory/registry.py` — Global Project Registry

**Exports:** `register_project()`, `update_project_stats()`, `list_projects()`,
`get_project_paths()`, `populate_from_directory()`.

**Behavioral contract:**
- `register_project()` MUST be idempotent.
- Writes MUST be atomic (write to `.tmp` file, then rename).
- Corrupt or missing registry MUST return empty (never raise).
- `get_project_paths()` MUST filter to paths that still exist on disk.
- No locking; concurrent writes MAY race.

### 8.11 `factory/workflow/executor.py` — Workflow Graph Executor

**Exports:** `WorkflowExecutor.execute() -> ExecutionResult`

**Behavioral contract:**
- MUST walk the DAG from `start_node` following edge conditions.
- `AgentNode` MUST invoke via `invoke_agent()`.
- `FnNode` MUST invoke via `asyncio.create_subprocess_shell`.
- `GateNode` MUST parse Verdict from output; unrecognized output SHOULD fall
  back to PROCEED.
- `ForkNode` MUST execute targets concurrently via `asyncio.gather`.
- `JoinNode` MUST act as a barrier (wait for all sources).
- Non-blocking nodes MUST run as background asyncio tasks.
- Background tasks MUST be cancelled after 30s timeout on workflow completion.
- Reloop target MUST be fuzzy-matched against node IDs.
- Node failures MUST set `halted=True` with reason.

### 8.12 `factory/workflow/primitives.py` — Workflow Graph Types

**Exports:** All node types, `Edge`, `Workflow`, `Factory`, `Verdict`, enums.

**Behavioral contract:**
- `Verdict` model_validator: RELOOP MUST have `target`, HALT MUST have `reason`.
- `Workflow.validate_graph()` MUST delegate to NetworkX.
- `Workflow.subgraph()` MUST raise `ValueError` if a requested node ID is missing.
- All models MUST use `ConfigDict(strict=True, extra="forbid")`.

### 8.13 `factory/checkpoint.py` — Crash-Resilient Checkpointing

**Exports:** `save_checkpoint()`, `load_checkpoint()`, `clear_checkpoint()`.

**Behavioral contract:**
- `load_checkpoint()` MUST return `None` on missing or corrupt file (never raise).
- `save_checkpoint()` MUST create `.factory/` if needed.
- `clear_checkpoint()` MUST be safe to call when no checkpoint exists.

### 8.14 `factory/clean_pr.py` — Clean PR Mode

**Exports:** `filter_pr_diff()`, `strip_pr_artifacts()`.

**Behavioral contract:**
- Exclude MUST win over include when patterns overlap.
- Default excludes: `eval/score.py`, `benchmarks/**`, `tests/eval_*`,
  `.factory/**`.
- `strip_pr_artifacts()` MUST archive the full diff to the experiment directory
  before stripping.
- Git failures on individual files MUST be logged and skipped, not propagated.

### 8.15 `factory/research/runner.py` — Research Run Infrastructure

**Exports:** `parse_result()`, `execute_run()`, `execute_multi_run()`,
`aggregate_metric()`.

**Behavioral contract:**
- `parse_result()` MUST support JSON-only parsing with dotted paths and
  slash-ratio (`numerator/denominator`).
- `parse_result()` MUST raise `ResultParseError` on: missing file, bad JSON,
  missing key, non-numeric value, NaN/Inf, zero denominator.
- `execute_run()` MUST run commands in a new process session for group kill.
- Timeout MUST kill the entire process group via SIGKILL.
- MUST always save artifacts regardless of run status.
- `create_run_dir()` MUST reject path traversal in `cycle_id` (containing
  `../` or `/`).
- `aggregate_metric()` MUST support mean, median, max, all_pass (= min).

### 8.16 `factory/research/leakage.py` — Ground Truth Leakage Detection

**Exports:** `fingerprint_fixed_surfaces()`, `scan_for_leakage()`,
`scan_diff_for_leakage()`, `validate_research_config()`.

**Behavioral contract:**
- `fingerprint_fixed_surfaces()` MUST filter stopwords and extract distinctive
  tokens.  Read errors MUST be silently skipped.
- `scan_for_leakage()` uses three sub-checks: token overlap (Jaccard), negation
  hints, specific value matching.
- Sensitivity thresholds: low=0.25, medium=0.15, high=0.08.
- `scan_diff_for_leakage()` MUST extract only added lines from the diff.

### 8.17 `factory/ace/reflector.py` — Experiment Reflection

**Exports:** `reflect_on_experiments()`, `update_playbook_counters()`.

**Behavioral contract:**
- MUST scan all managed projects (directory scan + registry).
- MUST require minimum data thresholds (typically 3-5 experiments) before
  generating playbook bullets.
- Missing projects and corrupt files MUST be silently skipped.
- Counter updates use fuzzy matching (term overlap + SequenceMatcher,
  threshold 0.35).

### 8.18 `factory/ace/curator.py` — Playbook Curation

**Exports:** `curate_playbook(existing, candidates, max_items=15) -> Playbook`

**Behavioral contract:**
- MUST be a pure function.
- Semantic deduplication at 0.75 similarity threshold.
- Net-negative items MUST be removed (harmful - helpful >= 3, or harmful >
  helpful with 3+ observations).
- Output MUST be capped at `max_items` by net score.
- IDs MUST be reassigned sequentially.

### 8.19 `factory/ace/injector.py` — Playbook Injection

**Exports:** `load_playbook()`, `inject_playbook()`.

**Behavioral contract:**
- Two-tier lookup: `~/.factory/playbooks/<role>.md` then
  `factory/agents/playbooks/<role>.md`.
- MUST return `None` if no playbook found or content is empty.
- `inject_playbook()` is pure string concatenation.

### 8.20 `factory/spec/parser.py` — Spec Parser

**Exports:** `parse_spec(spec_path) -> RepoSpec`

**Behavioral contract:**
- MUST raise `FileNotFoundError` if spec file is missing.
- MUST support both legacy structural format and behavioral format.
- Missing sections MUST yield empty strings/lists (never raise on malformed
  markdown).
- `RepoSpec.get_module(name)` MUST be case-insensitive.

### 8.21 `factory/spec/validate.py` — Spec Validation

**Exports:** `validate_spec(project_path) -> ValidationResult`

**Behavioral contract:**
- MUST check: path existence, import cross-references (via Haiku agent),
  orphan detection, section completeness (16 mandatory sections + Appendix A),
  entity name matching.
- Haiku agent failures MUST produce warnings, not errors.
- MUST write validation report to `.factory/spec_validation.md`.
- `ValidationResult.passed` = True when no errors (warnings are acceptable).

### 8.22 `factory/events.py` — Event System

**Exports:** `emit_event()`, `load_events()`, `sum_agent_costs()`.

**Behavioral contract:**
- `emit_event()` MUST append a timestamped JSON event to
  `.factory/events.jsonl`.  MUST create `.factory/` if needed.
- `load_events()` MUST return empty list if file missing.
- No locking on append; concurrent appends MAY interleave lines.
- Event types: `agent.started`, `agent.completed`, `agent.failed`,
  `agent.timeout`, `cycle.started`, `cycle.completed`, `experiment.begin`,
  `experiment.finalize`, `bob.ceiling_warning`, `workflow.started`,
  `workflow.completed`, `workflow.halted`, `node.started`, `node.completed`,
  `gate.verdict`, `worktree.created`, `worktree.removed`, `ceo.message`.

### 8.23 `factory/runners/protocol.py` — Runner Protocol

**Exports:** `Runner` (Protocol), `RunnerMeta` (frozen dataclass).

**Behavioral contract:**
- `is_available()` MUST check `shutil.which(binary)`.
- `check_auth()` MUST check all `required_env_vars` in `os.environ` (or
  delegate to `custom_auth_check`).
- `build_command()` MUST return `(cmd_list, env_dict, temp_files)`.
- `RunnerMeta` is frozen (immutable after construction).

### 8.24 `factory/runners/claude.py` — Claude Runner

**Behavioral contract:**
- MUST parse last JSONL line with `"result"` key for structured output.
- Temp files for prompts MUST be cleaned up in `finally` blocks.
- CEO role MUST emit `ceo.message` events.
- JSON parse failures MUST be silently skipped.

### 8.25 `factory/runners/bob.py` — Bob Shell Runner

**Behavioral contract:**
- Auth check: `BOBSHELL_API_KEY` in env > `.factory/.bob_auth` file >
  `~/.bob/settings.json`.  MUST raise `BobAuthError` if none found.
- MUST log usage to `.factory/bob_usage.jsonl` on every invocation.
- MUST check invocation ceilings before each call; MUST raise
  `CeilingExceededError` if exceeded.
- MUST emit `bob.ceiling_warning` event when <= 2 invocations remain.
- Dry-run mode (`FACTORY_BOB_DRY_RUN=1`) MUST return stub results.
- MUST persist API key to `.factory/.bob_auth` with chmod 0600.

### 8.26 `factory/runners/codex.py` — Codex Runner

**Behavioral contract:**
- Auth: `CODEX_API_KEY` > `OPENAI_API_KEY` > OAuth at `~/.codex/auth.json`.
  MUST raise `CodexAuthError` if none found.
- MUST auto-retry once on 401 Unauthorized (2s delay).
- MUST create isolated `CODEX_HOME` temp directory when using API key mode.
- MUST strip `OPENAI_API_KEY` from env when OAuth detected to avoid mode
  conflict.
- Temp directories MUST always be cleaned up.

### 8.27 `factory/runners/usage.py` — Usage Tracking

**Behavioral contract:**
- `log_usage()` MUST append JSONL to `.factory/bob_usage.jsonl`.
- `check_ceilings()` MUST raise `CeilingExceededError` if `count >= limit`.
- MUST return `CeilingWarning` if <= 2 invocations remain.
- Default ceiling: 8 per cycle.
- Malformed JSONL lines MUST be silently skipped during counting.

### 8.28 `factory/study.py` — Project Study

**Exports:** `study_project()`, `add_backlog_item()`, `remove_backlog_item()`.

**Behavioral contract:**
- MUST read Claude Code conversation logs from `~/.claude/projects/`.
- MUST run `gh search repos`, `gh issue list`, `gh api user` subprocesses;
  failures MUST return empty results.
- MUST write `.factory/strategy/observations.md` and
  `.factory/strategy/backlog.md`.
- Community issues MUST be flagged as reference-only.
- Targeted mode (`focus`) MUST restrict budget to a single item.
- Backlog items MUST be deduplicated.

### 8.29 `factory/precheck.py` — Hard Precheck Gate

**Exports:** `run_precheck() -> PreCheckResult`

**Behavioral contract:**
- `passed=True` ONLY when ALL checks pass.
- Up to 6 checks: score direction, scope guard, fixed surface guard,
  anti-pattern, hard constraints, QA execution.
- CEO CANNOT override a failed precheck.
- Anti-pattern similarity threshold: 0.6 (default).
- Hard constraint timeout: 120s (default).
- QA check verifies Sacred Rule 9 (QA must have been invoked).

### 8.30 `factory/worktree.py` — Git Worktree Management

**Exports:** `create_worktree()`, `remove_worktree()`, `prune_stale()`.

**Behavioral contract:**
- MUST create worktrees at `.factory-worktrees/run-<id>/`.
- MUST create a symlink `.factory -> <project>/.factory` (shared state).
- Branch named `factory/run-<id>`.
- Base ref MUST be resolved to commit SHA (not symbolic ref).
- `remove_worktree()` MUST be safe to call on already-removed paths.
- MUST emit `worktree.created`/`worktree.removed` events.

### 8.31 `factory/refine_state.py` — Refinement State

**Exports:** `read_state()`, `begin_refinement()`, `complete_refinement()`.

**Behavioral contract:**
- `read_state()` MUST return empty `RefinementState()` on missing file or
  parse errors.
- Only the last entry MAY be completed.
- `complete_refinement()` returns `False` if no entries or last already
  completed.

### 8.32 `factory/user_config.py` — User Configuration

**Exports:** `load_config()`, `resolve()`, `show_config()`, `migrate_env_to_config()`.

**Behavioral contract:**
- `resolve()` MUST implement 5-tier precedence: CLI flag > env var > profile
  credential > config.toml default > hardcoded default.
- `load_config(profile)` MUST inject credentials into `os.environ` via
  `setdefault`.
- Profile names MUST match `[a-zA-Z0-9_-]+`.
- Credential keys MUST match `[A-Z_][A-Z0-9_]*`.
- Sensitive keys MUST be auto-masked in `show_config()`.
- Config file MUST be created with 0600 permissions.

### 8.33 `factory/workflow/skill_export.py` — Skill Export

**Exports:** `workflow_to_skill_md()`, `export_all_skills()`, `validate_skill()`.

**Behavioral contract:**
- MUST convert `Workflow` graphs to SKILL.md with YAML frontmatter, numbered
  phases, template slots (`{{slot::default}}`), and HTML annotation comments.
- Topological sort MUST ignore RELOOP back-edges.
- Fork targets MUST be inlined under their ForkNode.
- SHOULD warn if generated skill exceeds 500 lines.
- `validate_skill()` MUST check frontmatter structure, name format (kebab-case,
  <= 64 chars), description length (<= 1024), and body length (<= 500 lines).

### 8.34 Other Modules

| Module | Key Contract |
|--------|-------------|
| `factory/insights.py` | `analyze()` MUST compute per-category stats; winning >= 80% keep rate (min 3 experiments), losing < 50%. |
| `factory/report.py` | `parse_ceo_verdicts()` extracts verdicts from `ceo-verdict-*.md` via regex.  Content truncated to 500 chars. |
| `factory/summary.py` | Summary MUST be scoped to current session via latest `cycle.started` event.  Marginal revert threshold = 0.01. |
| `factory/telemetry.py` | All operations MUST be graceful no-ops when Langfuse not configured.  `flush()` does double-flush with 1s+0.3s sleeps. |
| `factory/issue.py` | `parse_issue_ref()` MUST handle bare numbers, URLs, and `owner/repo#N`.  MUST raise `ValueError` for unparseable refs. |
| `factory/discovery/introspect.py` | Detection priority: Python > TypeScript > Rust > Go.  Missing tools return `None`/`"unknown"`. |
| `factory/discovery/profile.py` | Weights MUST sum to 1.0.  Confidence: explicit=1.0, discovered=0.8, researched=0.5, fallback=0.2. |

---

## 9 Shared Contracts

### 9.1 Pydantic Model Contract

All domain models in `factory/models.py` MUST satisfy:

```python
model_config = ConfigDict(strict=True, extra="forbid")
```

- **strict=True**: Type coercion is disabled.  A `str` field rejects `int`.
- **extra="forbid"**: Unknown fields cause `ValidationError`.

### 9.2 Runner Protocol

All runners MUST implement:

```python
class Runner(Protocol):
    def metadata(self) -> RunnerMeta: ...
    def build_command(self, request: AgentRunRequest) -> tuple[list[str], dict, list[Path]]: ...
    async def headless(self, request: AgentRunRequest) -> AgentRunResult: ...
    async def interactive_run(self, request: AgentRunRequest) -> AgentRunResult: ...
```

Runners MUST clean up temp files in `finally` blocks.  Runners MUST NOT
propagate auth errors as generic exceptions; they MUST use their specific
error type (`BobAuthError`, `CodexAuthError`).

### 9.3 Event Schema

Every event emitted to `.factory/events.jsonl` MUST contain:

```json
{
  "type": "<event_type>",
  "timestamp": "<ISO 8601>",
  "agent": "<role or null>",
  "data": {}
}
```

### 9.4 Eval Result Schema

Every eval dimension MUST return:

```json
{
  "name": "<dimension_name>",
  "score": 0.0,
  "weight": 0.0,
  "passed": false,
  "details": "<human-readable explanation>"
}
```

- `score` MUST be in `[0.0, 1.0]`.
- `weight` is relative within its tier; MUST be normalized before scoring.

### 9.5 Agent Prompt Resolution

Two-tier lookup, applied in order:
1. `.factory/agents/<role>.md` (project-specific override)
2. `factory/agents/prompts/<role>.md` (factory default)

After resolution, the ACE injector appends the evolved playbook (if any).
Profile injection (`~/.factory/profile.md`) is applied when `use_profile=True`.

---

## 10 Configuration Specification

### 10.1 User Config (`~/.factory/config.toml`)

Five-tier precedence: **CLI flag > env var > profile credential > config.toml
default > hardcoded default**.

```toml
[defaults]
runner = "claude"         # default runner backend
model = ""                # default model (empty = runner default)
projects_dir = "~/factory-projects"

[credentials.<profile>]
FACTORY_RUNNER = "..."
ANTHROPIC_API_KEY = "..."
```

- Profile names MUST match `[a-zA-Z0-9_-]+`.
- Credential keys MUST match `[A-Z_][A-Z0-9_]*`.
- Config file MUST be 0600 permissions.

### 10.2 Project Config (`factory.md` -> `.factory/config.json`)

Parsed by `ExperimentStore.reparse_config()` with section-name mapping:

| Section | Field(s) |
|---------|----------|
| `## Goal` | `goal` |
| `## Scope` | `scope[]` |
| `## Guards` | `guards[]` |
| `## Eval` | `eval_command` |
| `## Threshold` | `eval_threshold` |
| `## Constraints` | `constraints[]` |
| `## Research Target` | `research_target` (ResearchTarget) |
| `## Mutable Surfaces` | `mutable_surfaces[]` |
| `## Fixed Surfaces` | `fixed_surfaces[]` |
| `## Cost Budget` | `cost_budget` (CostBudgetConfig) |
| `## Multi-Run` | `inner_loop` (InnerLoopConfig) |
| `## Outer Loop Surfaces` | `outer_loop` (OuterLoopConfig) |
| `## Test Timeout` | `test_timeout` (int, >= 1, default 600) |
| `## Hygiene Weights` | `hygiene_weights` (TierWeights) |
| `## Growth Weights` | `growth_weights` (TierWeights) |

### 10.3 Eval Profile (`.factory/eval_profile.json`)

Generated by discovery pipeline.  Key invariants:
- `dimensions[].weight` MUST sum to 1.0.
- `human_reviewed` MUST be `false` initially.
- Setting `human_reviewed=true` is required to transition from
  `EVALS_PENDING_REVIEW` to `HAS_FACTORY`.
- `tier` MUST be one of: `explicit`, `discovered`, `researched`, `fallback`.
- `confidence` MUST be in `[0.0, 1.0]`.

---

## 11 Entry Points

### 11.1 CLI Entry Point

```
factory.cli:main -> argparse dispatcher -> cmd_* handler functions
```

The `factory` script is registered in `pyproject.toml` as
`[project.scripts] factory = "factory.cli:main"`.

### 11.2 Key CLI Commands

| Command | Handler | Trigger |
|---------|---------|---------|
| `factory ceo <path\|idea>` | `cmd_ceo` | Main orchestration entry |
| `factory run <path>` | `cmd_run` | Heartbeat wrapper for ceo |
| `factory agent <role>` | `cmd_agent` | Direct specialist invocation |
| `factory eval <path>` | `cmd_eval` | Run composite eval |
| `factory detect <path>` | `cmd_detect` | Show project state |
| `factory workflow run <name>` | `cmd_workflow_run` | Headless workflow execution |
| `factory spec generate <path>` | `cmd_spec_generate` | Generate GRAPH-SPEC.md |
| `factory precheck <path>` | `cmd_precheck` | Hard precheck gate |

### 11.3 Workflow Entry Points

13 registered workflows, each selectable by project state and context flags:

| Workflow | Start Node | Trigger |
|----------|-----------|---------|
| `build` | `fork_research` | `NO_REPO \| REPO_INCOMPLETE` |
| `design` | `fork_research` | `NO_REPO + interactive` |
| `discover` | `discover` | `NO_FACTORY` |
| `review` | `eval_test` | `EVALS_PENDING_REVIEW` |
| `improve` | `study` | `HAS_FACTORY` |
| `research` | `baseline` | `HAS_FACTORY + research_target` |
| `meta` | `insights` | `mode=meta` |
| `refine` | `refiner` | `HAS_FACTORY + refine` |
| `qa` | `qa` | `mode=qa` |
| `create` | `fork_research` | `mode=create` |
| `skill-refine` | `dag_sort` | `mode=skill-refine` |
| `spec-generate` | `extract` | Internal (no trigger) |
| `spec-update` | `diff_scope` | Internal (no trigger) |

---

## 12 Failure Model and Recovery

### 12.1 Custom Exception Hierarchy

| Exception | Module | Condition | Recovery |
|-----------|--------|-----------|----------|
| `ResultParseError` | `factory/models.py` | Missing file, invalid JSON, missing key, non-numeric value, NaN/Inf, zero denominator | Caller returns ERROR status |
| `BobAuthError` | `factory/runners/bob.py` | No API key found | User must set `BOBSHELL_API_KEY` |
| `CodexAuthError` | `factory/runners/codex.py` | No API key or OAuth | User must set `CODEX_API_KEY` |
| `CeilingExceededError` | `factory/runners/usage.py` | Invocation ceiling breached | Increase `FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE` |
| `ConsecutiveAgentFailureError` | `factory/agents/runner.py` | 2+ consecutive spawn failures | Cycle aborted; investigate agent/runner health |

### 12.2 Error Handling Patterns

| Pattern | Where Used | Behavior |
|---------|-----------|----------|
| Subprocess failures wrapped in result objects | `eval/runner.py`, `eval/hygiene.py` | Never propagate; return neutral/zero score |
| Graceful None returns | `checkpoint.py`, `obsidian/notes.py` | Missing/corrupt optional resources |
| Event emission on failure | `events.py` | `agent.failed`, `agent.timeout`, `bob.ceiling_warning` |
| Filelock for concurrent access | `store.py` | TSV append, experiment ID allocation |
| Atomic writes (tmp + rename) | `registry.py`, `checkpoint.py`, `store.py` | Crash-safe persistence |
| Swallowed non-critical errors | `registry.py` updates, telemetry | Side effects that MUST NOT block the main flow |

### 12.3 Crash Recovery

- **Checkpoint:** `save_checkpoint()` persists CEO state to
  `.factory/checkpoint.json`.  `load_checkpoint()` returns `None` on
  corruption (never raises).  `factory resume` reads the checkpoint and
  restarts the CEO from the saved state.
- **CycleState:** `.factory/state/cycle.json` preserves mode across CEO
  respawns within a single cycle.
- **Filelock:** `store.py` uses `FileLock` on `.factory/.store.lock` to prevent
  concurrent TSV corruption and experiment ID races.
- **Worktree isolation:** Builder agents MAY run in git worktrees
  (`.factory-worktrees/`) for parallel isolation.  The `.factory` directory is
  symlinked back to the main project.

---

## 13 Security and Safety

### 13.1 Sacred Rules (CEO)

The CEO agent MUST enforce these nine rules.  Violation of any Sacred Rule
MUST halt the current workflow.

1. **Never skip eval.** Every experiment MUST have before/after eval scores.
2. **Never merge without QA.** Builder output MUST pass QA review before merge.
3. **Never exceed cost budget.** `cost_budget.max_per_cycle` and `max_total`
   MUST be respected.
4. **Always run guards.** `check_all()` MUST be invoked before finalization.
5. **Always check preconditions.** State detection MUST precede mode selection.
6. **Never modify fixed surfaces.** Files matching `fixed_surfaces` patterns
   MUST NOT be changed.
7. **Always archive.** The Archivist MUST be spawned after every experiment
   (non-blocking is acceptable).
8. **CEO doesn't implement.** The CEO MUST delegate code changes to the Builder.
9. **QA verification required.** The precheck gate verifies that a `qa`
   agent event exists for the current experiment.

### 13.2 Auth Credential Handling

- Bob Shell API key MUST be stored at `.factory/.bob_auth` with chmod 0600.
- Codex API key mode MUST use an isolated `CODEX_HOME` temp directory.
- `OPENAI_API_KEY` MUST be stripped from env when Codex OAuth is detected.
- Config file `~/.factory/config.toml` MUST be created with 0600 permissions.
- Sensitive keys MUST be auto-masked in `factory config show`.
- Profile name validation MUST reject path traversal and spaces.
- Credential key validation MUST enforce uppercase env var naming.

### 13.3 Data Leakage Prevention (Research Mode)

- `validate_research_config()` checks for mutable/fixed surface overlap.
- `fingerprint_fixed_surfaces()` extracts distinctive tokens from ground truth.
- `scan_for_leakage()` detects token overlap, negation hints, and specific
  value leakage in agent outputs.
- `scan_diff_for_leakage()` scans only added lines in git diffs.
- `create_run_dir()` MUST reject path traversal in cycle_id.

### 13.4 Scope Enforcement

- `check_scope()` validates that all changed files match `scope[]` patterns.
- `check_fixed_surfaces()` validates that no file matching `fixed_surfaces[]`
  was modified.
- Guards run as git-diff analysis; they do not prevent writes, only detect
  violations post-hoc.

---

## 14 Test and Validation Matrix

### 14.1 Test Infrastructure

- **Framework:** pytest with pytest-asyncio (`asyncio_mode = "auto"`)
- **Fixtures:** `tmp_project`, `sample_config`, `python_project` in
  `tests/conftest.py`
- **Isolation:** Autouse `_isolate_registry` fixture redirects global registry
  to temp directory

### 14.2 Structural Invariants (Tested)

| Invariant | Test |
|-----------|------|
| Every workflow with a Builder MUST have a reachable QA | Parametric graph test |
| Build mode fork MUST have exactly 3 parallel researchers | Node count assertion |
| Design mode MUST be structurally identical to build except gate evaluator | Diff assertion |
| All 13 workflows MUST be registered in `register_all()` | Registration count test |
| Skill export MUST produce valid SKILL.md for every workflow | `validate_skill()` test |

### 14.3 Eval Validation

- Hygiene tier MUST always return exactly 6 dimensions.
- Growth tier MUST always return exactly 6 dimensions.
- Dimension weights within each tier MUST sum to 1.0.
- `CompositeScore.total` MUST be in `[0.0, 1.0]`.

---

## 15 Extension Points

### 15.1 Runner Plugins

New runners can be registered via the `factory.runners` entry_point group in
`pyproject.toml`.  The `_RUNNERS` dict in `factory/runners/__init__.py`
provides the default registry; entry_point plugins extend it.

### 15.2 Workflow Registration

New workflows are added by:
1. Writing a function that returns a `Workflow` in `definitions.py`.
2. Adding it to `register_all()`.
3. Adding a `WORKFLOW_META` entry in `skill_export.py`.
4. Wiring `--mode` in `cli.py`.

### 15.3 Agent Role Extension

New agent roles are added by:
1. Adding to the `AgentRole` enum in `primitives.py`.
2. Creating a prompt file at `factory/agents/prompts/<role>.md`.
3. Optionally creating a playbook at `factory/agents/playbooks/<role>.md`.

### 15.4 Eval Dimension Extension

Project-specific eval dimensions can be added via:
- `## Eval Spec` section in `factory.md` (parsed as `ProjectEvalDimension[]`).
- Custom `eval/score.py` scripts (subprocess execution, JSON output).

### 15.5 Language Adapters

New language support is added by implementing the adapter interface in
`factory/eval/languages/` following the pattern of `python.py`, `go.py`,
`node.py`, and `rust.py`.

---

## 16 Implementation Checklist

### 16.1 Adding a New Workflow Mode

- [ ] Define workflow function in `factory/workflow/definitions.py`
- [ ] Register in `register_all()`
- [ ] Add `WORKFLOW_META` entry in `factory/workflow/skill_export.py`
- [ ] Wire `--mode` choice in `factory/cli.py` (`build_parser`)
- [ ] Add routing in `cmd_ceo` and `_build_ceo_task`
- [ ] Run `factory workflow validate <name>`
- [ ] Run `factory workflow export-skills`
- [ ] Write tests (graph validation, skill export, trigger, registration)
- [ ] Verify `pytest` and `ruff check` pass

### 16.2 Adding a New Runner

- [ ] Create `factory/runners/<name>.py` implementing `Runner` protocol
- [ ] Define `RunnerMeta` with binary name, env vars, and auth check
- [ ] Register in `_RUNNERS` dict or via entry_point
- [ ] Implement `headless()` and `build_command()`
- [ ] Add auth error type
- [ ] Add dry-run mode support
- [ ] Write tests
- [ ] Document in CLAUDE.md

### 16.3 Adding a New Agent Role

- [ ] Add to `AgentRole` enum in `factory/workflow/primitives.py`
- [ ] Create `factory/agents/prompts/<role>.md`
- [ ] Optionally create `factory/agents/playbooks/<role>.md`
- [ ] Wire into workflow definition(s) as `AgentNode`
- [ ] Verify `resolve_prompt()` finds the new role

---

## Appendix A: Reference Algorithms

### A.1 FEEC Classification

```
Input: hypothesis text, experiment history
Output: FEECCategory (FIX=0, EXPLOIT=1, EXPLORE=2, COMBINE=3)

1. Lowercase the hypothesis text
2. Match keywords in priority order:
   FIX:     {"fix", "bug", "crash", "fail", "broken", "error"}
   EXPLOIT: {"improve", "increase", "enhance", "optimize", "refactor"}
   COMBINE: {"combine", "merge", "integrate", "unify"}
3. First match wins
4. Default: EXPLORE
```

### A.2 Composite Score Computation

```
Input: list[EvalResult], guard_violations, threshold
Output: CompositeScore

1. If no results: total = 0.0
2. Normalize weights: w_i = w_i / sum(all w)
3. total = sum(r.score * r.normalized_weight for r in results)
4. passed = (len(guard_violations) == 0) AND (total >= threshold)
```

### A.3 Stuck Detection

```
Input: experiment history, threshold (default 3)
Output: bool

1. Walk history in reverse
2. Track consecutive reverts with same FEEC category
3. A "keep" verdict resets the streak
4. Return True if streak >= threshold
```

### A.4 Hypothesis Similarity (Jaccard)

```
Input: two hypothesis texts
Output: float in [0.0, 1.0]

1. Tokenize: split on whitespace, keep tokens with len >= 3
2. Build sets A, B from tokens
3. Return |A intersect B| / |A union B|
4. Return 0.0 if both sets empty
```

### A.5 Playbook Curation

```
Input: existing Playbook, candidate PlaybookItems, max_items (default 15)
Output: curated Playbook

1. Merge: deduplicate candidates against existing (0.75 similarity threshold)
2. Prune: remove items where (harmful - helpful >= 3) OR
          (harmful > helpful AND observations >= 3)
3. Rank: sort by net_score (helpful - harmful) descending
4. Cap: take top max_items
5. Reassign sequential IDs
```

### A.6 Eval Weight Normalization

```
Input: tier results with raw weights, optional TierWeights overrides
Output: normalized results

1. Apply overrides: if TierWeights has a non-None value for a dimension,
   replace that dimension's weight
2. Compute sum of all weights in the tier
3. Normalize: w_i = w_i / sum
4. Guarantee: sum of normalized weights == 1.0
```
