# Architecture

re:factory is a three-layer system with strict separation between tooling, orchestration, and execution.

## Three Layers

### Layer 1: Python CLI (`factory/`)

Pure tools that don't make decisions. The CLI provides measurement, storage, and project introspection — never deciding *what* to change or *whether* to keep a change.

Entry point: `factory/cli.py` → `factory.cli:main` (registered as `factory` in pyproject.toml). Each subcommand is a `cmd_*` function dispatched via a handler dict.

### Layer 2: CEO Agent

A dedicated Claude Code agent that owns the full workflow. Spawned via `factory ceo <path>` or `factory run <path>`. The CEO:

- Detects project state and routes to the appropriate mode
- Spawns specialist agents as subprocesses
- Makes keep/revert decisions based on eval scores
- Ensures mandatory archival after every cycle
- Reads event log and `.factory/` state directly for crash-resilient resume

Prompt: `factory/agents/prompts/ceo.md`

### Layer 3: Specialist Agents

Nine specialist Claude Code subprocesses, each with a narrow responsibility:

| Agent | Role | Invoked via |
|-------|------|------------|
| **Researcher** | Observe code, search for best practices, read prior knowledge | `factory agent researcher --task "..."` |
| **Strategist** | Generate ranked hypotheses using FEEC priority | `factory agent strategist --task "..."` |
| **Builder** | Implement a single hypothesis, open a PR | `factory agent builder --task "..."` |
| **Reviewer** | Guard rules + structured code review | `factory agent reviewer --task "..."` |
| **Evaluator** | Run evals, compare before/after scores | `factory agent evaluator --task "..."` |
| **Archivist** | Write learnings to `.factory/archive/`, update performance reports | `factory agent archivist --task "..."` |
| **Distiller** | Synthesize research + raw idea into a buildable project spec | `factory agent distiller --task "..."` |
| **Refiner** | Classify and scope post-cycle refinement requests (T1/T2/T3 tiers) | `factory agent refiner --task "..."` |
| **Failure Analyst** | Classify run failures by root cause (research mode only) | `factory agent failure_analyst --task "..."` |

Agent prompts are resolved via two-tier lookup in `factory/agents/runner.py`:
1. Project-specific override: `<project>/.factory/agents/<role>.md`
2. re:factory default: `factory/agents/prompts/<role>.md`

Evolved playbooks from ACE are auto-injected at runtime.

## State Machine

The CEO detects project state and routes to the appropriate mode:

| State | Condition | Mode |
|-------|-----------|------|
| `no_repo` | No git repo | **Build** — scaffold from spec |
| `incomplete` | Repo exists, missing structure | **Build** — complete scaffold |
| `no_factory` | Code exists, no `.factory/` | **Discover** — introspect + generate evals |
| `evals_pending_review` | Evals generated, not reviewed | **Review** — human approval gate |
| `has_factory` | Everything initialized | **Improve** — run experiment loop |

**Additional modes** (selected explicitly, not auto-detected):

| Flag | Mode | What it does |
|------|------|-------------|
| `--focus "item"` | **Targeted** | Pins one backlog item, one hypothesis, one experiment, then exits |
| `--mode interactive` | **Interactive** | Research → Distiller spec → user feedback loop → build |
| `--mode research` | **Research** | Failure analysis → targeted research → hypothesis → build → metric evaluation with leakage guards and monotonic improvement |
| `--mode meta` | **Meta** | Full Improve loop on re:factory itself, then ACE playbook evolution |
| `--refine "request"` | **Refine** | Refiner scopes → Builder implements → full review pipeline → keep/revert |

State detection logic lives in `factory/state.py`.

![State Machine](diagrams/state-machine.svg)

> **Note:** Explicit flags (`--mode interactive`, `--mode research`, `--mode meta`, `--focus`) override auto-detection. All modes return to `has_factory` on completion.

## Data Flow

### Discovery Pipeline

```
factory/discovery/introspect.py   → Detect language, framework, project type
factory/discovery/profile.py      → Build EvalProfile with dimensions and weights
factory/discovery/generate.py     → Generate eval/score.py script
```

### Ideation Pipeline (Interactive Mode)

```
1. Researcher surveys   → .factory/strategy/research.md (domain landscape)
2. Distiller synthesizes → idea.md spec (features, architecture, non-goals)
3. CEO presents draft    → user reviews, gives feedback
4. Iterate (2-3)         → Distiller revises, optional follow-up research
5. User approves         → spec persisted to .factory/strategy/current.md
6. Transition            → proceed to Build mode
```

### Research Pipeline (Research Mode)

```
1. Baseline        → Evaluator runs run_command, records starting metric
2. Failure Analyst → Classifies failures (per-instance root cause + aggregated categories)
3. Researcher      → Web search for targeted solutions to dominant failure patterns
4. Strategist      → 1-3 hypotheses targeting dominant failure modes
   └─ CEO gate: mutable_surfaces check + leakage scan
5. Builder         → Implements hypothesis (mutable surfaces only)
   └─ CEO gate: fixed_surfaces check + leakage scan
6. Run             → Re-executes run_command, extracts new metric
7. Verdict         → Keep if metric >= previous_best AND hygiene intact; else revert
```

Key differences from Improve mode:
- **Failure Analyst** replaces the standard Researcher observation step
- **Mutable/fixed surfaces** enforce strict file-level access control
- **Leakage guards** scan hypotheses and diffs for ground truth contamination (token overlap, negation hints, specific values)
- **Monotonic improvement** — the metric must never regress below the previous best
- **Precheck** adds fixed surface guard + leakage detector on top of standard checks

### Experiment Loop (Improve Mode)

```
1. Researcher observes  → .factory/strategy/observations.md
2. Strategist ranks     → .factory/strategy/current.md (FEEC-prioritized hypotheses)
3. Builder implements   → experiment branch + PR
4. Evaluator measures   → eval_before.json, eval_after.json
5. CEO decides          → keep (merge) or revert (close PR)
6. Archivist records    → .factory/archive/ notes, performance report
```

### Eval Pipeline

```
factory/eval/hygiene.py   → 6 mandatory dimensions (tests, lint, types, coverage, guards, config)
factory/eval/growth.py    → 5 universal dimensions (capability, diversity, observability, research, effectiveness)
factory/eval/runner.py    → Three-tier merge: hygiene (50%) + growth (50%), or hygiene + growth + project
factory/eval/scorer.py    → Weighted composite score computation
factory/eval/guards.py    → Guard rule enforcement (scope, immutability)
```

![Eval System](diagrams/eval-system.svg)

### Data Flow

![Core Pipeline](diagrams/dataflow-core.svg)

For research projects and ACE self-improvement, additional data flows manage mutable/fixed surfaces, leakage guards, and playbook evolution:

![Research & Self-Improvement](diagrams/dataflow-research.svg)

### Experiment Lifecycle

Each experiment follows three phases. **Phase 1** observes the project and generates hypotheses:

![Observe & Plan](diagrams/lifecycle-observe.svg)

**Phase 2** executes the approved hypothesis — building, reviewing, and evaluating:

![Execute](diagrams/lifecycle-execute.svg)

**Phase 3** runs a non-overridable precheck gate and makes the keep/revert decision:

![Decision](diagrams/lifecycle-decide.svg)

In standard mode, the cycle loops back to the next hypothesis. In targeted mode (`--focus`), it exits after one decision.

### Strategy

`factory/strategy.py` implements FEEC priority: **Fix** > **Exploit** > **Explore** > **Combine**.

- Fix: broken tests, failing lint, regressions
- Exploit: improve existing working features
- Explore: add new capabilities
- Combine: cross-cutting improvements

Stuck detection activates after 3+ consecutive same-category reverts, forcing category rotation.

## Key Modules

| Module | Purpose |
|--------|---------|
| `factory/cli.py` | CLI entry point, argparse subcommands |
| `factory/models.py` | Pydantic v2 models (strict mode) |
| `factory/state.py` | Project state detection (5 states) |
| `factory/store.py` | `.factory/` filesystem store |
| `factory/events.py` | Event system (JSONL append-only log) |
| `factory/strategy.py` | FEEC priority heuristic |
| `factory/study.py` | Interaction log analysis |
| `factory/insights.py` | Cross-project pattern analysis |
| `factory/checkpoint.py` | CEO checkpoint save/load (legacy, debugging) |
| `factory/analysis.py` | Experiment comparison (diff, explain) |
| `factory/registry.py` | Global project registry (`~/.factory/registry.json`) |
| `factory/report.py` | Performance report generation and loading |
| `factory/agents/runner.py` | Agent subprocess spawner + event emission |

## `.factory/` Directory

Generated at runtime — not checked into version control:

```
.factory/
├── config.json              # Parsed from factory.md
├── eval_profile.json        # Discovered eval dimensions
├── results.tsv              # Append-only experiment history
├── events.jsonl             # Structured event log
├── performance_report.json  # Aggregated verdicts, observations, experiment stats
├── experiments/
│   └── 001/
│       ├── hypothesis.md
│       ├── eval_before.json
│       ├── eval_after.json
│       ├── changes.diff
│       └── verdict.json
├── strategy/
│   ├── current.md           # Active hypotheses
│   ├── observations.md      # Researcher findings
│   ├── backlog.md           # Unified backlog (features, deferred items, issues)
│   └── insights.md          # Cross-project patterns
├── reviews/
│   ├── <role>-latest.md
│   └── ceo-verdict-<role>.md
├── archive/                 # Archivist notes (institutional memory)
│   ├── experiments/         # Per-experiment notes
│   ├── strategies/          # Strategy snapshots
│   ├── sources/             # Research source notes
│   └── patterns/            # Cross-project patterns
└── agents/                  # Per-project prompt overrides
```

## Diagrams

- [State Machine](diagrams/state-machine.svg)
- [Architecture Overview](diagrams/architecture.svg)
- [Eval System](diagrams/eval-system.svg)
- [Data Flow — Core Pipeline](diagrams/dataflow-core.svg)
- [Data Flow — Research & Self-Improvement](diagrams/dataflow-research.svg)
- [Experiment Lifecycle — Observe & Plan](diagrams/lifecycle-observe.svg)
- [Experiment Lifecycle — Execute](diagrams/lifecycle-execute.svg)
- [Experiment Lifecycle — Decide](diagrams/lifecycle-decide.svg)

## Related Docs

- [Self-Improvement Loop](self-improvement.md) — How the CEO tracks agents, cross-project learning, and autonomous playbook evolution
- [ACE Playbook Evolution](ace.md) — The Reflect → Curate → Inject playbook evolution mechanics
- [Eval System](eval.md) — Three-tier scoring, guards, and precheck gates
