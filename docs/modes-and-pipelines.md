# Factory Modes & Pipelines Reference

> How the factory detects what a project needs, which mode it enters, what agents it spawns, and the exact deterministic steps it follows to solve the user's problem.

---

## How It Works — The 30-Second Version

The factory detects the state of your project (does it exist? does it have evals? is it factory-ready?) and routes into the appropriate **mode**. Each mode is a deterministic pipeline of agent invocations. The CEO agent orchestrates everything — it spawns specialist agents, reviews their output, and makes keep/revert decisions. Every experiment follows the same lifecycle: begin → build → review → eval → precheck → finalize.

```
User's project
  │
  ├─ no repo / incomplete ──────────► Build mode (B0–B6)
  │                                      │
  ├─ repo exists, no factory ────────► Discover mode
  │                                      │
  ├─ evals pending review ──────────► Review mode
  │                                      │
  └─ factory initialized ──────────► Improve mode (Steps 0–5)
                                     │  └─ research_target configured ──► Research mode (R0–R5)
                                     │
                                     └─ After cycle ──► Post-Cycle Refinement Loop
```

---

## 1. Project State Detection

Before any mode runs, the factory detects the project's current state. Five states exist, checked in priority order:

| State | Condition | Routes To |
|---|---|---|
| `no_repo` | No `.git` directory at the given path | Build mode |
| `incomplete` | Git repo exists, open GitHub issues with `plan` label | Build mode |
| `no_factory` | Git repo, no plan issues, no `.factory/config.json` | Discover mode |
| `evals_pending_review` | `.factory/eval_profile.json` exists with `human_reviewed: false` | Review mode |
| `has_factory` | `.factory/config.json` exists | Improve mode (or Research if `research_target` is configured) |

Detection runs via `factory detect <path>`. After each mode completes, the factory re-detects state and may chain into the next mode (e.g., Build → Discover → Review → Improve).

---

## 2. The Agents

Eight specialist agents, each with a focused role. The CEO never does their work — it delegates, reviews, and decides.

| Agent | Model | Role | When Spawned |
|---|---|---|---|
| **CEO** | opus | Executive orchestrator. Detects state, spawns specialists, runs experiments, makes keep/revert decisions. | Entry point — runs the entire cycle |
| **Researcher** | sonnet | Deep research: local codebase analysis (`factory study`), web research, archive synthesis | Observe phase (every mode) |
| **Strategist** | sonnet | Generates prioritized hypotheses in Improve/Research modes. In Ideation mode, synthesizes specs from research. | Hypothesize phase |
| **Builder** | sonnet | Implements a single focused change, opens a PR. One hypothesis per invocation. | Execute phase |
| **Reviewer** | sonnet | Reviews PRs against guard rules, eval scores, code quality. Runs `factory guard`. | Guard check phase |
| **Evaluator** | sonnet | Runs `factory eval`, reports composite + per-dimension scores, compares before/after. | Before and after each experiment |
| **Archivist** | sonnet | Records learnings to `.factory/archive/`. Maintains institutional memory across cycles. | After every phase (mandatory) |
| **Failure Analyst** | sonnet | Classifies failures from research benchmark runs by stage and root cause. | Research mode only (Phase R1) |

### Additional Agents (not in main registry)

| Agent | Role |
|---|---|
| **Refiner** | Classifies and scopes user refinement requests into Tier 1/2/3 (Refine mode) |
| **Distiller** | Synthesizes research into buildable specs during Phase 0 ideation |

---

## 3. Phase 0 — Interactive Ideation

**Entry:** `factory ceo --mode interactive` or when the user provides a raw idea.
**Purpose:** Transform a vague idea into a research-grounded, buildable specification through iterative refinement with the user.

```
I0: Research ──► I0r: CEO Review ──► I1: Distiller ──► I1r: CEO Review
                                                            │
                                    ┌───────────────────────┘
                                    ▼
                              I2: Present to User
                                    │
                         ┌──────────┼──────────┐
                         ▼          ▼          ▼
                    Approved    Feedback    Research needed
                         │          │          │
                         │          └─► I3 ───►│
                         │           (loop)    │
                         ▼                     │
                    I4: Finalize               │
                         │                     │
                    ┌────┴────┐                │
                    ▼         ▼                ▼
              Build mode  Improve mode   Re-research
              (new idea)  (existing project)
```

| Step | Agent | Action |
|---|---|---|
| **I0** | Researcher | Survey the space: similar projects, tech stack, architecture patterns, pitfalls |
| **I0r** | CEO | Review research relevance and depth |
| **I1** | Distiller | Synthesize research + raw idea into structured spec (features, tech stack, MVP scope) |
| **I1r** | CEO | Mandatory checks: depth (3+ sentences per feature), research grounding, buildability |
| **I1v** | CEO | Research config validation (research ideation only) |
| **I2** | CEO | Present spec to user, ask for feedback |
| **I3** | Distiller | Incorporate feedback, optionally re-research if new territory introduced |
| **I4** | CEO + Archivist | Persist approved spec, archive ideation, transition to Build or Improve |

**Max 5 feedback iterations.** No code is written during Phase 0 — only a spec document.

---

## 4. Build Mode (B0–B6)

**Entry:** Project state is `no_repo` or `incomplete`.
**Purpose:** Build the project from scratch (or complete an incomplete build) following the approved spec.
**Constraint:** ALL phases must be attempted. Early exit is forbidden.

```
B-0: Sprint Assessment
 │
 ▼
B0: Research ──► B0r: CEO Review ──► B0a: Archivist
 │
 ▼
B1: Strategy ──► B1r: CEO Review (HARD GATE) ──► B2: Archivist
 │
 ▼
B3: Build Phase 1 ──► B3r: CEO Review ──► B4: Archivist
 │
 ▼
B3: Build Phase 2 ──► B3r: CEO Review ──► B4: Archivist
 │
 ... (repeat for each phase)
 │
 ▼
B5: E2E Verification Gate ──► B5a: Persist Backlog
 │
 ▼
B6: Re-detect state ──► Chain to Discover/Review/Improve
```

| Step | Agent | Action |
|---|---|---|
| **B-0** | CEO | Check `.factory/events.jsonl` for incomplete sprints. Resume or start fresh. |
| **B0** | Researcher | Survey tech stack, best practices, architecture patterns for the spec |
| **B0r** | CEO | Review research (max 2 redirects) |
| **B0a** | Archivist | Record research findings |
| **B1** | Strategist | Create phased build plan as GitHub issues. Phase 1 is always scaffold + eval. |
| **B1r** | CEO | **Hard gate.** Verify plan aligns with spec, phases are right-sized, deferred items genuinely require human intervention. |
| **B2** | Archivist | Record approved plan |
| **B3** | Builder | Implement one phase. Commit changes. (Sequential — one phase at a time.) |
| **B3r** | CEO | Review what was built against the plan |
| **B4** | Archivist | Record build progress |
| **B5** | CEO | **E2E gate.** The project must actually run. Figure out the start command, try it, fix failures. Ask user for credentials if needed. Persist the working smoke test command. |
| **B5a** | CEO | Extract deferred items to backlog |
| **B6** | CEO | Re-detect project state. If `no_factory` → Discover. If still `incomplete` → next Build phase. |

---

## 5. Discover Mode

**Entry:** Project state is `no_factory`.
**Purpose:** Auto-discover eval dimensions and generate the eval harness.

| Step | Action |
|---|---|
| 1 | Run `factory discover <path>` — introspects language, framework, test suite, linter, type checker |
| 2 | Verify output: `eval_profile.json` and `eval/score.py` |
| 3 | Re-detect state → should be `evals_pending_review` |

Lightweight mode — usually completes in seconds.

---

## 6. Review Mode

**Entry:** Project state is `evals_pending_review`.
**Purpose:** Verify discovered eval dimensions work and initialize the factory.

| Step | Action |
|---|---|
| 1 | Run `eval/score.py` to test all dimensions |
| 2 | Fix broken dimensions (install missing tools, adjust commands) |
| 3 | Mark eval profile as reviewed (`human_reviewed: true`) |
| 4 | Create `factory.md` from template (Goal, Scope, Guards, Eval, Smoke Test) |
| 4b | Populate research config sections if Phase 0 produced a Research Configuration |
| 5 | Run `factory init <path>` to create `.factory/config.json` |
| 6 | Run baseline eval |
| 7 | Commit factory config |

---

## 7. Improve Mode — The Core Evolution Loop (Steps 0–5)

**Entry:** Project state is `has_factory`.
**Purpose:** Systematically improve the project through hypothesis-driven experiments.

```
Step 0: Observe ──────────────────────────────────────────────────────────►
│                                                                         │
├── 0a: factory study + Researcher ──► 0b: CEO Review ──► 0c: Archivist  │
│                                                                         │
Step 1: Hypothesize ─────────────────────────────────────────────────────►
│                                                                         │
├── Strategist ──► CEO Review (HARD GATE) ──► Archivist                  │
│                                                                         │
Step 2: Execute (per hypothesis) ────────────────────────────────────────►
│                                                                         │
│  ┌─── 2a: Baseline Eval                                                │
│  ├─── 2b: factory begin                                                 │
│  ├─── 2c: Create GitHub Issue                                           │
│  ├─── 2d: Builder implements                                            │
│  ├─── 2d-r: CEO Code Quality Review (review-until-clean, max 3 iter)   │
│  ├─── 2e: Reviewer guard check                                         │
│  ├─── 2f: Post-change Eval                                             │
│  ├─── 2f-e2e: E2E Smoke Test                                           │
│  ├─── 2g: Precheck Gate (NON-OVERRIDABLE)                              │
│  ├─── 2h-final: Final Holistic Review (max 3 iter)                     │
│  ├─── Approve or Revert                                                 │
│  └─── 2h: Archivist                                                    │
│                                                                         │
Step 3: Final Archive (BLOCKING) ──► Step 4: Notify ──► Step 5: Commit   │
```

### Observation Phase (Step 0)

| Sub-step | Agent | Action |
|---|---|---|
| **0a** | CEO | Run `factory study` — local analysis, cross-project insights, open issues, backlog, observability coverage |
| **0b** | Researcher | Deep research: web search for best practices, check `.factory/archive/` for prior knowledge |
| **0b-r** | CEO | Review research output |
| **0c** | Archivist | Record research findings |

### Hypothesis Phase (Step 1)

| Sub-step | Agent | Action |
|---|---|---|
| **1** | Strategist | Generate prioritized hypotheses following FEEC priority (Fix > Exploit > Explore > Combine). Reads backlog, observations, research, eval scores. |
| **1r** | CEO | **Hard gate.** Mandatory checks: at least one growth hypothesis, backlog convergence (backlog must shrink), new item cap (max 2), operational item validation, backlog item adequacy. |
| — | Archivist | Record strategy decisions |

### Experiment Phase (Step 2) — Per Hypothesis

Each hypothesis goes through the full pipeline. No shortcuts for "small" changes.

| Sub-step | Agent | Action |
|---|---|---|
| **2a** | Evaluator | Run baseline eval, save `score_before` |
| **2b** | CEO | `factory begin --hypothesis "..."` → creates experiment branch |
| **2c** | CEO | Create GitHub issue with acceptance criteria |
| **2d** | Builder | Implement the hypothesis on the experiment branch, open draft PR |
| **2d-review** | CEO | **Review-Until-Clean Pipeline** (see below) |
| **2e** | Reviewer | Run `factory guard` — scope, eval immutability, git clean, experiment branch |
| **2e-review** | CEO | Validate reviewer output (not rubber-stamped?) |
| **2f** | Evaluator | Run post-change eval, save `score_after` |
| **2f-e2e** | CEO | Run smoke test from `factory.md`. If not configured, figure it out and persist. |
| **2g** | CEO | Run `factory precheck` — 4 checks: score_direction, scope, anti_pattern, smoke_test. **Failure = mandatory revert, no override.** |
| **2h-final** | CEO | Final holistic code review via headless `claude -p` (max 3 iterations) |
| — | CEO | KEEP: `gh pr ready`, `factory review --verdict KEEP`, `factory finalize --verdict keep` |
| — | CEO | REVERT: `factory review --verdict REVERT`, `gh pr close`, `factory finalize --verdict revert` |
| **2h** | Archivist | Record experiment outcome |

### The Review-Until-Clean Pipeline (Step 2d-review)

Every experiment PR goes through this pipeline — no exceptions (Sacred Rule 9).

```
Read PR diff
    │
    ▼
7-category structured checklist:
  1. Correctness     5. Style & consistency
  2. Security        6. Scope compliance
  3. Edge cases      7. Guardrail compliance
  4. Missing tests
    │
    ├── CLEAN ──────────────────────► Proceed to 2e
    │
    ├── ABORT (garbage PR) ─────────► Close PR, finalize as error
    │
    └── ISSUES_FOUND ──────────────►
            │
            ├── iteration >= 3? ───► Stop looping, proceed
            │
            ├── issues >= previous? ► Stop (not converging)
            │
            └── Route fixes to Builder
                    │
                    └── Re-read diff, re-evaluate ──► (loop)
```

### Finalization (Steps 3–5)

| Step | Agent | Action |
|---|---|---|
| **3** | Archivist | **Blocking.** Pre-flight check: all checkpoint entries present. Final archive. |
| **3b** | CEO | `factory summary` — what was built, what was deferred, what needs human input |
| **4** | CEO | `factory notify` — send notifications |
| **5** | CEO | Commit `.factory/` state |

---

## 8. Research Mode (R0–R5)

**Entry:** `has_factory` + `research_target` configured in `config.json`.
**Purpose:** Improve a measurable research target (e.g., benchmark accuracy) through iterative failure analysis and targeted fixes.

```
R0: Baseline ──► R1: Failure Analyst ──► Archivist ──►
R1.5: Researcher ──► Archivist ──►
R2: Strategist ──► Archivist ──►
R3: Builder (per hypothesis) ──► Archivist ──►
R4: Run (execute run_command) ──►
R5: Verdict (hygiene gate → monotonic check → precheck → keep/revert)
```

| Step | Agent | Action |
|---|---|---|
| **R0** | Evaluator | Run `run_command` from research config, record baseline metric. Multi-run mode if `inner_loop.runs_per_cycle > 1`. |
| **R1** | Failure Analyst | Classify failures from baseline by stage, root cause, and failure mode distribution |
| **R1.5** | Researcher | Search for solutions to the dominant failure patterns. **Mandatory — not optional.** |
| **R2** | Strategist | Generate 1–3 hypotheses targeting dominant failures. Surface constraints enforced (mutable vs fixed). Ground truth leakage scan. |
| **R3** | Builder | Implement on experiment branch. **Must only modify mutable surfaces.** |
| **R4** | Evaluator | Run `run_command` again on modified code. Multi-run with aggregation (mean, median, max, all_pass). |
| **R5** | CEO | Verdict decision chain: hygiene gate (NON-OVERRIDABLE) → monotonic improvement check → precheck gate → keep/revert. Plateau detection triggers inner/outer loop surface expansion. |

### Key Research-Specific Features

- **Surface constraints:** Builder can only modify `mutable_surfaces`. `fixed_surfaces` are untouchable (eval infrastructure, test data, ground truth).
- **Ground truth leakage detection:** Both hypothesis text and PR diffs are scanned for tokens matching ground truth files.
- **Monotonic improvement:** The aggregate target metric must never regress below the previous best.
- **Inner/outer loop:** When metric plateaus for N consecutive cycles, the system expands mutable surfaces to allow architectural changes.
- **Termination conditions:** Target met, budget exhausted, or all hypotheses processed.

---

## 9. Refine Mode (R0–R12)

**Entry:** `factory ceo --refine "instruction"` or during the Post-Cycle Refinement Loop.
**Purpose:** Lightweight pipeline for user-directed changes. The user is the strategist.

```
R0: Refiner classifies ──► R0-r: CEO Review ──►
R1: Tier gate (Tier 3 exits) ──►
R2: factory begin ──► R3: Create issue ──► R4: Builder ──►
R5–R10: Full review pipeline (identical to Improve) ──►
R11: Keep/revert + finalize ──► R12: Archivist
```

| Step | Agent | Action |
|---|---|---|
| **R0** | Refiner | Classify request as Tier 1 (trivial), 2 (moderate), or 3 (too large) |
| **R1** | CEO | Tier 3 → exit with recommendation to use `--focus`. Tier 1/2 → proceed. |
| **R2** | CEO | `factory begin` |
| **R3** | CEO | Create GitHub issue from Refiner's scoped task |
| **R4** | Builder | Implement the change |
| **R5–R10** | CEO + Reviewer + Evaluator | **Full review pipeline** — identical to Improve mode 2d-review through 2h-final. No shortcuts. |
| **R11** | CEO | Keep/revert verdict + finalize |
| **R12** | Archivist | Single batch archival |

### Key difference from Improve:
No Researcher, no Strategist — the user already knows what they want. But the review pipeline is identical — every refinement gets the same quality gates.

---

## 10. Meta Mode

**Entry:** `factory ceo --mode meta` (explicit invocation only).
**Purpose:** Self-improvement — run the Improve loop on the factory itself, then evolve agent playbooks.

| Phase | Action |
|---|---|
| **1: Improve** | Full Improve mode pipeline targeting the factory's own codebase |
| **2: ACE** | `factory insights` → `factory ace` → Archivist records playbook evolution |

**Cadence:** Weekly for active projects. Not after initial build. Not after every cycle. Minimum 5 experiments across all projects before running.

---

## 11. Post-Cycle Refinement Loop

After any Improve/Research cycle completes in foreground (non-headless) mode, the CEO enters a refinement loop.

```
PC1: Present results + wait for input
         │
         ├── "done" / "looks good" ──► Exit
         │
         ├── Question ──► Answer directly ──► Back to PC1
         │
         └── Change request ──► PC3: Execute Refine pipeline (R0–R12)
                                       │
                                       └── Back to PC1
```

No hard cap on refinements. Advisory warnings at 5 and 10. Sacred Rules 8 (no self-implementation) and 9 (no skipping review) apply at all times.

---

## 12. The Experiment Lifecycle

Every code change, regardless of mode, follows this lifecycle:

```
factory begin ──► Builder ──► Review-Until-Clean ──► Guard Check
    │                                                      │
    │              ┌───────────────────────────────────────┘
    │              ▼
    │          Post-change Eval ──► E2E Smoke Test ──► Precheck Gate
    │                                                      │
    │              ┌───────────────────────────────────────┘
    │              ▼
    │          Final Holistic Review
    │              │
    │         ┌────┴────┐
    │         ▼         ▼
    │       KEEP      REVERT
    │         │         │
    │    gh pr ready  gh pr close
    │         │         │
    └──► factory finalize ──► Archivist
```

### The Four Gates (all must pass to KEEP)

| Gate | Check | Override? |
|---|---|---|
| **Guard Check** | Scope, eval immutability, git clean, experiment branch | NO |
| **Eval Score** | Score must not regress below threshold | NO |
| **Precheck** | score_direction + scope + anti_pattern + smoke_test | NO |
| **Code Review** | Structured 7-category checklist + final holistic review | YES (after max iterations) |

---

## 13. Mandatory Archival Checkpoints

The Archivist fires after every phase — no exceptions (Sacred Rule 7).

| Checkpoint | When | Blocking? |
|---|---|---|
| Post-research | After Researcher completes | YES |
| Post-strategy | After Strategist completes | YES |
| Post-build | After each Builder phase | YES |
| Post-experiment | After each keep/revert decision | YES |
| Final archive | After all experiments done | YES |

Each invocation writes a line to `.factory/reviews/archivist-checkpoints.md`. Before finalization, the CEO verifies all entries are present.

---

## 14. The Sacred Rules

Nine inviolable rules checked by `factory guard` before any change is kept:

1. **Do not delete or overwrite existing tests** — tests may be extended, never removed
2. **Do not modify files outside declared scope** — `factory.md` defines modifiable files
3. **Do not introduce secrets or credentials** — no API keys, tokens, or passwords in the repo
4. **Do not lower the eval threshold** — the bar only goes up
5. **Do not skip the eval step** — every change must be scored
6. **Do not merge PRs** — leave them open for human review
7. **Do not skip archival checkpoints** — the Archivist must fire at every checkpoint
8. **Do not do another agent's job** — the CEO delegates ALL technical work
9. **Do not skip the review pipeline** — full review for every experiment

---

## 15. Strategy Prioritization: FEEC

When the Strategist generates hypotheses, it follows the FEEC heuristic:

| Priority | Category | Examples |
|---|---|---|
| 1 (highest) | **Fix** | Bugs, broken tests, failing evals |
| 2 | **Exploit** | Improve weak eval dimensions near thresholds |
| 3 | **Explore** | New features, new approaches |
| 4 | **Combine** | Merge patterns from successful experiments |

**Backlog items take priority over new items.** The backlog must shrink each cycle — new items are capped at 2 per cycle.

---

## 16. Runner Infrastructure

The factory uses a protocol-based runner abstraction to support multiple AI agent backends.

### Runner Protocol

```python
class Runner(Protocol):
    name: str
    def metadata(cls) -> RunnerMeta: ...
    def build_command(request) -> tuple[list[str], dict[str, str], list[Path]]: ...
    async def headless(request) -> AgentRunResult: ...
    def interactive_run(request) -> int: ...
```

### Registered Runners

| Runner | Binary | System Prompt Delivery | Telemetry | Interactive |
|---|---|---|---|---|
| **claude** | `claude` | `--append-system-prompt-file` (dedicated slot) | JSON output parsing (full) | tmux persist + hooks |
| **codex** | `codex` | `AGENTS.md` at project root (developer priority) | NDJSON event stream parsing | tmux persist (generalized) |
| **opencode** | `opencode` | User message concatenation | None | `subprocess.run()` |
| **bob** | `bob` | User message concatenation | Invocation ceiling (8/cycle) | `subprocess.run()` |

Third-party runners can be registered via the `factory.runners` entry-point group in `pyproject.toml`.

---

## 17. Eval System

### Eval Dimensions (12 total)

**Hygiene dimensions (6):**

| Dimension | Weight | Measures |
|---|---|---|
| `tests` | 0.30 | Test suite pass/fail (auto-detected runner) |
| `lint` | 0.15 | Linter clean (ruff, eslint, clippy, etc.) |
| `type_check` | 0.10 | Type checker clean (mypy, tsc, etc.) |
| `coverage` | 0.25 | Test coverage percentage |
| `guard_patterns` | 0.10 | Glob pattern verification for scope enforcement |
| `config_parser` | 0.10 | `factory.md` configuration completeness |

**Growth dimensions (6):**

| Dimension | Weight | Measures |
|---|---|---|
| `capability_surface` | 0.25 | Modules + public functions + entry points (AST analysis) |
| `experiment_diversity` | 0.20 | Category spread across experiments (anti-repetition) |
| `observability` | 0.18 | Logging coverage, structured logging, request tracing |
| `research_grounding` | 0.14 | Vault sources, citation coverage, experiment notes |
| `factory_effectiveness` | 0.13 | Keep rate, positive deltas, multi-project reach |
| `spec_compliance` | 0.10 | Qualitative spec compliance checks |

### Weight Distribution

| Configuration | Hygiene | Growth | Project Eval |
|---|---|---|---|
| No project eval (default) | 50% | 50% | — |
| With project eval | 30% | 20% | 50% |

### Language Support

| Language | Detection | Test Runner | Linter | Type Checker |
|---|---|---|---|---|
| Python | `pyproject.toml` / `setup.py` | pytest | ruff | mypy |
| Node.js | `package.json` | jest | eslint | tsc |
| Go | `go.mod` | `go test` | `go vet` | `go build` |
| Rust | `Cargo.toml` | `cargo test` | clippy | `cargo check` |

---

## 18. CLI Command Reference

### Core Workflow

| Command | Purpose |
|---|---|
| `factory ceo <path>` | Launch the CEO agent on a project |
| `factory run <path>` | Run a cycle (single-shot or loop) |
| `factory detect <path>` | Detect project state |
| `factory agent <role>` | Spawn a specialist agent directly |

### Experiment Lifecycle

| Command | Purpose |
|---|---|
| `factory begin` | Start a new experiment |
| `factory eval` | Run evaluations |
| `factory guard` | Run guard checks |
| `factory precheck` | Run pre-merge checks |
| `factory review` | Post review verdict on PR |
| `factory finalize` | Finalize experiment (keep/revert) |
| `factory clean-pr` | Strip non-essential artifacts from PR |

### Project Setup

| Command | Purpose |
|---|---|
| `factory discover` | Auto-discover eval dimensions |
| `factory init` | Initialize factory for a project |
| `factory install` | Install agent files for a runner |

### Strategy & Knowledge

| Command | Purpose |
|---|---|
| `factory study` | Analyze project state |
| `factory history` | Show experiment history |
| `factory summary` | Generate session summary |
| `factory backlog-add/remove/list` | Manage the backlog |
| `factory insights` | Generate cross-project insights |
| `factory ace` | Run playbook self-improvement |

### Operational

| Command | Purpose |
|---|---|
| `factory log` | Append to event log |
| `factory checkpoint` | Save/load crash recovery state |
| `factory resume` | Resume in-flight cycle |
| `factory notify` | Send notifications |
| `factory dashboard` | Launch web dashboard |
| `factory tmux` | Launch in tmux session |
| `factory runners list` | List available runners |
| `factory usage` | Show usage statistics |

---

## 19. State & Persistence

### Files the factory creates and manages

```
.factory/
├── config.json              # Machine-readable project config (from factory.md)
├── eval_profile.json        # Eval dimensions and scoring profile
├── events.jsonl             # Append-only event log (all lifecycle events)
├── last_eval.json           # Most recent eval results
├── results.tsv              # Experiment history (one row per experiment)
├── state/
│   └── cycle.json           # In-flight cycle state (mode, respawns, runner)
├── checkpoint.json          # Crash recovery checkpoint
├── strategy/
│   ├── current.md           # Current strategy / spec / hypotheses
│   ├── backlog.md           # Persistent backlog items
│   ├── observations.md      # Latest study output
│   └── research.md          # Latest research report
├── reviews/
│   ├── <role>-latest.md     # Latest output from each agent
│   ├── ceo-verdict-<role>.md # CEO review verdict per agent
│   ├── archivist-checkpoints.md # Archival compliance tracking
│   └── session-summary.md   # End-of-cycle summary
├── archive/
│   ├── experiments/          # Per-experiment notes
│   ├── strategies/           # Strategy snapshots
│   ├── sources/              # Research source notes
│   └── patterns/             # Cross-project patterns
├── experiments/
│   └── NNN/                  # Per-experiment artifacts
│       ├── eval_before.json
│       ├── eval_after.json
│       └── verdict.json
└── worktrees/                # Git worktree management
```

### Event types in `events.jsonl`

Key events: `detect`, `sprint.started`, `sprint.completed`, `agent.started`, `agent.completed`, `agent.failed`, `experiment.begin`, `experiment.finalize`, `eval.started`, `eval.completed`, `precheck.completed`, `ceo.respawn`, `cycle.aborted`.

---

## 20. Mode Selection Cheat Sheet

| I want to... | Mode | Command |
|---|---|---|
| Start from a vague idea | Phase 0 → Build | `factory ceo --mode interactive` |
| Build a project from a spec | Build | `factory ceo <path>` (auto-detected) |
| Set up factory on existing project | Discover → Review | `factory ceo <path>` (auto-detected) |
| Improve an existing project | Improve | `factory ceo <path>` (auto-detected) |
| Improve a specific thing | Targeted Improve | `factory ceo <path> --focus "the thing"` |
| Quick fix after a cycle | Refine | `factory ceo <path> --refine "fix the typo"` |
| Optimize a research benchmark | Research | `factory ceo <path> --mode research` |
| Improve the factory itself | Meta | `factory ceo <path> --mode meta` |
| Run continuously | Loop | `factory run <path> --loop --interval 3600` |
