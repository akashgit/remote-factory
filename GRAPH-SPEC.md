# GRAPH-SPEC — Remote Factory Behavioral Specification

> Normative language follows [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119). Terms in **bold** at first use are defined in §6 Domain Model.

---

## §1 Problem Statement

Software projects accumulate technical debt, miss best practices, and stagnate without continuous, disciplined improvement. Human-driven improvement cycles are expensive, inconsistent, and bandwidth-limited.

The Remote Factory solves this by providing an **autonomous software improvement engine** — a four-layer system that detects a project's state, discovers evaluation dimensions, formulates improvement hypotheses, implements them via specialist agents, and verifies results through non-overridable quality gates. The system operates as a directed-graph workflow engine where each mode (build, improve, research, refine, etc.) is a typed DAG of agent nodes, function nodes, and gate nodes executed deterministically.

---

## §2 Goals and Non-Goals

### §2.1 Goals

1. Autonomously improve any software project through hypothesis-driven experiment cycles
2. Enforce non-overridable quality gates (precheck) that prevent regressions
3. Support multiple CLI backends (Claude Code, Bob Shell, Codex, OpenCode) via a runner abstraction
4. Evolve agent behavior over time through cross-project playbook learning (ACE)
5. Provide 13 workflow modes as composable, validated DAGs with formal execution semantics
6. Maintain full experiment history with append-only TSV and per-experiment artifact directories

### §2.2 Non-Goals

1. Direct API calls to LLM providers — the factory spawns CLI subprocesses exclusively
2. Real-time collaboration or multi-user concurrency on a single project
3. Replacement of human judgment on architectural decisions — the factory defers Tier 3 refinements

### §2.3 Design Philosophy

- **Hypothesis-driven**: Every change is an experiment with before/after eval, a verdict, and archival
- **Non-overridable gates**: The precheck gate cannot be bypassed by the CEO agent; failure means mandatory revert
- **Composable workflows**: Modes are DAGs built from 6 primitive node types, reusable via `subgraph()`
- **Self-improvement**: ACE pipeline evolves per-agent playbooks from cross-project experiment data
- **Fail-fast**: Consecutive agent failures (threshold=2) abort the cycle; corrupt state returns safe defaults

---

## §3 Project Identity

| Field | Value |
|---|---|
| Name | remote-factory |
| Language | Python 3.11+ |
| Type | CLI tool + agent orchestration engine |
| Package manager | uv |
| Entry point | `factory.cli:main` (registered as `factory` script) |
| Test runner | pytest (asyncio_mode=auto) |
| Linter | ruff |
| Type checker | mypy |
| Logging | structlog (stderr) |

---

## §4 Technical Stack

| Layer | Technology | Purpose |
|---|---|---|
| CLI framework | argparse (`_GroupedHelpParser`) | 70+ subcommands in 8 groups |
| Models | Pydantic v2 (strict, extra=forbid) | All domain types |
| Async runtime | asyncio | Workflow executor, eval runner, subprocess management |
| Concurrency | filelock (`FileLock`) | Safe concurrent experiment ID allocation and TSV append |
| Graph validation | networkx | Reachability, cycle detection, read/write consistency |
| Observability | Langfuse (optional) | Hierarchical span tracing with transcript ingestion |
| Dashboard | FastAPI + SSE | Real-time project monitoring on port 8420 |
| Notifications | Telegram Bot API | Experiment digest delivery |
| Knowledge store | Obsidian vault (optional) | Experiment notes, project dashboards, strategy archives |
| Configuration | TOML (`~/.factory/config.toml`) | Five-tier precedence resolution |

---

## §5 Architecture Overview

The factory is a four-layer system:

### Layer 1: Python CLI (`factory/`)

Pure tools that do not make decisions. Entry point `factory/cli.py` dispatches via a handler dict to `cmd_*` functions organized in CLI module files (`cli/ceo.py`, `cli/admin.py`, `cli/store.py`, etc.). The CLI layer MUST NOT contain agent decision logic.

### Layer 2: Workflow Graph Engine (`factory/workflow/`)

All 13 factory modes are defined as directed graphs of typed nodes in `factory/workflow/definitions.py`. Each graph is a `Workflow` Pydantic model with `AgentNode`, `FnNode`, `GateNode`, `ForkNode`, `JoinNode`, and `Study` primitives connected by `Edge` objects.

The same graph definition produces two execution formats:
- **Headless**: `WorkflowExecutor` (`factory/workflow/executor.py`) walks the DAG deterministically
- **Interactive**: `skill_export.py` converts graphs to Claude Code `SKILL.md` files under `skills/workflow-*/`

### Layer 3: CEO Agent

The CEO prompt is split into core identity (`ceo.md`) and mode-specific playbooks (`skills/workflow-*/SKILL.md`). The CEO detects project state, reads the appropriate SKILL.md, and follows it as the mode-specific playbook.

### Layer 4: Specialist Agents (`factory/agents/`)

Eight specialist subprocesses spawned by the CEO via `factory agent <role>`. Agent prompts use a two-tier lookup: project override (`.factory/agents/<role>.md`) then factory default (`factory/agents/prompts/<role>.md`). ACE-evolved playbooks are auto-injected.

### Module Dependency Graph

```
factory/models.py                    ← Foundation: all Pydantic types
    ├── factory/state.py             ← 5-state project detection
    ├── factory/store.py             ← Experiment lifecycle (FileLock)
    ├── factory/eval/
    │   ├── runner.py                ← Mandatory 12 dimensions + project eval
    │   ├── hygiene.py               ← 6 hygiene dimensions (multi-language)
    │   ├── growth.py                ← 6 growth dimensions
    │   ├── scorer.py                ← Weighted composite computation
    │   ├── guards.py                ← Git/scope/surface/immutability checks
    │   └── languages/{python,node,go,rust}.py  ← Per-language evaluators
    ├── factory/precheck.py          ← 6 non-overridable checks
    ├── factory/strategy.py          ← FEEC heuristic, plateau/stuck detection
    ├── factory/workflow/
    │   ├── primitives.py            ← 6 node types, Edge, Verdict, Workflow
    │   ├── definitions.py           ← 13 workflow DAGs
    │   ├── executor.py              ← Async DAG walker
    │   ├── validation.py            ← Graph validation (networkx)
    │   ├── skill_export.py          ← DAG → SKILL.md conversion
    │   ├── guard.py                 ← Slot/annotation integrity guard
    │   └── registry.py              ← Workflow discovery (builtin/user/project)
    ├── factory/agents/
    │   ├── runner.py                ← Agent invocation + failure tracking
    │   └── plugin.py                ← Agent file generation + sync checking
    ├── factory/ace/
    │   ├── reflector.py             ← Cross-project bullet generation
    │   ├── curator.py               ← 3-phase playbook pruning
    │   ├── injector.py              ← Playbook → prompt injection
    │   └── paths.py                 ← 2-tier path resolution
    └── factory/runners/
        ├── protocol.py              ← Runner interface + RunnerMeta
        ├── claude.py                ← Claude Code backend (default)
        ├── bob.py                   ← Bob Shell backend + ceiling enforcement
        ├── codex.py                 ← OpenAI Codex backend
        └── opencode.py              ← OpenCode backend
```

---

## §6 Domain Model

### §6.1 Core Enumerations

| Entity | Values | Description |
|---|---|---|
| **ProjectState** | `no_repo`, `incomplete`, `no_factory`, `evals_pending_review`, `has_factory` | Five-state project lifecycle |
| **VerdictType** | `proceed`, `reloop`, `halt` | Gate evaluation outcomes |
| **AgentRole** | `researcher`, `strategist`, `builder`, `qa`, `failure_analyst`, `ceo`, `archivist`, `refiner`, `skill_reviewer` | 9 specialist roles |
| **FEECCategory** | `FIX=0`, `EXPLOIT=1`, `EXPLORE=2`, `COMBINE=3` | Hypothesis priority (lower = higher) |
| **RunStatus** | `PASS`, `FAIL`, `ERROR`, `TIMEOUT` | Research run outcomes |
| **AggregateMethod** | `mean`, `median`, `max`, `all_pass` | Multi-run metric aggregation |

### §6.2 Configuration Models

All models use `ConfigDict(strict=True, extra="forbid")` — extra fields MUST raise `ValidationError`.

| Entity | Key Fields | Invariants |
|---|---|---|
| **FactoryConfig** | `goal`, `scope`, `guards`, `eval_command`, `eval_threshold`, `hypothesis_budget`, `research_target`, `mutable_surfaces`, `fixed_surfaces`, `hard_constraints`, `clean_pr` | `test_timeout` ≥ 1; `research_target` nullable |
| **EvalProfile** | `project_type`, `dimensions[]`, `tier`, `confidence`, `human_reviewed` | `human_reviewed` defaults `false`; tier ∈ {explicit, discovered, researched, fallback} |
| **HypothesisBudget** | `min_growth`, `max_new` | Controls backlog-first allocation |
| **ResearchTarget** | `objective`, `metric`, `target`, `run_command`, `result_path`, `timeout` | `result_parser` MUST be `"json"` |
| **InnerLoopConfig** | `runs_per_cycle`, `aggregate`, `plateau_threshold` | `runs_per_cycle` ≥ 1 |
| **HardConstraint** | `name`, `check`, `description` | Shell command; exit 0 = pass |

### §6.3 Experiment Models

| Entity | Key Fields | Invariants |
|---|---|---|
| **ExperimentRecord** | `id`, `timestamp`, `hypothesis`, `verdict`, `score_before`, `score_after`, `delta`, `cost_usd`, `research_citations` | `verdict` ∈ {keep, revert, error} |
| **CompositeScore** | `total`, `results[]`, `guard_violations`, `passed` | Weighted sum of `EvalResult` entries |
| **EvalResult** | `name`, `score`, `weight`, `passed`, `details` | `score` ∈ [0.0, 1.0] |

### §6.4 Workflow Primitives

| Entity | Key Fields | Invariants |
|---|---|---|
| **Node** (base) | `id`, `reads`, `writes`, `blocking` | All nodes inherit these |
| **AgentNode** | `role`, `model`, `prompt_template`, `timeout` | Spawns a specialist agent |
| **FnNode** | `command`, `callable_name` | Runs a deterministic shell command |
| **GateNode** | `evaluator_type`, `evaluator_role`, `gate_prompt` | `evaluator_type` ∈ {agent, fn, user} |
| **ForkNode** | `targets[]` | Launches all targets concurrently |
| **JoinNode** | `sources[]` | Barrier — waits for all sources |
| **Study** | Inherits FnNode + `focus` | Wraps `factory study` |
| **Edge** | `source`, `target`, `condition` | `condition` nullable; when set ∈ VerdictType |
| **Verdict** | `type`, `target`, `feedback`, `max_iterations`, `reason` | RELOOP requires target; HALT requires reason |

---

## §7 State Machines and Lifecycles

### §7.1 Project State Detection

```
detect_state(path) →
  !exists or !.git               → NO_REPO
  eval_profile.json[human_reviewed=false] → EVALS_PENDING_REVIEW
  .factory/config.json exists    → HAS_FACTORY
  .git + open 'plan' issues      → REPO_INCOMPLETE
  .git, no open issues           → NO_FACTORY
```

The factory MUST check `EVALS_PENDING_REVIEW` before `HAS_FACTORY` to handle the discover → review → init flow.

### §7.2 Experiment Lifecycle

```
store.init() → store.begin(hypothesis) → [exp_id allocated, FileLock]
  → save_eval(exp_id, "before") → Builder implements
  → save_eval(exp_id, "after")  → save_diff(exp_id)
  → finalize(exp_id, record)    → [verdict.json + TSV append, FileLock]
  → registry.update_project_stats()
```

- `begin()` MUST use `FileLock` for concurrent ID allocation
- `finalize()` MUST compute `delta = score_after - score_before` when not preset
- `finalize()` MUST register project in global registry

### §7.3 Workflow Execution

```
WorkflowExecutor.execute() →
  _execute_from(start_node) →
    ForkNode  → asyncio.gather(branch_targets) → follow next
    JoinNode  → increment nodes_executed → follow next
    GateNode  → _evaluate_gate → Verdict:
      PROCEED → follow proceed edge
      RELOOP  → check iteration_counts[(gate_id, target)]
                if < max_iterations → inject feedback → _execute_from(target)
                if ≥ max_iterations → HALT
      HALT    → set halted=True, record reason
    AgentNode/FnNode/Study →
      if blocking: execute synchronously → follow next
      if non-blocking: asyncio.Task → follow next immediately
```

- The executor MUST wait for all background tasks (30s timeout) before returning
- The executor MUST track `iteration_counts` per `(gate_id, target)` pair
- Gate feedback MUST be accumulated in `node_context` across iterations

### §7.4 CEO Completion Guard

```
run_with_completion_guard() →
  check existing cycle_state → restore mode + runner
  OR create new CycleState → persist to cycle.json
  → invoke CEO → check exit code
  → user interrupt (signal 130/143/128+) → preserve cycle state, return
  → explicit ABORT event → delete cycle state, return
  → _detect_incomplete():
    improve/research: verdict_count < hypothesis_count → incomplete
    build: phase_count < total_phases → incomplete
    discover: no eval_profile.json → incomplete
  → if incomplete: _build_continuation_task → respawn (max 5)
  → if cap hit: write cycle-incomplete.md, return error
```

- The guard MUST NOT respawn when `FACTORY_CEO_RESPAWN_DISABLED=1`
- Cycle state older than 24 hours MUST be treated as stale (ignored)
- Continuation tasks MUST include explicit mode override preventing mode flipping

### §7.5 Precheck Gate (Non-Overridable)

```
run_precheck() →
  1. check_score_direction  — no regression, meets threshold
  2. check_scope           — changed files within allowed scope
  3. check_surfaces        — no modifications to fixed surfaces
  4. check_anti_pattern    — hypothesis not similar to reverted experiments (Jaccard ≥ 0.6)
  5. check_hard_constraints — user-defined shell commands exit 0
  6. check_qa_execution    — QA agent was invoked (Sacred Rule 9)
  → ANY failure = mandatory revert; CEO MUST NOT override
```

- When verdict is `keep` but precheck fails, finalize MUST override to `revert` and emit `verdict.overridden` event
- `--force` flag bypasses hard constraint gate only

### §7.6 ACE Pipeline (Playbook Evolution)

```
Reflect → scan all projects → load histories → compute category stats
  → generate candidate bullets per role
Update Counters → fuzzy-match hypothesis text against bullets
  → increment helpful (keep) or harmful (revert)
Curate → remove net-negative items (harmful > helpful, ≥3 observations)
  → semantic dedup (SequenceMatcher ≥ 0.75)
  → cap at max_items=15 by net score
Inject → append playbook section to agent prompt at invocation time
Persist → write to ~/.factory/playbooks/<role>.md
```

### §7.7 Worktree Lifecycle

```
create_worktree(project_path, base_branch, run_id)
  → resolve base_branch to SHA
  → git worktree add .factory-worktrees/run-<id>
  → symlink .factory/ into worktree
  → emit worktree.created event
run completes →
remove_worktree() → rmtree → git worktree prune → git branch -D
  → emit worktree.removed event
crash →
prune_stale() → list active worktrees → remove orphans
```

### §7.8 Message Queue

```
write_message(text) → validate non-empty, ≤10K chars, <20 pending
  → write timestamped .md to .factory/messages/
read_pending() → sorted chronological, cap at 20 messages, 50K total chars
mark_read(id) → move to messages/read/ subdirectory (idempotent)
```

- Symlinks and path traversal MUST be rejected

---

## §8 Module Specifications

### §8.1 `factory/state.py` — Project State Detection

| Contract | Normative |
|---|---|
| `detect_state` returns one of 5 `ProjectState` values | MUST |
| Check `EVALS_PENDING_REVIEW` before `HAS_FACTORY` | MUST |
| Only `plan` label signals unbuilt repo (not `implementation`) | MUST |
| `_has_open_plan_issues` timeout at 15s | SHOULD |
| Graceful on `gh` CLI unavailable | MUST |

### §8.2 `factory/store.py` — Experiment Store

| Contract | Normative |
|---|---|
| `init` creates `.factory/` with `experiments/`, `strategy/`, `agents/`, `reviews/`, `config.json`, `results.tsv` | MUST |
| `begin` uses `FileLock` for concurrent ID allocation | MUST |
| `begin` auto-registers project in global registry | MUST |
| `finalize` uses `FileLock` for TSV append | MUST |
| `finalize` computes delta when not pre-set | MUST |
| `load_history` handles missing `research_citations` column (backward compat) | MUST |
| `read_config` uses `strict=False` for enum coercion from JSON | MUST |
| `reparse_config` parses `factory.md` sections, HTML comments, code blocks, list continuations | MUST |
| `ensure_factory_dir` removes broken/circular symlinks before mkdir | MUST |

### §8.3 `factory/eval/runner.py` — Eval Runner

| Contract | Normative |
|---|---|
| Compute 6 mandatory hygiene + 6 mandatory growth dimensions | MUST |
| Default weight split: 50% hygiene / 50% growth (no project eval) | MUST |
| With project eval: configurable, default 30/20/50 | MUST |
| `_normalize_tier` rescales weights to target sum | MUST |
| Sparse within-tier overrides applied before normalization | SHOULD |
| Save results to `.factory/last_eval.json` | SHOULD |

### §8.4 `factory/precheck.py` — Precheck Gate

| Contract | Normative |
|---|---|
| A single failure makes the entire precheck fail | MUST |
| The CEO MUST NOT override a failed precheck | MUST |
| `check_score_direction`: `None` scores → fail | MUST |
| `check_anti_pattern`: Jaccard threshold default 0.6 | MUST |
| `check_qa_execution`: requires event after `experiment.begin` | MUST |
| `check_qa_execution`: skipped when `exp_id=None` | MUST |
| Hard constraint timeout: 120s default | SHOULD |

### §8.5 `factory/strategy.py` — FEEC Heuristic

| Contract | Normative |
|---|---|
| `categorize_hypothesis`: keyword match, FIX first | MUST |
| `detect_stuck`: True when N consecutive reverts share a FEEC category | MUST |
| `detect_plateau`: True when last N scored experiments do not exceed running best | MUST |
| `hypothesis_similarity`: Jaccard on tokens ≥3 chars | MUST |
| `format_tiered_history`: Tier 1 (last 3) full, Tier 2 (4-10) one-line, Tier 3 aggregate | MUST |

### §8.6 `factory/agents/runner.py` — Agent Runner

| Contract | Normative |
|---|---|
| Two-tier prompt lookup: project override → factory default | MUST |
| Auto-inject playbook from ACE | MUST |
| Emit `agent.started`/`completed`/`failed` events | MUST |
| Consecutive failure threshold = 2 → raise `ConsecutiveAgentFailureError` | MUST |
| Save agent output to `.factory/reviews/<role>-latest.md` | MUST |
| Append `IDENTITY_REANCHOR` to non-CEO outputs | MUST |

### §8.7 `factory/workflow/primitives.py` — Workflow Primitives

| Contract | Normative |
|---|---|
| `Verdict` RELOOP requires `target` | MUST (validator) |
| `Verdict` HALT requires `reason` | MUST (validator) |
| `Workflow.validate_graph()` delegates to networkx | MUST |
| `Workflow.subgraph()` deep-copies nodes and filters edges | MUST |
| `Factory.select_workflow` iterates workflows, returns first matching trigger | MUST |
| `DEFAULT_AGENT_POOL`: 9 entries with role-specific model and timeout defaults | MUST |

### §8.8 `factory/workflow/executor.py` — Workflow Executor

| Contract | Normative |
|---|---|
| Follow edges based on verdict condition matching | MUST |
| Track `iteration_counts[(gate_id, target)]` for reloop limits | MUST |
| Accumulate feedback in `node_context` across iterations | MUST |
| Non-blocking nodes run as `asyncio.Task` | MUST |
| Wait for background tasks (30s timeout) at end of execution | MUST |
| `_parse_agent_verdict`: last non-empty line determines verdict | MUST |
| `_parse_fn_verdict`: JSON `{passed: bool}` or text parsing | MUST |
| Dry-run mode returns stub output without spawning processes | MUST |
| `_wait_for_reads`: 60s timeout polling at 100ms intervals | MUST |

### §8.9 `factory/workflow/definitions.py` — Workflow Definitions

| Contract | Normative |
|---|---|
| 13 workflows registered via `register_all()` | MUST |
| W₁ Build: trigger on `NO_REPO` or `REPO_INCOMPLETE` | MUST |
| W₂ Design: W₁ with user gate at strategy approval | MUST |
| W₃ Improve: trigger on `HAS_FACTORY` | MUST |
| W₃b QA: subgraph of W₃ via `subgraph()` | MUST |
| W₄ Research: extends W₃ with baseline, failure_analyst, plateau gate | MUST |
| W₅ Meta: insights → playbook evolution → test pruning | MUST |
| W₆ Discover: trigger on `NO_FACTORY` | MUST |
| W₇ Review: trigger on `EVALS_PENDING_REVIEW` | MUST |
| W₈ Refine: Tier 3 halts early | MUST |
| W₉ Create: meta-mode for new factory modes | MUST |
| Spec gates: GRAPH-SPEC.md existence checked; generated if absent | MUST |
| Spec update gate: strategy MUST include GRAPH-SPEC Diff when spec exists | MUST |

### §8.10 `factory/eval/guards.py` — Guard Rules

| Contract | Normative |
|---|---|
| `check_eval_immutable`: `eval/` directory MUST NOT be modified | MUST |
| `check_git_clean`: working tree MUST be clean (ignoring lock files) | MUST |
| `check_scope`: changed files MUST be within declared scope | MUST |
| `check_fixed_surfaces`: fixed surface files MUST NOT be modified | MUST |
| `_glob_match`: `**` matches across directory boundaries; `*` does not | MUST |
| Auto-generated lock files MUST be ignored | MUST |

### §8.11 `factory/ace/` — Playbook Evolution

| Contract | Normative |
|---|---|
| Fuzzy matching: key-term overlap ≥0.4 OR SequenceMatcher ≥0.35 | MUST |
| Net-negative pruning: harmful > helpful with ≥3 observations | MUST |
| Semantic dedup: SequenceMatcher ≥0.75 | MUST |
| Cap at `max_items=15` per role | MUST |
| Two-tier path resolution: user-local → factory default | MUST |

### §8.12 `factory/runners/` — Runner Abstraction

| Contract | Normative |
|---|---|
| Resolution order: explicit name → `FACTORY_RUNNER` env var → `"claude"` | MUST |
| Each runner implements `Runner` protocol (name, metadata, build_command, headless, interactive_run) | MUST |
| Bob Shell ceiling enforcement via `FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE` | MUST |
| Codex auto-retry on 401 Unauthorized (once, 2s delay) | SHOULD |
| Dry-run modes: `FACTORY_BOB_DRY_RUN`, `FACTORY_CODEX_DRY_RUN`, `FACTORY_OPENCODE_DRY_RUN` | MUST |

---

## §9 Shared Contracts

### §9.1 Event Protocol

All events MUST be appended to `.factory/events.jsonl` as newline-delimited JSON with fields: `type`, `timestamp` (ISO 8601), `project`, `agent` (nullable), `data` (dict).

Event types: `agent.started`, `agent.completed`, `agent.failed`, `agent.timeout`, `cycle.started`, `cycle.completed`, `cycle.aborted`, `experiment.begin`, `experiment.finalize`, `verdict.overridden`, `worktree.created`, `worktree.removed`, `ceo.message`, `bob.ceiling_warning`.

### §9.2 File I/O Contracts

- `emit_event` MUST create `.factory/events.jsonl` and `.factory/` directory if absent
- `ensure_factory_dir` MUST remove broken/circular symlinks before mkdir
- All file writes to `.factory/` SHOULD handle `OSError` gracefully
- Symlink resolution: emitting via symlink MUST write to the resolved real path

### §9.3 Pydantic Model Contract

All domain models MUST use `ConfigDict(strict=True, extra="forbid")`. Extra fields MUST raise `ValidationError`. All models MUST support JSON roundtrip serialization.

---

## §10 Configuration Specification

### §10.1 Five-Tier Precedence

```
CLI flag > env var > profile credential > config.toml [defaults] > hardcoded default
```

### §10.2 Config File (`~/.factory/config.toml`)

```toml
[defaults]
runner = "claude"           # Default runner backend
projects_dir = "~/factory-projects"

[credentials.vertex]
FACTORY_RUNNER = "claude"
ANTHROPIC_API_KEY = "sk-ant-..."
```

- Profile names MUST match `[a-zA-Z0-9_-]+`
- Credential keys MUST match `[A-Z_][A-Z0-9_]*`
- Config file MUST be created with 0600 permissions (atomic, `O_CREAT | O_EXCL`)
- Sensitive keys (containing "key", "token", "secret", "password") MUST be masked in `show_config`

### §10.3 Project Config (`factory.md` → `.factory/config.json`)

`ExperimentStore.reparse_config()` parses `factory.md` markdown into `FactoryConfig`. Sections map via `section_map` dict. Code blocks, HTML comments, and list continuations are handled.

---

## §11 Entry Points

| Entry Point | Mechanism | Purpose |
|---|---|---|
| `factory` CLI | `pyproject.toml` script → `factory.cli:main` | Primary user interface |
| `python -m factory` | `factory/__main__.py` | Alternative invocation |
| `factory ceo /path` | CLI → completion guard → agent subprocess | Orchestrate improvement cycle |
| `factory run /path --loop` | Heartbeat wrapper around CEO | Continuous improvement |
| `factory agent <role>` | CLI → `invoke_agent()` → runner subprocess | Direct specialist invocation |
| `factory workflow run <name>` | CLI → `WorkflowExecutor` | Headless DAG execution |
| `factory dashboard` | FastAPI server on :8420 | Web monitoring UI |
| `factory serve-mcp` | MCP stdio server | 4 tools: score, experiments, status, projects |

---

## §12 Failure Model and Recovery

### §12.1 Error Types

| Error | Module | Trigger | Recovery |
|---|---|---|---|
| `ConsecutiveAgentFailureError` | `agents/runner.py` | 2+ consecutive failures | Abort cycle |
| `ValidationError` (Pydantic) | `models.py` | Extra fields, wrong types | Raise to caller |
| `ResultParseError` | `models.py` | Unparseable research result | Return ERROR status |
| `RuntimeError` | `executor.py` | Shell/agent non-zero exit | Halt workflow |
| `RuntimeError` | `executor.py` | Max gate iterations exhausted | Halt workflow |
| `ValueError` | `messages.py` | Empty text, oversized, too many pending | Raise to caller |
| `ValueError` | `issue.py` | Unparseable issue reference | Raise to caller |
| `FileNotFoundError` | `agents/runner.py` | Missing prompt file | Raise to caller |
| `BobAuthError` | `runners/bob.py` | No API key | Raise to caller |
| `CodexAuthError` | `runners/codex.py` | No API key or OAuth | Raise to caller |
| `CeilingExceededError` | `runners/usage.py` | Bob invocation limit hit | Abort with actionable message |

### §12.2 Recovery Patterns

| Scenario | Recovery |
|---|---|
| CEO premature exit | Completion guard detects incomplete work → auto-respawn (max 5) |
| Stale cycle state (>24h) | Ignored; fresh cycle created |
| Corrupt `cycle.json` | Returns None (no crash) |
| Corrupt `config.json` | Raises `ValueError` with "Run 'factory init --reparse'" message |
| Corrupt `results.tsv` | Invalid verdict values coerced to `"error"` |
| Missing `eval_profile.json` | Returns None; discovery mode triggered |
| Background task failure | Logged as warning; does not halt workflow |
| Worktree crash | `prune_stale()` cleans orphaned worktrees on next run |
| `gh` CLI unavailable | Graceful fallback (empty results, skipped checks) |
| Langfuse unavailable | Silent no-op; tracing disabled |

### §12.3 Precheck Failure Recovery

When `verdict="keep"` but precheck fails:
1. Override verdict to `"revert"`
2. Emit `verdict.overridden` event
3. Record `"OVERRIDDEN"` in experiment notes

This is non-overridable by the CEO agent.

---

## §13 Security and Safety

| Control | Implementation |
|---|---|
| API key isolation | Config file at 0600 permissions; secrets masked in `show_config` |
| Path traversal prevention | Message queue rejects symlinks and `..` traversal |
| Command injection mitigation | `shlex.quote()` for project paths in shell commands |
| Fixed surface protection | Precheck gate blocks modifications to declared fixed surfaces |
| Ground truth leakage detection | Fingerprinting + token overlap + negation hint + specific value checks |
| Bob usage ceiling | Hard limit on invocations per cycle (`FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE`) |
| Scope enforcement | Guard checks restrict changes to declared scope patterns |
| QA execution mandate | Sacred Rule 9: QA agent MUST be invoked for every experiment |

---

## §14 Test and Validation Matrix

| Module | Test File(s) | Key Assertions |
|---|---|---|
| `factory/models.py` | `test_models.py`, `test_inner_outer_loop.py` | Strict validation, JSON roundtrip, enum coercion |
| `factory/eval/runner.py` | `test_runner.py`, `test_eval_weights.py` | 12 mandatory dimensions, weight normalization |
| `factory/eval/hygiene.py` | `test_hygiene.py`, `test_hygiene_characterization.py`, `test_hygiene_architecture.py` | Per-language scoring, weight invariants, architecture neutral scores |
| `factory/eval/growth.py` | `test_eval_growth.py`, `test_growth.py` | 6 growth dimensions, 50/50 merge, neutral defaults |
| `factory/eval/guards.py` | `test_guards.py` | Glob matching, scope enforcement, surface protection |
| `factory/precheck.py` | `test_precheck.py`, `test_hard_constraints.py` | Non-overridable gate, all 6 check types |
| `factory/store.py` | `test_integration.py` | Full experiment lifecycle |
| `factory/strategy.py` | `test_precheck.py` | FEEC categorization, similarity, anti-patterns |
| `factory/agents/runner.py` | `test_agents.py` | Prompt resolution, failure abort, parallel invocation |
| `factory/agents/plugin.py` | `test_plugin_agents.py` | 7 roles, sync checking, install paths |
| `factory/ace/` | `test_ace.py`, `test_ace_counters.py`, `test_ace_paths.py` | Playbook roundtrip, fuzzy matching, pruning |
| `factory/workflow/` | `test_annotations.py` | Graph validation, skill export parity |
| `factory/workflow/guard.py` | `test_guard.py` | Slot/annotation integrity |
| `factory/events.py` | `test_events.py`, `test_event_enrichment.py` | Append-only log, worktree events, enrichment |
| `factory/insights.py` | `test_insights.py` | 13 categories, cross-project patterns |
| `factory/issue.py` | `test_issue.py` | GitHub/GitLab parsing, remote inference |
| `factory/messages.py` | `test_messages.py` | Queue constraints, read lifecycle |
| `factory/research/leakage.py` | `test_leakage.py` | Fingerprinting, sensitivity levels |
| `factory/runners/` | `test_background.py` | Session lifecycle, cleanup |

---

## §15 Extension Points

| Extension Point | Mechanism | Description |
|---|---|---|
| Custom runners | `factory.runners` entry point group | Register new CLI backends via `importlib.metadata` |
| Project agent overrides | `.factory/agents/<role>.md` | Per-project prompt customization |
| Custom workflows | `.factory/workflows/` or registered search paths | Project-specific workflow definitions loaded dynamically |
| Hard constraints | `factory.md` `## Hard Constraints` section | User-defined shell checks enforced at precheck |
| Project eval dimensions | `factory.md` `## Project Eval` section | User-defined eval commands |
| Eval spec items | `factory.md` `## Eval Spec` section | Auto-promoted to project eval dimensions |
| Within-tier weight overrides | `factory.md` `## Hygiene Weights` / `## Growth Weights` | Sparse weight adjustment per dimension |
| Playbook evolution | `~/.factory/playbooks/<role>.md` | ACE-evolved behavioral rules |
| Obsidian vault | `FACTORY_VAULT_PATH` env var | Knowledge export destination |
| Notification backends | `Notifier` protocol | Currently: Telegram; extensible |

---

## §16 Implementation Checklist

### §16.1 Invariants That MUST Hold

- [ ] All Pydantic models use `ConfigDict(strict=True, extra="forbid")`
- [ ] `ExperimentStore` uses `FileLock` for `begin()` and `finalize()`
- [ ] Precheck gate is non-overridable by the CEO agent
- [ ] All 13 workflows validate cleanly via `validate_graph()`
- [ ] Weight sums: hygiene weights sum to 1.0, growth weights sum to 1.0
- [ ] FEEC priority order: FIX < EXPLOIT < EXPLORE < COMBINE
- [ ] Consecutive agent failure threshold = 2
- [ ] Max CEO respawns = 5
- [ ] Cycle staleness threshold = 24 hours
- [ ] Anti-pattern Jaccard similarity threshold = 0.6
- [ ] ACE semantic dedup threshold = 0.75
- [ ] ACE max items per role = 15

### §16.2 Workflow-Specific Invariants

- [ ] W₁ Build: Phase 1 MUST be scaffold + eval harness
- [ ] W₂ Design: gate_strategy MUST be user evaluator
- [ ] W₃b QA: gate_qa MUST HALT (not RELOOP to builder) on failure
- [ ] W₄ Research: QA MUST verify mutable/fixed surface compliance
- [ ] W₅ Meta: Archivist MUST be non-blocking
- [ ] W₈ Refine: Tier 3 MUST halt early
- [ ] W₁₀ Skill Refine: guard max 2 reloops, then fallback to unrefined
- [ ] All workflows with spec gates: MUST generate GRAPH-SPEC.md if absent

---

## Appendix A: Reference Algorithms

### A.1 FEEC Hypothesis Categorization

```python
def categorize_hypothesis(text: str) -> FEECCategory:
    text_lower = text.lower()
    # Priority order: FIX > EXPLOIT > COMBINE > EXPLORE
    if any(kw in text_lower for kw in FIX_KEYWORDS):
        return FEECCategory.FIX
    if any(kw in text_lower for kw in EXPLOIT_KEYWORDS):
        return FEECCategory.EXPLOIT
    if any(kw in text_lower for kw in COMBINE_KEYWORDS):
        return FEECCategory.COMBINE
    return FEECCategory.EXPLORE
```

### A.2 Hypothesis Similarity (Jaccard)

```python
def hypothesis_similarity(a: str, b: str) -> float:
    tokens_a = {w for w in a.lower().split() if len(w) >= 3}
    tokens_b = {w for w in b.lower().split() if len(w) >= 3}
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
```

### A.3 Weight Normalization

```python
def _normalize_tier(results, target_weight, overrides=None):
    if overrides:
        results = [r.copy(weight=overrides.get(r.name, r.weight)) for r in results]
    weight_sum = sum(r.weight for r in results)
    if weight_sum <= 0:
        return results
    return [r.copy(weight=(r.weight / weight_sum) * target_weight) for r in results]
```

### A.4 Plateau Detection

```python
def detect_plateau(history, window=3):
    scored = [r for r in history if r.score_after is not None]
    if len(scored) < window:
        return False
    recent = scored[-window:]
    best_before = max(r.score_after for r in scored[:-1]) if len(scored) > 1 else 0
    return all(r.score_after <= best_before for r in recent)
```

### A.5 Verdict Parsing (Executor)

The executor parses the **last non-empty line** of agent output:
- Line starts with `HALT` → `Verdict.halt(reason)` (parsed from `REASON="..."`)
- Line starts with `RELOOP` → `Verdict.reloop(target, feedback)` (parsed from `TARGET="..."`, `FEEDBACK="..."`)
- Otherwise → `Verdict.proceed()`

For function output: JSON `{passed: bool}` → proceed/halt; text containing `fail`/`revert` → halt; `reloop` → reloop if RELOOP edge exists.
