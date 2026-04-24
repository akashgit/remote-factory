# Architecture

The Factory is a three-layer system with strict separation between tooling, orchestration, and execution.

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
- Maintains a checkpoint for crash-resilient resume

Prompt: `factory/agents/prompts/ceo.md`

### Layer 3: Specialist Agents

Six specialist Claude Code subprocesses, each with a narrow responsibility:

| Agent | Role | Invoked via |
|-------|------|------------|
| **Researcher** | Observe code, search for best practices, read vault knowledge | `factory agent researcher --task "..."` |
| **Strategist** | Generate ranked hypotheses using FEEC priority | `factory agent strategist --task "..."` |
| **Builder** | Implement a single hypothesis, open a PR | `factory agent builder --task "..."` |
| **Reviewer** | Guard rules + structured code review | `factory agent reviewer --task "..."` |
| **Evaluator** | Run evals, compare before/after scores | `factory agent evaluator --task "..."` |
| **Archivist** | Write learnings to vault, update dashboards | `factory agent archivist --task "..."` |

Agent prompts are resolved via two-tier lookup in `factory/agents/runner.py`:
1. Project-specific override: `<project>/.factory/agents/<role>.md`
2. Factory default: `factory/agents/prompts/<role>.md`

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

State detection logic lives in `factory/state.py`.

![State Machine](diagrams/state-machine.svg)

## Data Flow

### Discovery Pipeline

```
factory/discovery/introspect.py   → Detect language, framework, project type
factory/discovery/profile.py      → Build EvalProfile with dimensions and weights
factory/discovery/generate.py     → Generate eval/score.py script
```

### Experiment Loop

```
1. Researcher observes  → .factory/strategy/observations.md
2. Strategist ranks     → .factory/strategy/current.md (FEEC-prioritized hypotheses)
3. Builder implements   → experiment branch + PR
4. Evaluator measures   → eval_before.json, eval_after.json
5. CEO decides          → keep (merge) or revert (close PR)
6. Archivist records    → vault notes, experiment artifacts
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
| `factory/checkpoint.py` | CEO checkpoint save/load |
| `factory/analysis.py` | Experiment comparison (diff, explain) |
| `factory/agents/runner.py` | Agent subprocess spawner + event emission |

## `.factory/` Directory

Generated at runtime — not checked into version control:

```
.factory/
├── config.json           # Parsed from factory.md
├── eval_profile.json     # Discovered eval dimensions
├── results.tsv           # Append-only experiment history
├── events.jsonl          # Structured event log
├── experiments/
│   └── 001/
│       ├── hypothesis.md
│       ├── eval_before.json
│       ├── eval_after.json
│       ├── changes.diff
│       └── verdict.json
├── strategy/
│   ├── current.md        # Active hypotheses
│   ├── observations.md   # Researcher findings
│   └── insights.md       # Cross-project patterns
├── reviews/
│   ├── <role>-latest.md
│   └── ceo-verdict-<role>.md
└── agents/               # Per-project prompt overrides
```

## Diagrams

- [Architecture Overview](diagrams/architecture.svg)
- [Data Flow](diagrams/data-flow.svg)
- [Experiment Lifecycle](diagrams/experiment-lifecycle.svg)
- [Eval System](diagrams/eval-system.svg)
- [State Machine](diagrams/state-machine.svg)
