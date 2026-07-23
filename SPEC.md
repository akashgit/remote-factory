# Remote Factory — Behavioral Specification

**Version:** 0.2.0
**Status:** Living document — updated automatically by the factory's spec-update workflow.

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD",
"SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be
interpreted as described in [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

---

## §1 Problem Statement

Software projects accumulate technical debt, stale tests, unimplemented backlog
items, and configuration drift. Humans address these incrementally, but the
process is expensive, error-prone, and difficult to sustain at scale.

Remote Factory is an **autonomous software evolution harness** that detects a
project's state, delegates improvement work to specialist AI agents, evaluates
the results against quantitative dimensions, and archives the outcomes — all
in a continuous loop requiring minimal human intervention.

The system operates on any git-managed codebase. It MUST NOT require the target
project to adopt specific frameworks, languages, or conventions beyond a
`factory.md` configuration file and a `.factory/` data directory.

---

## §2 Goals and Non-Goals

### §2.1 Goals

1. **Autonomous improvement** — the factory MUST be able to run unsupervised,
   making measurable improvements to a target project's eval score.
2. **Multi-modal operation** — the factory MUST support building new projects
   from scratch, improving existing ones, conducting research experiments,
   evolving its own agent playbooks, and generating documentation/specifications.
3. **Quantitative evaluation** — every change MUST be scored against a composite
   eval comprising hygiene, growth, and project-specific dimensions before a
   keep/revert verdict is issued.
4. **Knowledge accumulation** — experiment outcomes, patterns, and decisions
   MUST be archived for cross-project learning and anti-pattern avoidance.
5. **Runner agnosticism** — the factory MUST support multiple CLI backends
   (Claude Code, Bob Shell, OpenAI Codex, OpenCode) as interchangeable runners.
6. **Observability** — all agent invocations, gate verdicts, and cycle
   transitions MUST be emitted as structured events to `events.jsonl`.

### §2.2 Non-Goals

- The factory MUST NOT call LLM APIs directly; it delegates exclusively
  through CLI subprocess runners.
- The factory is not a CI/CD pipeline; it does not deploy artifacts.
- The factory does not manage infrastructure or cloud resources.

### §2.3 Design Philosophy

- **Tools don't decide** — Layer 1 (Python CLI) provides pure functions and
  data stores. All decisions are made by Layer 3 (CEO agent) reading Layer 2
  (workflow graphs).
- **Graphs are the source of truth** — every operational mode is defined as a
  directed graph of typed nodes. The same graph produces both headless execution
  and interactive skill playbooks.
- **Fail loud, recover gracefully** — agent failures increment a consecutive
  failure counter; exceeding the threshold aborts the cycle with a structured
  event rather than allowing the CEO to fall back to doing work itself.
- **FEEC priority** — hypothesis ranking follows Fix > Exploit > Explore >
  Combine ordering. The strategist MUST NOT submit hypotheses that violate
  this priority without explicit justification.

---

## §3 Project Identity

| Field | Value |
|---|---|
| Name | `remote-factory` |
| Entry point | `factory.cli:main` (registered as `factory` in pyproject.toml) |
| Language | Python 3.11+ |
| Package manager | uv |
| Build system | Hatchling |
| Test framework | pytest + pytest-asyncio (asyncio_mode = auto) |
| Linter | ruff (100-char line length) |
| Type checker | mypy |
| Logging | structlog |
| Models | Pydantic v2 (strict=True, extra="forbid" on all models) |
| License | MIT |

---

## §4 Technical Stack

| Layer | Technology | Purpose |
|---|---|---|
| Domain models | Pydantic v2 | Strict schema validation for all config, eval, experiment, and workflow data |
| Workflow graphs | NetworkX | DAG validation (cycle detection, reachability, connectivity) |
| Subprocess orchestration | asyncio + subprocess | Agent invocation, shell command execution, streaming output |
| Concurrency control | filelock | Safe concurrent TSV append and experiment ID allocation |
| Telemetry | Langfuse | Distributed tracing across agent spans within a cycle |
| Dashboard | FastAPI + SSE | Live web UI for multi-project monitoring |
| Knowledge graph | graphify | AST-derived code structure for spec generation |
| Notifications | Telegram (optional) | Experiment digest delivery |
| Obsidian integration | Custom templates | Vault-based experiment note generation |

---

## §5 Architecture Overview

The factory is a four-layer system:

### Layer 1: Python CLI (`factory/`)

Pure tools and data stores. The CLI MUST NOT make autonomous decisions. Each
subcommand maps to a `cmd_*` handler dispatched via a dict in
`factory/cli/__init__.py`. Key subsystems:

- **Store** (`factory/store.py`) — manages `.factory/` directory lifecycle,
  experiment creation, TSV history, config parsing.
  [[graph:factory.store]]
- **Eval** (`factory/eval/`) — computes hygiene, growth, and project dimensions.
  [[graph:factory.eval.runner]]
- **Strategy** (`factory/strategy.py`) — FEEC categorization, plateau detection,
  anti-pattern matching.
  [[graph:factory.strategy]]
- **Registry** (`factory/registry.py`) — global project tracking at
  `~/.factory/registry.json`.
  [[graph:factory.registry]]

### Layer 2: Workflow Graph Engine (`factory/workflow/`)

All operational modes are defined as directed graphs of typed nodes in
`factory/workflow/definitions.py`. The engine provides two execution formats:

- **Headless:** `WorkflowExecutor` walks the DAG deterministically.
  [[graph:factory.workflow.executor]]
- **Interactive:** `skill_export.py` converts graphs to SKILL.md playbooks
  for the CEO agent.
  [[graph:factory.workflow.skill_export]]

### Layer 3: CEO Agent

The CEO prompt (`factory/agents/prompts/ceo.md`) combined with mode-specific
SKILL.md playbooks. Spawned via `factory ceo` or `factory run`. The CEO
MUST NOT implement code, run tests, or do research directly — it orchestrates
and delegates exclusively.

### Layer 4: Specialist Agents (`factory/agents/`)

Eight specialist roles resolved via two-tier prompt lookup:
project override (`.factory/agents/<role>.md`) then factory default
(`factory/agents/prompts/<role>.md`). Evolved playbooks from
`~/.factory/playbooks/<role>.md` are auto-injected.
[[graph:factory.agents.runner]]

---

## §6 Domain Model

### §6.1 Entity Relationships

```
FactoryConfig ──1:1──> ResearchTarget (optional)
     │                  InnerLoopConfig (optional)
     │                  OuterLoopConfig (optional)
     │                  AdversarialConfig (optional)
     │                  ParallelConfig (optional)
     │                  CostBudgetConfig (optional)
     │
     ├──1:N──> HardConstraint
     ├──1:N──> ProjectEvalDimension
     └──1:1──> HypothesisBudget
              EvalWeights
              TierWeights (hygiene, growth)

ExperimentStore ──1:N──> ExperimentRecord
     │
     └──1:1──> EvalProfile ──1:N──> EvalDimension
                                    ProjectProfile

CompositeScore ──1:N──> EvalResult

ProjectRegistry ──1:N──> ProjectEntry

AdversarialConfig ──1:1──> AdversarialComponent (generator)
                   1:1──> AdversarialComponent (discriminator)
AdversarialState ──1:N──> AdversarialPhaseRecord

Workflow ──1:N──> Node (AgentNode | FnNode | GateNode | ForkNode |
                        JoinNode | SubgraphForkNode | SelectionNode | Study)
         1:N──> Edge

CrossProjectInsights ──1:N──> ProjectSummary
                      1:N──> HypothesisOutcome
                      1:N──> Pattern
```

[[graph:factory.models]]

### §6.2 Key Domain Types

| Type | Location | Invariants |
|---|---|---|
| `ProjectState` | `models.py` | Enum of exactly 5 values; detection order matters (§7.1) |
| `FactoryConfig` | `models.py` | MUST round-trip through `factory.md` → `config.json` without loss |
| `ExperimentRecord` | `models.py` | Verdict MUST be one of `keep`, `revert`, `error`, `superseded` |
| `CompositeScore` | `models.py` | `total` MUST equal weighted sum of individual `EvalResult.score * weight` |
| `Workflow` | `primitives.py` | Graph MUST pass NetworkX validation (no orphans, reachable exit) |
| `Verdict` | `primitives.py` | Algebraic type: `Proceed | Reloop(target, feedback, max) | Halt(reason)` |

---

## §7 State Machines and Lifecycles

### §7.1 Project State Machine

Detection is performed by `factory/state.py:detect_state()`. The order of
checks is load-bearing — EVALS_PENDING_REVIEW MUST be checked before
HAS_FACTORY.

```
                    ┌─────────────────────────────────────────┐
                    │                                         │
  ┌──────────┐     │  ┌───────────────┐   ┌──────────────┐  │  ┌─────────────┐
  │ NO_REPO  │────>│  │ REPO_         │   │ NO_FACTORY   │──│─>│ EVALS_      │
  │          │     │  │ INCOMPLETE    │   │              │  │  │ PENDING_    │
  └──────────┘     │  └───────┬───────┘   └──────┬───────┘  │  │ REVIEW     │
       │           │          │ (build)          │ (discover)│  └──────┬──────┘
       │ (create)  │          │                  │           │         │ (review)
       └───────────┤          v                  v           │         v
                   │  ┌───────────────────────────────────┐  │  ┌─────────────┐
                   │  │            HAS_FACTORY            │<─┘  │ HAS_FACTORY │
                   │  │     (improve / research / meta)   │<────│  (reviewed) │
                   │  └───────────────────────────────────┘     └─────────────┘
                   └──────────────────────────────────────────────────────────┘
```

**Transitions:**

| From | To | Trigger |
|---|---|---|
| `NO_REPO` | `REPO_INCOMPLETE` | `factory ceo <idea>` creates repo with `plan` issue |
| `REPO_INCOMPLETE` | `HAS_FACTORY` | Build workflow completes |
| `NO_FACTORY` | `EVALS_PENDING_REVIEW` | Discover workflow generates `eval_profile.json` |
| `EVALS_PENDING_REVIEW` | `HAS_FACTORY` | Review workflow sets `human_reviewed: true` and creates `config.json` |
| `HAS_FACTORY` | `HAS_FACTORY` | Improve/Research/Meta cycles (steady state) |

### §7.2 Experiment Lifecycle

```
begin(hypothesis)          save_eval("before")     save_eval("after")
     │                          │                        │
     v                          v                        v
  ┌──────┐    ┌──────────┐   ┌──────┐   ┌──────────┐  ┌──────────┐
  │ OPEN │───>│ BASELINE │──>│ BUILD│──>│ QA       │─>│ FINALIZE │
  └──────┘    │ SCORED   │   │      │   │ VERIFIED │  │          │
              └──────────┘   └──────┘   └──────────┘  └────┬─────┘
                                                           │
                                              ┌────────────┼────────────┐
                                              v            v            v
                                          ┌──────┐    ┌────────┐  ┌───────┐
                                          │ KEEP │    │ REVERT │  │ ERROR │
                                          └──────┘    └────────┘  └───────┘
```

The `ExperimentStore` MUST use `filelock` for concurrent ID allocation and TSV
append. The `begin()` method MUST register the project in the global registry.
The `finalize()` method MUST update registry stats.

### §7.3 Adversarial Eval Loop

When `adversarial` config is present, the factory alternates between
optimizing a generator and discriminator component:

```
  ┌───────────┐  score >= threshold   ┌──────────────────┐
  │ GENERATOR │  for N consecutive    │  DISCRIMINATOR   │
  │  ACTIVE   │─────────────────────> │  ACTIVE          │
  │           │ <─────────────────────│                  │
  └───────────┘  score >= threshold   └──────────────────┘
       │              for N consecutive         │
       │                                        │
       └──────────── both streaks ──────────────┘
                     >= convergence_window
                           │
                           v
                    ┌──────────────┐
                    │  CONVERGED   │
                    └──────────────┘
```

Phase switching MUST use hysteresis (default: 3 consecutive rounds above
threshold). Convergence requires BOTH per-role streak counters to independently
reach the convergence window. [[graph:factory.adversarial]]

### §7.4 Workflow Execution

The `WorkflowExecutor` walks a DAG of typed nodes:

1. For each node, it MUST wait until all declared `reads` are in the
   `completed_files` set (timeout: 60s).
2. `AgentNode` — invokes an agent via `factory/agents/runner.py`.
3. `FnNode` — runs a shell command with `{project_path}` substitution.
4. `GateNode` — evaluates to a `Verdict` (Proceed/Reloop/Halt).
   - `Reloop` MUST respect `max_iterations` per (gate, target) pair.
   - The executor MUST track iteration counts and halt on exhaustion.
5. `ForkNode` — runs all targets concurrently via `asyncio.gather`.
6. `JoinNode` — barrier that passes through when all sources complete.
7. `SubgraphForkNode` — forks N copies of a subgraph into isolated worktrees.
8. `SelectionNode` — compares parallel results and merges the winner.
9. Non-blocking nodes (`blocking=False`) MUST be dispatched as background
   tasks and MUST NOT block the main execution path.

[[graph:factory.workflow.executor]]

---

## §8 Module Specifications

### §8.1 Workflow Definitions (`factory/workflow/definitions.py`)

This module defines all operational modes as Python functions returning
`Workflow` objects. Each workflow MUST have:
- A unique `name`
- A `start_node` that exists in `nodes`
- A `trigger` function mapping `(ProjectState, context) -> bool`
- A valid DAG (passes `validate_graph()`)

**Registered workflows** (via `register_all()`):

| ID | Name | Trigger | Description |
|---|---|---|---|
| W₁ | `build` | NO_REPO or REPO_INCOMPLETE | New project from idea/spec |
| W₂ | `design` | W₁ trigger + interactive context | W₁ with user gate at strategy |
| W₃ | `improve` | HAS_FACTORY | Study → research → strategy → build/QA loop |
| W₃b | `qa` | mode=qa | Standalone deep-QA PR verification |
| W₄ | `research` | HAS_FACTORY + research_target | W₃ + baseline, failure analyst, plateau gate |
| W₅ | `meta` | mode=meta | Cross-project insights → playbook evolution |
| W₆ | `discover` | NO_FACTORY | Auto-discover eval dimensions |
| W₇ | `review` | EVALS_PENDING_REVIEW | Verify evals, create factory.md, baseline |
| W₈ | `refine` | HAS_FACTORY + refine context | Lightweight user-directed refinement |
| W₉ | `create` | mode=create | Meta-mode for creating new factory modes |
| W₁₀ | `skill-refine` | mode=skill-refine | Verified skill generation pipeline |
| W₁₁ | `doc-generate` | (no trigger) | Generate documentation from scratch |
| W₁₂ | `doc-update` | (no trigger) | Update docs based on git diff |
| W₁₃ | `spec-generate` | (no trigger) | Extract and annotate behavioral spec |
| W₁₄ | `spec-update` | (no trigger) | Patch spec based on scoped diff |
| W₁₅ | `parallel-improve` | HAS_FACTORY + mode=parallel-improve | Fork N hypotheses into worktrees |
| W₁₆ | `deep-qa` | mode=deep-qa | Standalone deep-QA workflow |

Plus contributed benchmark workflows: `swebench`, `legacybench`,
`featurebench`, `programbench`, `terminalbench`, `tomswe`.

[[graph:factory.workflow.definitions]]

### §8.2 Deep-QA Subgraph

The deep-QA verification pipeline is a reusable subgraph shared by W₁, W₃,
W₄, W₈, W₉, and W₁₅:

```
health_checker → code_reviewer → gate_review → adversarial_tester
```

The `gate_review` node MUST short-circuit to halt when `CRITICAL_FOUND`
appears in the code review output. The adversarial tester has a 1800s timeout
(3x the default) to allow thorough testing.

### §8.3 Eval System (`factory/eval/`)

The eval system computes a `CompositeScore` from three tiers of dimensions:

1. **Hygiene** (6 mandatory): tests, lint, type_check, coverage,
   config_parser, architecture. Computed by `factory/eval/hygiene.py`.
2. **Growth** (5 mandatory): capability_surface, experiment_diversity,
   observability, research_grounding, factory_effectiveness.
   Computed by `factory/eval/growth.py`.
3. **Project** (user-defined): configured via `project_eval` in `factory.md`
   or auto-promoted from `eval_spec`.

Weight distribution:
- Without project eval: 50% hygiene + 50% growth.
- With project eval: configurable, default 30% hygiene + 20% growth + 50% project.

Within-tier weight overrides (`TierWeights`) MUST be applied before
normalization. The composite MUST be saved to `.factory/last_eval.json`
after computation.

[[graph:factory.eval.runner]]
[[graph:factory.eval.hygiene]]
[[graph:factory.eval.growth]]

### §8.4 Strategy Engine (`factory/strategy.py`)

**FEEC categorization** uses keyword matching against hypothesis text:
- FIX (priority 0): fix, error, bug, crash, fail, regression, broken, repair
- EXPLOIT (priority 1): improve, increase, extend, enhance, optimize, boost
- EXPLORE (priority 2): default catch-all
- COMBINE (priority 3): combine, merge, integrate, unify, consolidate

**Stuck detection** returns `True` when the last N consecutive reverts share
the same FEEC category.

**Plateau detection** returns `True` when the last N experiments show no
improvement in `score_after` over the running best.

**Anti-pattern matching** uses Jaccard similarity (threshold: 0.6) to find
reverted experiments similar to a proposed hypothesis.

**3-tier history compression:**
- Tier 1 (last 3): full detail
- Tier 2 (4–10 back): one-line summaries
- Tier 3 (11+): aggregate statistics

[[graph:factory.strategy]]

### §8.5 Agent Runner (`factory/agents/runner.py`)

The runner MUST:
1. Resolve prompts via two-tier lookup (project override → factory default).
2. Auto-inject ACE playbooks from `~/.factory/playbooks/<role>.md`.
3. Inject SKILL.md for CEO agents when `workflow_mode` is set.
4. Track consecutive failures and abort after 2 consecutive agent spawn
   failures (raising `ConsecutiveAgentFailureError`).
5. Save agent output to `.factory/reviews/<role>-latest.md`.
6. Emit `agent.started`, `agent.completed`, and `agent.failed` events.
7. Begin/complete Langfuse spans for telemetry when configured.

Successful invocations MUST reset the failure counter. The `FACTORY_NO_GITHUB`
environment variable MUST append a "GitHub Disabled" directive to the prompt.

[[graph:factory.agents.runner]]

### §8.6 Runner Abstraction (`factory/runners/`)

All runners implement the `Runner` protocol:

```python
class Runner(Protocol):
    def build_command(request: AgentRunRequest) -> tuple[list[str], dict, list]: ...
    def interactive_run(request: AgentRunRequest) -> AgentRunResult: ...
    def metadata() -> RunnerMeta: ...
    async def headless(request: AgentRunRequest) -> AgentRunResult: ...
```

Implementations:
- **ClaudeRunner** — Claude Code CLI (`claude`). Default. Supports background dispatch.
- **BobRunner** — Bob Shell CLI (`bob`). Token ceiling enforcement via
  `FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE`.
- **CodexRunner** — OpenAI Codex CLI (`codex`). Uses `codex exec` in headless mode.
- **OpenCodeRunner** — OpenCode CLI (`opencode`). Requires Go binary, not npm fork.

Runner selection: `FACTORY_RUNNER` env var or `--runner` CLI flag. Default: `claude`.

[[graph:factory.runners.protocol]]
[[graph:factory.runners.claude]]

### §8.7 Experiment Store (`factory/store.py`)

The `ExperimentStore` manages the `.factory/` directory:

- `init()` MUST create `config.json`, `results.tsv` (with header), and
  subdirectories `experiments/`, `strategy/`, `agents/`, `reviews/`.
- `reparse_config()` MUST re-read `factory.md`, regenerate `config.json`, and
  handle all config sections including research target, inner/outer loop,
  adversarial, parallel, hard constraints, and tier weights.
- `begin()` MUST use `filelock` for safe concurrent ID allocation.
- `finalize()` MUST compute delta if not provided, write `verdict.json`,
  append to `results.tsv` under lock, and update registry stats.
- `load_history()` MUST handle backward-compatible TSV parsing (missing
  columns, invalid verdicts coerced to "error").

[[graph:factory.store]]

### §8.8 Global Registry (`factory/registry.py`)

The registry at `~/.factory/registry.json` uses a self-registration pattern:
- `register_project()` is called from `ExperimentStore.begin()`.
- `update_project_stats()` is called from `ExperimentStore.finalize()`.
- Registration is idempotent — duplicate paths are silently skipped.
- Atomic save via tmp file + rename.
- `populate_from_directory()` provides migration from directory scanning.

[[graph:factory.registry]]

### §8.9 ACE Playbook System (`factory/ace/`)

The Autonomous Continuous Evolution engine:
- **Reflector** (`reflector.py`) — analyzes `PerformanceReport` to generate
  playbook diffs (DO/DON'T bullet additions/removals with evidence).
- **Curator** (`curator.py`) — three-phase pruning: net-negative removal
  (harmful > helpful after 3+ observations), semantic deduplication
  (SequenceMatcher threshold: 0.75), capacity capping.
- **Injector** (`injector.py`) — splices evolved playbook bullets into agent
  prompts at a designated injection point.

[[graph:factory.ace.curator]]
[[graph:factory.ace.injector]]

### §8.10 Discovery Pipeline (`factory/discovery/`)

- **Introspect** (`introspect.py`) — detects language, framework, project type,
  test/lint/type-check commands, and package manager.
- **Profile** (`profile.py`) — builds a `ProjectProfile` from introspection.
- **Generate** (`generate.py`) — produces `eval_profile.json` and `eval/score.py`.
- **Eval Spec** (`eval_spec.py`) — generates starter `eval_spec` items based on
  project type and framework.

[[graph:factory.discovery.introspect]]

### §8.11 Telemetry (`factory/telemetry.py`)

Langfuse integration for distributed tracing:
- `is_enabled()` lazily initializes the client.
- `begin_trace()` / `end_trace()` bracket a factory cycle.
- `begin_span()` / `end_span()` bracket individual agent invocations.
- `TranscriptTailer` — daemon thread that tails Claude Code JSONL transcripts
  and incrementally ingests tool calls as Langfuse observations.
- `ingest_transcript_to_span()` parses full transcripts into span observations.

[[graph:factory.telemetry]]

### §8.12 Research Infrastructure (`factory/research/`)

- **Runner** (`runner.py`) — executes `run_command` N times, aggregates metrics,
  persists artifacts to `.factory/research/runs/<cycle_id>/`.
- **Leakage** (`leakage.py`) — checks agent output for ground truth leakage
  against fixed surface fingerprints.
- `create_run_dir()` MUST reject path traversal attempts (slashes, `..`).

[[graph:factory.research.runner]]

### §8.13 Cross-Project Insights (`factory/insights.py`)

- `discover_projects()` — finds factory-managed projects by scanning for
  `.factory/results.tsv`.
- `load_all_histories()` — loads experiment records from all discovered projects.
- `classify_hypothesis()` — categorizes hypotheses into 13 categories via
  keyword matching (feature, bugfix, testing, lint, coverage, performance,
  refactoring, type_safety, observability, infrastructure, prompt_engineering,
  agent_improvement, eval_improvement).

[[graph:factory.insights]]

---

## §9 Shared Contracts

### §9.1 Eval Output Contract

The project's `eval/score.py` MUST output JSON to stdout:

```json
{
  "results": [
    {
      "name": "dimension_name",
      "score": 0.85,
      "weight": 0.4,
      "passed": true,
      "details": "human-readable explanation"
    }
  ]
}
```

`score` MUST be in [0.0, 1.0]. `weight` is relative within the project tier.

### §9.2 Agent Review File Contract

After each agent invocation, the runner saves output to
`.factory/reviews/<role>-latest.md` with a standard header:

```markdown
# <Role> Agent Output

- **timestamp:** 2026-07-23T12:00:00Z
- **exit_code:** 0

---

<agent stdout>
```

Non-CEO agents have an identity re-anchor block appended (Sacred Rule 8).

### §9.3 Gate Verdict Contract

Gate agents MUST output one of three verdict forms as their last non-empty line:

```
PROCEED
RELOOP target="<node_id>" feedback="<message>"
HALT reason="<message>"
```

`FnNode` gates output `PROCEED`, `FAIL: <reason>`, `REVERT`, or
`RELOOP: <feedback>` as the first line of stdout.

### §9.4 Events Contract

All events are appended to `.factory/events.jsonl` as one JSON object per line.
Event types include: `agent.started`, `agent.completed`, `agent.failed`,
`agent.timeout`, `cycle.started`, `cycle.completed`, `cycle.aborted`,
`bob.ceiling_warning`, and all `workflow.*` / `node.*` / `gate.*` events.

### §9.5 Results TSV Contract

`results.tsv` is append-only with 13 tab-separated columns:

```
id  timestamp  hypothesis  change_summary  issue_number  pr_number
score_before  score_after  delta  verdict  cost_usd  notes  research_citations
```

The `research_citations` column uses `|` as delimiter. Missing values are
empty strings. Rows MUST be appended under `filelock`.

---

## §10 Configuration Specification

### §10.1 `factory.md`

The user-facing configuration file at the project root. Parsed by
`ExperimentStore.reparse_config()` using heading-based section extraction.
Section name mapping normalizes headings (e.g., `## Command` → `eval_command`,
`## Threshold` → `eval_threshold`).

Required sections: `Goal`, `Scope`, `Guards`, `Command`, `Threshold`, `Constraints`.

Optional sections: `Hypothesis Budget`, `Smoke Test`, `Project Eval`,
`Eval Weights`, `Research Target`, `Inner Loop` / `Multi-Run`, `Outer Loop` /
`Surface Scoping`, `Mutable Surfaces`, `Fixed Surfaces`, `Research Constraints`,
`Cost Budget`, `Hard Constraints`, `Eval Spec`, `Hygiene Weights`,
`Growth Weights`, `Adversarial`, `Parallel`, `Clean PR`, `Test Timeout`.

### §10.2 `~/.factory/config.toml`

User-level configuration with five-tier precedence:
CLI flag > env var > profile credential > config.toml default > hardcoded default.

Credential profiles (`[credentials.<name>]`) are loaded via `--profile <name>`
and injected into `os.environ`.

### §10.3 Target Project `.factory/` Layout

```
.factory/
├── config.json               # Machine-readable config (FactoryConfig)
├── eval_profile.json         # Discovered eval dimensions (EvalProfile)
├── results.tsv               # Append-only experiment history
├── last_eval.json            # Latest composite score
├── performance_report.json   # Consolidated data for ACE
├── adversarial_state.json    # GAN-style loop state
├── trace_id.txt              # Current Langfuse trace ID
├── .store.lock               # filelock for concurrent access
├── experiments/
│   └── NNN/                  # hypothesis.md, eval_{before,after}.json,
│                             # changes.diff, verdict.json
├── strategy/                 # observations.md, current.md, backlog.md,
│                             # insights.md, research.md, research-*.md
├── reviews/                  # <role>-latest.md, ceo-verdict-<role>.md
├── archive/                  # Long-term knowledge (experiments/, patterns/,
│                             # decisions/, sources/)
├── agents/                   # Per-project agent prompt overrides
├── research/
│   └── runs/<cycle_id>/      # stdout, stderr, summary.json per run
├── state/
│   └── cycle.json            # In-flight cycle state (CycleState)
└── events.jsonl              # Structured event log
```

---

## §11 Entry Points

### §11.1 CLI Entry Points

| Command | Handler | Description |
|---|---|---|
| `factory ceo <path\|idea>` | `cli.ceo.cmd_ceo` | Spawn the CEO agent |
| `factory run <path>` | `cli.ceo.cmd_run` | Heartbeat wrapper with `--loop` |
| `factory agent <role>` | `cli.agents.cmd_agent` | Invoke a specialist directly |
| `factory eval <path>` | `cli.eval_cmds.cmd_eval` | Run eval and print composite score |
| `factory init <path>` | `cli.admin.cmd_init` | Parse factory.md → config.json |
| `factory discover <path>` | `cli.admin.cmd_discover` | Auto-discover eval dimensions |
| `factory study <path>` | `cli.admin.cmd_study` | Analyze codebase for observations |
| `factory begin <path>` | `cli.store.cmd_begin` | Start an experiment |
| `factory status <path>` | `cli.store.cmd_status` | Show project state and recent history |
| `factory history <path>` | `cli.store.cmd_history` | Show experiment history table |
| `factory precheck <path>` | `cli.eval_cmds.cmd_precheck` | Hard gate before keep/revert |
| `factory workflow run <name>` | `workflow.cli` | Execute a workflow headlessly |
| `factory dashboard` | `cli.infra` | Start live web dashboard on :8420 |
| `factory spec generate <path>` | `cli.spec` | Generate SPEC.md from codebase |
| `factory spec update <path>` | `cli.spec` | Patch SPEC.md from diff scope |

[[graph:factory.cli.__init__]]

### §11.2 Mode Selection

The CEO determines operational mode via:

1. Explicit `--mode` flag (build, design, improve, research, meta, create,
   deep-qa, qa, parallel-improve, refine).
2. `--refine` flag → refine mode.
3. `--focus` flag → targeted improve mode.
4. Auto-detection from `ProjectState`:
   - `research_target` in config → research mode.
   - `HAS_FACTORY` → improve mode.
   - `NO_FACTORY` → discover mode.
   - `EVALS_PENDING_REVIEW` → review mode.
   - `NO_REPO` / `REPO_INCOMPLETE` → build mode.

---

## §12 Failure Model and Recovery

### §12.1 Agent Failure Handling

- **Consecutive failure abort:** After 2 consecutive agent failures, the
  factory MUST raise `ConsecutiveAgentFailureError` and emit a
  `cycle.aborted` event. The CEO MUST NOT fall back to doing agent work itself.
- **Individual failures:** Non-zero exit codes are logged, emitted as
  `agent.failed` events, and returned to the caller. The failure counter
  is incremented.
- **Success resets:** A successful invocation MUST reset the counter to 0.

### §12.2 Workflow Failure Handling

- **Gate exhaustion:** When a reloop gate exceeds `max_iterations`, the
  executor MUST halt with a descriptive reason.
- **Node failure:** Any exception during node execution sets `halted=True`
  and records the halt reason.
- **Read timeout:** If a node's `reads` are not satisfied within 60s, the
  workflow halts.
- **Background failures:** Non-blocking node failures are logged but MUST NOT
  halt the main execution path.

### §12.3 Crash Recovery

- **Checkpoint** (`factory/checkpoint.py`) — saves CEO state for resume.
- **CycleState** — persisted at `.factory/state/cycle.json` to preserve mode
  across CEO respawns.
- **Idempotent begin** — `ExperimentStore.begin()` handles pre-existing
  experiment directories from interrupted runs.

### §12.4 Cost Guardrails

- `CostBudget` model enforces per-experiment, per-session, and per-month caps.
- Bob Shell runner enforces `FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE` with
  ceiling warnings at ≤2 remaining invocations.

---

## §13 Security and Safety

- The factory MUST NOT call LLM APIs directly. All AI interactions go through
  CLI subprocess runners, inheriting the user's authentication context.
- `factory.md` and `config.json` MUST NOT contain secrets. API keys are
  resolved via environment variables or `~/.factory/config.toml` credential
  profiles.
- `create_run_dir()` MUST reject path traversal (slashes, `..` sequences).
- Research mode `fixed_surfaces` MUST NOT be modified by any agent. The code
  reviewer verifies compliance.
- The `clean_pr.py` module strips non-essential artifacts (`.factory/` contents)
  from PR diffs before pushing to external repositories.
- Hard constraints (`HardConstraint`) are shell commands that MUST exit 0.
  Non-zero exit forces mandatory revert — the CEO cannot override.

---

## §14 Test and Validation Matrix

| Area | Test Location | Strategy |
|---|---|---|
| Domain models | `tests/test_models.py` | Pydantic strict validation, round-trip JSON, rejection of extras |
| CLI routing | `tests/test_cli.py` | Subcommand dispatch, parser validation, grouped help, sacred rules |
| Workflow graphs | `tests/test_workflow_definitions.py` | NetworkX validation, trigger functions, registration completeness |
| Parallel improve | `tests/test_parallel_improve.py` | Subgraph fork, selection, merge, worktree isolation |
| Deep-QA | `tests/test_workflow_qa.py` | Subgraph extraction, CLI mode parsing |
| Eval system | `tests/test_eval_*` | Hygiene dimensions, growth dimensions, weight normalization |
| Strategy | `tests/test_strategy.py` | FEEC categorization, plateau detection, tiered history |
| Agent runner | `tests/test_agents.py` | Failure tracking, parallel invocation, model flags, no-github |
| Runners | `tests/test_runners.py` | Streaming, ANSI sanitization, review file saving |
| Store | `tests/test_integration.py` | begin/finalize round-trip, history loading |
| State detection | `tests/test_state.py` | GitHub issue checking, eval review detection |
| Adversarial | `tests/test_adversarial.py` | Phase switching, convergence, component model |
| Worktree | `tests/test_worktree.py` | Creation, SHA resolution, symlink resolution |
| Telemetry | `tests/test_session_lifecycle.py` | Span lifecycle, transcript tailing |
| Playbook hygiene | `tests/test_playbook_hygiene.py` | No hardcoded paths, zeroed counters |
| Contributed workflows | `tests/test_*_workflow.py` | Meta validation, trigger, terminal flag |

Test infrastructure: `pytest-asyncio` with `asyncio_mode = "auto"`. Shared
fixtures in `tests/conftest.py`. Autouse `_isolate_registry` redirects the
global registry to a temp directory.

---

## §15 Extension Points

### §15.1 Adding a New Workflow Mode

1. Define a `<mode>_workflow()` function in `factory/workflow/definitions.py`
   returning a `Workflow` with typed nodes, edges, and a trigger function.
2. Register it in `register_all()`.
3. Add a `WORKFLOW_META` entry in `factory/workflow/skill_export.py`.
4. Wire the CLI: parser mode choices, `cmd_ceo` routing, `_build_ceo_task` section.
5. Run `factory workflow validate <name>` and `factory workflow export-skills`.
6. Write tests verifying graph validation, trigger, and registration.

The `create` mode (W₉) automates this process.

### §15.2 Adding a Runner

Implement the `Runner` protocol in `factory/runners/<name>.py`:
- `build_command()` — construct CLI args, env, and temp files.
- `headless()` — async execution returning `AgentRunResult`.
- `metadata()` — return `RunnerMeta` with binary name and capabilities.

Register via `factory/runners/__init__.py:register_runner()`.

### §15.3 Adding an Agent Role

1. Create a prompt file at `factory/agents/prompts/<role>.md`.
2. Add the role to `AgentRole` literal in `factory/agents/runner.py`.
3. Add a default `AgentConfig` in `DEFAULT_AGENT_POOL` (`primitives.py`).
4. Add the role to `AgentRole` enum in `factory/workflow/primitives.py`.

### §15.4 Adding Eval Dimensions

- **Hygiene:** Add to `factory/eval/hygiene.py:compute_hygiene_results()`.
- **Growth:** Add to `factory/eval/growth.py:compute_growth_results()`.
- **Project-specific:** Add `ProjectEvalDimension` entries to `factory.md`
  `## Project Eval` section or `eval_spec` items.

### §15.5 Contributed Workflows

Benchmark workflows live in `factory/workflow/contributed/`. Each MUST:
- Define a `workflow()` function returning a `Workflow`.
- Expose a module-level `meta` dict with `name` and `description`.
- Set `terminal=True` on the `Workflow` (benchmark-specific flag).
- Include a `test_workflow.py` with meta, trigger, and terminal tests.

---

## §16 Implementation Checklist

- [ ] All Pydantic models use `ConfigDict(strict=True, extra="forbid")`
- [ ] All async store operations use `filelock` for concurrent access
- [ ] Workflow graphs pass `validate_graph()` (no orphans, reachable exit)
- [ ] Gate nodes define both PROCEED and RELOOP edges (or HALT)
- [ ] Experiment IDs are monotonically increasing per project
- [ ] Registry self-registration occurs on `begin()`, stats update on `finalize()`
- [ ] Agent output is saved to `.factory/reviews/` after every invocation
- [ ] Events emitted for all agent lifecycle transitions
- [ ] FEEC priority enforced in hypothesis ranking
- [ ] Consecutive failure abort at threshold 2
- [ ] Non-blocking nodes dispatched as background tasks
- [ ] SubgraphForkNode creates isolated worktrees per branch
- [ ] SelectionNode merges winner and finalizes losers as "superseded"
- [ ] Adversarial phase switching uses hysteresis
- [ ] Research mode respects fixed_surfaces (code reviewer verifies)
- [ ] Clean PR mode strips `.factory/` artifacts from diffs
- [ ] Plateau detection uses running best, not absolute best
- [ ] Anti-pattern matching prevents re-proposing reverted hypotheses
- [ ] ACE curator deduplicates at similarity threshold 0.75
- [ ] Playbook injection preserves prompt structure

---

## Appendix A: Reference Algorithms

### A.1 FEEC Hypothesis Ranking

```
for each hypothesis h:
    lower = h.description.lower()
    if any(kw in lower for kw in FIX_KEYWORDS):  category = FIX (0)
    elif any(kw in lower for kw in EXPLOIT_KEYWORDS):  category = EXPLOIT (1)
    elif any(kw in lower for kw in COMBINE_KEYWORDS):  category = COMBINE (3)
    else:  category = EXPLORE (2)
sort hypotheses by category.value (stable sort)
```

### A.2 Composite Score Computation

```
effective_weights = (h_w, g_w, p_w) based on presence of custom project eval
for each tier T in [hygiene, growth, project]:
    apply within-tier weight overrides (sparse)
    normalize: each result.weight = (result.weight / sum_of_tier) * tier_weight
merged = hygiene_results + growth_results + project_results
total = sum(r.score * r.weight for r in merged)
passed = total >= threshold and no guard_violations
```

### A.3 Plateau Detection

```
scored = [r for r in history if r.score_after is not None]
if len(scored) < threshold: return False
best = scored[0].score_after
streak = 0
for r in scored[1:]:
    if r.score_after > best:
        best = r.score_after
        streak = 0
    else:
        streak += 1
return streak >= threshold
```

### A.4 Adversarial Phase Switching

```
component = config.generator if state.active_role == "generator" else config.discriminator
if score >= component.threshold:
    state.consecutive_above += 1
    state.<role>_consecutive_above += 1
else:
    state.consecutive_above = 0
    state.<role>_consecutive_above = 0
switched = state.consecutive_above >= config.hysteresis
if switched:
    state.active_role = opposite(state.active_role)
    state.consecutive_above = 0
converged = (state.gen_streak >= convergence_window AND
             state.disc_streak >= convergence_window)
```

---

## How to Read the Knowledge Graph

This spec uses `[[graph:...]]` reference links to point into a code knowledge graph
extracted by graphify. The graph contains AST-derived entities (modules, classes,
functions) and their typed relationships (imports, calls, inherits).

### Reference Link Types

- `[[graph:EntityName]]` — look up a specific entity (module, class, function)
- `[[graph:path:A:B]]` — find the dependency path between entities A and B
- `[[graph:query:question]]` — run a natural language query against the graph
- `[[graph:community:subsystem]]` — list all entities in a detected subsystem

### When to Use

- **Planning and design:** Read the overview sections in this spec
- **Implementation details:** Resolve `[[graph:...]]` links via `factory spec resolve` or query the graph directly with `graphify explain`, `graphify path`, `graphify query`
