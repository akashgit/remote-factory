# Expected Behavior: CEO Agent

## 1. Identity & Responsibility

The CEO is the autonomous executive orchestrator of the Software Factory. It detects project state, selects the appropriate workflow, spawns specialist agents (`factory agent <role>`), reviews their outputs at every step, makes keep/revert verdicts based on eval data, and ensures archival. It owns the experiment lifecycle from `factory begin` through `factory finalize` and manages git, GitHub issues/PRs, and notification workflows.

**What the CEO IS:**
- An executive orchestrator that delegates ALL technical work to specialist agents
- The quality gate — reviews every agent output before proceeding (PROCEED / REDIRECT / ABORT)
- The experiment lifecycle owner — calls `factory begin`, `factory finalize`, manages branches and PRs
- The strategic decision-maker — approves/rejects Strategist plans, makes keep/revert verdicts

**What the CEO is NOT:**
- NOT a code writer — never uses `Edit`/`Write` on source files (Sacred Rule 8)
- NOT a researcher — never uses `WebSearch`/`WebFetch` (Sacred Rule 8)
- NOT an eval runner — never runs `pytest`/`ruff`/`mypy`/`python eval/score.py` directly (Sacred Rule 8)
- NOT a passive pipeline — it actively reviews, redirects, and aborts agents

**Relationship to other agents:**
- Spawns all agents via `factory agent <role> --task "..." --project $PROJECT_PATH`
- Reads agent output from `.factory/reviews/<role>-latest.md`
- Writes verdicts to `.factory/reviews/ceo-verdict-<role>.md`
- Max 2 REDIRECTs per agent per gate; max 3 RELOOP iterations per phase

---

## 2. Per-Workflow Behavior

### Workflow: Build

**Phase:** Full build lifecycle — Research (parallel) → Strategy → Builder (per phase) → Eval → Archival
**Spawned by:** `factory ceo <idea_or_path>` when state is `no_repo` or `incomplete`; or `factory run` with build mode
**Inputs received:**
  - Raw idea string or spec file path
  - Project path (may not exist yet)
  - `.factory/config.json` (if project exists)

**Expected process (ordered steps):**
  1. Create task list via `TaskCreate` (3 tasks: Plan Loop, Build, E2E gate)
  2. **Phase 1: Parallel Research** — spawn 3 researchers in a SINGLE Bash call with `&` + `wait`:
     ```bash
     factory agent researcher --review-tag similar --task "..." --project "$PROJECT_PATH" --timeout 600 &
     factory agent researcher --review-tag techstack --task "..." --project "$PROJECT_PATH" --timeout 600 &
     factory agent researcher --review-tag pitfalls --task "..." --project "$PROJECT_PATH" --timeout 600 &
     wait
     ```
  3. **Barrier:** Read all 3 outputs (`research-similar.md`, `research-techstack.md`, `research-pitfalls.md`), combine into `research-combined.md`
  4. **CEO Review — Research:** Read combined research, write verdict to `ceo-verdict-research.md`
  5. **Phase 2: Strategist** — spawn synchronously, reads `research-combined.md`, writes phased build plan to `current.md`
  6. **CEO Review — Strategy (HARD GATE):** Verify: (a) every phase has Category/What/Why/Expected impact, (b) architecture cites research, (c) buildable without clarification, (d) Phase 1 = scaffold + eval harness, (e) Deferred section only has items needing human intervention. Write "PLAN APPROVED" if all pass.
  7. **Phase 3: Archivist Plan** — fire-and-forget with `&`, `--model haiku`
  8. **Phase 4: Builder** — spawn synchronously for each phase in `current.md`. Read `ceo-verdict-strategist.md`.
  9. **CEO Review — Build:** Read builder output + `git log` + PR diff. Verify work matches plan.
  10. **Phase 5: Evaluator** — spawn synchronously, runs `factory eval`
  11. **Gate: Precheck** — `factory precheck $PROJECT_PATH --score-before 0 --score-after 0`
  12. **Phase 6: Archivist Build** — fire-and-forget with `&`, `--model haiku`

**Expected outputs/artifacts:**
  - `.factory/strategy/research-similar.md` — similar projects analysis
  - `.factory/strategy/research-techstack.md` — tech stack recommendations
  - `.factory/strategy/research-pitfalls.md` — pitfalls and scope
  - `.factory/strategy/research-combined.md` — CEO-written combined research
  - `.factory/strategy/current.md` — phased build plan
  - `.factory/reviews/ceo-verdict-research.md` — research gate verdict
  - `.factory/reviews/ceo-verdict-strategy.md` — strategy gate verdict (must contain "PLAN APPROVED")
  - `.factory/reviews/ceo-verdict-build.md` — build gate verdict
  - `.factory/reviews/builder-latest.md` — builder output
  - `.factory/reviews/evaluator-latest.md` — eval results
  - `.factory/archive/plan.md` — archived research + strategy
  - `.factory/archive/build.md` — archived build results

**Handoff:** After all phases built and evaluated, CEO transitions to Discover mode if no factory config exists, or to Improve mode if factory is initialized. Deferred items must be extracted to `.factory/strategy/deferred.md` via `factory deferred-list` before transition (playbook rule ceo-00006).

**Known failure: #783 — Build mode respawn loop.** Build mode builders commit code and open PRs but never call `factory finalize`, so `results.tsv` stays empty. On CEO timeout + respawn, `_detect_incomplete()` reads `_count_verdicts()` = 0 and reports "0/N phases complete" even when all phases were built. Session 2 then overwrites `current.md` with improve-mode content, destroying the build plan. Session 3 sees 0 hypotheses and re-runs discovery. **Trace signal:** Check `results.tsv` — if header-only after build phases completed, this bug is active. Also check for `current.md` being rewritten outside the Strategist phase.

---

### Workflow: Design

**Phase:** Identical to Build except Strategy gate is a User Approval steering point
**Spawned by:** `factory ceo <idea_or_path> --mode design` or `factory ceo <path> --mode design`
**Inputs received:**
  - Same as Build

**Expected process (ordered steps):**
  1. Steps 1–5 identical to Build (parallel research → barrier → CEO review → Strategist)
  2. **Steering Point — Strategy (User Approval):** Present strategy to user, wait for approval or feedback. On feedback, re-run Strategist with corrections (max 3 iterations). This REPLACES the CEO Strategy gate — no CEO verdict written.
  3. Steps 7–12 identical to Build (Archivist → Builder → CEO review → Evaluator → Precheck → Archivist)

**Expected outputs/artifacts:**
  - Same as Build, except NO `.factory/reviews/ceo-verdict-strategy.md` — user approval replaces it

**Handoff:** Same as Build

---

### Workflow: Discover

**Phase:** Auto-discover eval dimensions and generate eval harness
**Spawned by:** `factory ceo <path>` when state is `no_factory`
**Inputs received:**
  - Project path with existing repo but no `.factory/` setup
  - Project source code, README, config files

**Expected process (ordered steps):**
  1. Create task list (2 tasks: Discover eval dimensions, Review and approve evals)
  2. **Step: Discover** — run `factory discover $PROJECT_PATH` (internally spawns Researcher in Mode 1)
  3. **CEO Review — Discover:** Read `.factory/eval_profile.json` and `eval/score.py`. Verify: (a) dimensions relevant to project, (b) score.py looks correct, (c) no missing dimensions. Write verdict to `ceo-verdict-discover.md`.
  4. **Step: Redetect** — run `factory detect $PROJECT_PATH` to update state

**Expected outputs/artifacts:**
  - `.factory/eval_profile.json` — eval dimensions with weights and commands
  - `eval/score.py` — standalone eval script
  - `.factory/agents/<role>.md` — optional agent overrides
  - `.factory/reviews/ceo-verdict-discover.md` — discover gate verdict

**Handoff:** After redetect, state should be `evals_pending_review` → CEO transitions to Review workflow

---

### Workflow: Review

**Phase:** Verify eval dimensions work, create factory.md, run baseline eval
**Spawned by:** `factory ceo <path>` when state is `evals_pending_review`
**Inputs received:**
  - Project path with `.factory/eval_profile.json` existing but `human_reviewed: false`

**Expected process (ordered steps):**
  1. Create task list (2 tasks: Test eval dimensions, Initialize factory config)
  2. **Step: Eval Test** — run `cd $PROJECT_PATH && python eval/score.py`
  3. **CEO Review — Eval:** Check all dimensions produce valid scores. If any dimension fails, dispatch Builder to fix it. PROCEED only when all pass.
  4. **Step: Mark Reviewed** — set `human_reviewed: true` in `eval_profile.json`
  5. **Phase 1: CEO — Create factory.md** — spawn CEO agent to generate `factory.md` from template. Fill in Goal, Scope, Guards, Eval command, Threshold, Smoke Test.
  6. **Step: Factory Init** — run `factory init $PROJECT_PATH`
  7. **Step: Baseline Eval** — spawn evaluator agent
  8. **Step: Commit** — `git add factory.md eval/score.py .factory/ && git commit`
  9. **CEO Review — E2E:** Run Smoke Test from `factory.md`. For pre-existing projects entering factory first time, MUST verify before transitioning.

**Expected outputs/artifacts:**
  - `.factory/reviews/ceo-verdict-eval.md` — eval gate verdict
  - `factory.md` — factory configuration
  - `.factory/config.json` — parsed factory config
  - `.factory/reviews/ceo-verdict-e2e.md` — E2E verification verdict

**Handoff:** After E2E passes, state becomes `has_factory` → CEO transitions to Improve workflow

---

### Workflow: Improve

**Phase:** Systematic experimentation loop — Observe → Research → Hypothesize → Build → Eval → Verdict → Archive
**Spawned by:** `factory ceo <path>` when state is `has_factory`; or `factory run <path>`
**Inputs received:**
  - Project path with `.factory/config.json` and experiment history
  - `.factory/strategy/backlog.md` — work queue
  - `.factory/strategy/observations.md` — generated by `factory study`
  - Optional: `--focus <target>` for targeted mode

**Expected process (ordered steps):**
  1. Create task list (4 tasks: Observe, Hypothesize, Execute, Final Archive)
  2. **Phase 1: Observe** — run `factory study $PROJECT_PATH`, writes `observations.md`
  3. **Phase 2: Researcher** — spawn synchronously (Mode 2), reads `observations.md`, writes `research-local.md`
  4. **CEO Review — Research:** Read `research-local.md`. Check: grounded in data? Web research useful? Blind spots? No calendar-time estimates (REDIRECT if present). Write verdict.
  5. **Phase 3: Strategist** — spawn synchronously. Reads `observations.md`, `research-local.md`, `backlog.md`. Writes `current.md` with hypotheses.
  6. **CEO Review — Strategy (HARD GATE):** Verify: (a) specific enough to implement, (b) scoped to one PR, (c) realistic eval impact, (d) follows FEEC priority, (e) not redundant with reverted experiment, (f) at least one growth hypothesis with explicit `**Growth dimension:**` tag, (g) backlog convergence. Write "PLAN APPROVED with approved hypotheses in priority order."
  7. **Step: Begin** — `factory begin $PROJECT_PATH --hypothesis "<H1 title>"`
  8. **Phase 4: Builder** — spawn synchronously. Reads `current.md` and CEO verdict. Implements hypothesis, runs tests, opens draft PR.
  9. **CEO Review — Build:** Read builder output + PR diff (`gh pr diff <number>`). Verify: work matches hypothesis, no scope creep, tests included. If PR obviously wrong → ABORT immediately (don't waste QA).
  10. **Phase 5: Evaluator** — spawn synchronously, runs `factory eval`
  11. **Gate: Precheck** — `factory precheck $PROJECT_PATH --score-before <before> --score-after <after>`
  12. **Step: Finalize** — `factory finalize $PROJECT_PATH --id $EXP_ID --verdict keep|revert --notes "ceo:keep|revert <details>"`
  13. **Phase 6: Archivist** — fire-and-forget with `&`, `--model haiku`
  14. **Loop:** If more approved hypotheses remain, return to step 7 for next hypothesis
  15. **Final Archive:** Blocking archivist invocation at cycle end (no `&`)

**Expected outputs/artifacts:**
  - `.factory/strategy/observations.md` — study output
  - `.factory/strategy/research-local.md` — researcher findings
  - `.factory/strategy/current.md` — prioritized hypotheses
  - `.factory/reviews/ceo-verdict-research.md` — research gate verdict
  - `.factory/reviews/ceo-verdict-strategy.md` — strategy gate verdict (must contain "PLAN APPROVED")
  - `.factory/reviews/ceo-verdict-build.md` — build gate verdict
  - `.factory/reviews/builder-latest.md` — builder output
  - `.factory/reviews/evaluator-latest.md` — eval results
  - `.factory/archive/experiment.md` — archived experiment results
  - `results.tsv` — updated with experiment verdict row

**Handoff:** After all approved hypotheses have verdicts AND final archive complete, cycle exits. If `--loop` flag set, wait for interval then start new cycle.

---

### Workflow: Research

**Phase:** Metric-driven optimization loop with failure analysis — Baseline → Failure Analysis → Research → Hypothesize → Build → Eval → Verdict → Plateau Gate
**Spawned by:** `factory ceo <path> --mode research` when `research_target` is configured in `factory.md`
**Inputs received:**
  - Project path with `research_target` in `.factory/config.json`
  - `mutable_surfaces` and `fixed_surfaces` declarations
  - Research constraints

**Expected process (ordered steps):**
  1. Create task list (6 tasks: Baseline, Analyze, Research, Hypothesize, Execute, Verdict)
  2. **Step: Baseline** — spawn evaluator to measure current metric
  3. **Phase 1: Failure Analyst** — spawn synchronously. Reads run artifacts at `.factory/research/runs/`. Classifies failures by type, computes distribution. Writes `failure_analysis.md`. **No CEO review gate** — output consumed directly by Researcher.
  4. **Phase 2: Researcher** — spawn synchronously (Mode 4 — failure-targeted). Reads `failure_analysis.md`. Searches web for solutions to dominant failure modes. Writes `research-local.md`.
  5. **CEO Review — Research:** Same criteria as Improve.
  6. **Phase 3: Strategist** — spawn synchronously. Reads `failure_analysis.md` and `research-local.md`. Generates 1–3 hypotheses targeting dominant failure modes. Each hypothesis names specific `mutable_surfaces` files. MUST NOT propose changes to `fixed_surfaces`.
  7. **CEO Review — Strategy (HARD GATE):** Same as Improve, plus verify: targets failure modes, names mutable surface files, avoids fixed surfaces, 1–3 hypotheses. Growth dimension tag NOT required in research mode.
  8. Steps 7–13: Same as Improve (Begin → Builder → CEO Review → Evaluator → Precheck → Finalize → Archivist)
  9. **Gate: Plateau** — automated check: reads `results.tsv`, compares last 2 scores. If improved → RELOOP to baseline (max 3 iterations). If no improvement → exit.

**Expected outputs/artifacts:**
  - Same as Improve, plus:
  - `.factory/strategy/failure_analysis.md` — failure classification and distribution
  - Plateau gate decision in trace

**Handoff:** On plateau (no improvement in last 2 scores), cycle exits. On RELOOP, returns to baseline measurement.

---

### Workflow: Meta

**Phase:** Cross-project insights → playbook evolution → test pruning
**Spawned by:** `factory ceo <path> --mode meta`
**Inputs received:**
  - Project path (typically the factory itself)
  - Cross-project experiment data accessible via `factory insights`

**Expected process (ordered steps):**
  1. Create task list (5 tasks: Observe, Hypothesize, Execute, Final Archive, Evolve playbooks)
  2. **Step: Insights** — run `factory insights $PROJECT_PATH`, writes `insights.md`
  3. **Phase 1: Researcher** — spawn synchronously (Mode 3 — self-improvement). Reads `insights.md` and current playbooks. Identifies patterns, anti-patterns, improvement opportunities. Writes `research-local.md`.
  4. **CEO Review — Research:** Check: cross-project patterns well-supported by data? Proposed improvements actionable?
  5. **Phase 2: Strategist** — spawn synchronously. Reads `research-local.md`. Proposes specific playbook edits (DO/DON'T bullets) with supporting evidence. Writes `playbook-diffs.md`.
  6. **Steering Point — User Approval:** Present playbook diffs to user. Wait for approval or feedback. On feedback, re-run Strategist (max 3 iterations).
  7. **Step: Apply Playbooks** — run `factory ace $PROJECT_PATH`
  8. **Phase 3: Archivist** — fire-and-forget, archives playbook evolution to `.factory/archive/meta.md`
  9. **Step: Test Collect** — run `pytest --co -q 2>/dev/null || true`
  10. **Phase 4: Test Researcher** — spawn synchronously. Analyzes test inventory for redundant/dead/flaky tests. Writes `test-analysis.md`.
  11. **Steering Point — Test Prune (User Approval):** Present test analysis to user. Wait for approval or feedback.
  12. **Phase 5: Test Builder** — spawn synchronously. Deletes approved redundant tests. Verifies remaining suite passes.

**Expected outputs/artifacts:**
  - `.factory/strategy/insights.md` — cross-project patterns
  - `.factory/strategy/research-local.md` — pattern analysis
  - `.factory/strategy/playbook-diffs.md` — proposed playbook edits
  - `.factory/archive/meta.md` — archived playbook evolution
  - `.factory/strategy/test-analysis.md` — redundant test analysis
  - `.factory/reviews/test-pruning-latest.md` — test deletion results

**Handoff:** After test pruning complete (or user declines), cycle exits.

---

### Workflow: Refine

**Phase:** Lightweight pipeline for user-directed refinements — Classify → Build → QA → Verdict → Archive
**Spawned by:** `factory ceo <path> --refine "<request>"` or user says "refine X" in foreground mode
**Inputs received:**
  - Project path with factory initialized
  - User's refinement request string

**Expected process (ordered steps):**
  1. **Phase 1: Refiner** — spawn synchronously. Reads CLAUDE.md and factory.md. Classifies request as Tier 1 (1–3 files, <50 lines), Tier 2 (3–8 files, 50–200 lines), or Tier 3 (8+ files, architectural).
  2. **CEO Review — Refiner:** Verify tier classification reasonable, files correct, Builder task specific enough.
  3. **Gate: Tier (Automated)** — if "Tier 3" in refiner output → HALT. Tier 3 requests are too large for Refine; user must use full Improve mode.
  4. **Step: Begin** — `factory begin $PROJECT_PATH --hypothesis "Refine: <request>"`
  5. **Step: Create Issue** — `gh issue create --title "Refine: <request>" --label "refinement"`
  6. **Phase 2: Builder** — spawn synchronously. Reads `refiner-latest.md` and GitHub issue.
  7. **Phase 3: Reviewer (QA)** — spawn synchronously. Runs 3-section verification: Health Check, Code Review (7-category checklist + `factory guard --check-scope`), Adversarial QA.
  8. **CEO Review — QA:** Read QA output. If issues found → REDIRECT to Builder (max 3 iterations to builder, unlike standard max 2).
  9. **Gate: Precheck** — automated eval gate
  10. **Step: Finalize** — `factory finalize $PROJECT_PATH --id $EXP_ID --verdict keep`
  11. **Phase 4: Archivist** — fire-and-forget, archives to `.factory/archive/refinement.md`

**Expected outputs/artifacts:**
  - `.factory/reviews/refiner-latest.md` — tier classification + Builder task
  - `.factory/reviews/ceo-verdict-refiner.md` — refiner gate verdict
  - `.factory/reviews/builder-latest.md` — builder output
  - `.factory/reviews/qa-latest.md` — 3-section verification report
  - `.factory/reviews/ceo-verdict-qa.md` — QA gate verdict
  - `.factory/archive/refinement.md` — archived refinement

**Handoff:** After finalize + archive, exits. In foreground mode, CEO may enter refinement loop for follow-up requests.

---

## 3. Invariants (MUST always hold)

**INV-1: Sacred Rule 8 — No agent job theft.**
> "You do NOT write application code, fix bugs, run evals directly, do research, or perform any work that a specialist agent should do. When an agent fails, you re-invoke it with better instructions or abort — you never take over its job."
> — `ceo.md:40`

Trace check: CEO should never have `Edit`/`Write` tool calls on `*.py`, `*.ts`, `*.go` files (except `.factory/reviews/`). CEO should never have `WebSearch`/`WebFetch` tool calls. CEO should never have `Bash` calls running `pytest`, `ruff`, `mypy`, `npm test`, `python eval/score.py`.

**INV-2: Strategy gate is a HARD GATE.**
> "The Builder MUST NOT start until you explicitly approve the Strategist's plan. Before writing `PLAN APPROVED`, verify: 1) At least one hypothesis has an explicit `**Growth dimension:**` tag naming one of the 5 growth dimensions 2) That hypothesis is genuinely growth."
> — `ceo.md:209-213`

Trace check: No `factory agent builder` call should appear before `ceo-verdict-strategy.md` is written with "PLAN APPROVED". In Improve/Research modes, the verdict must list approved hypotheses in priority order.

**INV-3: Cycle completion — no self-judged early exits.**
> "Self-judged early exits are FORBIDDEN. Do not exit because: 'This is a good stopping point', 'This is beyond the scope of a single session', 'The scaffold is complete'."
> — `ceo.md:52-56`

Trace check: All approved hypotheses must have a corresponding `factory finalize` call (Improve/Research) or all planned phases must have Builder invocations (Build). Valid exit conditions: (1) all planned work complete, (2) unrecoverable failure with `cycle.aborted` event, (3) user interrupt.

**INV-4: QA verification is mandatory for every PR.**
> "The QA Agent (health check + code review + adversarial QA) MUST execute for every experiment that produces a PR. 'The change is small' is not a valid reason to skip."
> — `ceo.md:356` (Sacred Rule 9)

Trace check: Every `gh pr create` must be followed by an `agent.started agent=qa` event.

**INV-5: Archival after every verdict AND at cycle end.**
> "Do not skip archival — the Archivist must fire after each verdict (async) and at cycle end (blocking final archive)."
> — `ceo.md:354` (Sacred Rule 7)

Trace check: Every `factory finalize` must be followed within 60s by `agent.started agent=archivist`. Cycle end (`sprint.completed` event) must be preceded by a blocking archivist invocation (not `&`).

**INV-6: Synchronous subagent invocation by default.**
> "All subagent invocations MUST be synchronous unless explicitly listed as exceptions."
> — `ceo.md:76`

Exceptions: (1) Parallel researchers via single Bash call with `&` + `wait` + `--review-tag`, (2) Archivist fire-and-forget via `&` in single Bash call.

**INV-7: Never use `run_in_background: True` for `factory agent` commands.**
> "Do NOT use `run_in_background: True` on the Bash tool — that returns immediately and the runner never captures output."
> — `ceo.md:95` and playbook rule `ceo-00009`

Trace check: No Bash tool call containing `factory agent` should have `run_in_background: true`.

**INV-8: At least one growth hypothesis per cycle (Improve/Meta modes).**
> "If ALL hypotheses are hygiene-only (tests, lint, type_check, coverage, bugfixes, cleanup, refactoring, dependency updates), you MUST REDIRECT the Strategist. No exceptions."
> — `ceo.md:202`

Growth dimensions: `capability_surface`, `experiment_diversity`, `observability`, `research_grounding`, `factory_effectiveness`. NOT growth: tests, lint, type_check, coverage, bugfixes, cleanup, refactoring, dependency updates, CI fixes.

---

## 4. Constraints & Forbidden Actions

**Forbidden tool usage:**
- `Edit` or `Write` on any file outside `.factory/reviews/` — Sacred Rule 8
- `WebSearch` or `WebFetch` — Sacred Rule 8
- `Bash` running `pytest`, `ruff`, `mypy`, `npm test`, `python eval/score.py` — Sacred Rule 8
- `Bash` with `run_in_background: True` for `factory agent` commands — playbook ceo-00009

**Forbidden workflow actions:**
- Merging PRs (`gh pr merge`) — Sacred Rule 6
- Deleting or overwriting existing tests — Sacred Rule 1
- Modifying files outside declared scope — Sacred Rule 2
- Lowering eval threshold — Sacred Rule 4
- Skipping eval step (finalizing without `score_after`) — Sacred Rule 5
- Skipping QA verification for any PR — Sacred Rule 9
- Skipping archival after verdicts or at cycle end — Sacred Rule 7
- Introducing secrets or credentials in repo — Sacred Rule 3

**Forbidden exit reasons:**
- "This is a good stopping point" — playbook ceo-00007
- "Phase 1 is complete and documented" — playbook ceo-00007
- "This is beyond the scope of a single session" — playbook ceo-00008
- "Strategy is ready for execution" — playbook ceo-00008

**Forbidden parallel patterns:**
- Separate Bash tool calls with `run_in_background: True` for researchers
- Sequential Bash calls each backgrounded individually
- Using `tail -f` to wait for subagent output
- Polling for subagent completion

**Permitted parallel patterns (exhaustive):**
- Single Bash call: `factory agent researcher --review-tag X ... & factory agent researcher --review-tag Y ... & wait`
- Single Bash call: `factory agent archivist ... &` (fire-and-forget, no `wait`)

---

## 5. Failure Modes & Diagnostic Signals

| Failure mode | Trace signal | Example issue |
|---|---|---|
| **Build mode respawn loop** — CEO respawns report "0/N phases complete" because build mode has no verdict tracking in `results.tsv` | `results.tsv` has only header row after build phases completed. Respawn input says "0/N phases complete" when git log shows N commits. `current.md` overwritten outside Strategist phase. | #783 |
| **Duplicate researcher spawning** — CEO uses `run_in_background: True` instead of shell `&` + `wait`, causing 2x token spend | `agent.started` count for `researcher` role exceeds 3 in Build/Design mode. Bash tool calls show `run_in_background: true` with `factory agent` commands. Output files missing immediately after tool call returns. | #763 |
| **Sacred Rule 8 violation** — CEO writes code or runs evals directly instead of delegating | CEO tool use shows `Edit`/`Write` on `*.py`/`*.ts`/`*.go` files. CEO tool use shows `Bash` running `pytest`/`ruff`/`mypy`. No `agent.started` event after agent failure. | #582 (proposal) |
| **QA/Archivist skip in build mode** — CEO exits after eval without spawning QA or Archivist | `sprint.completed` event without preceding `agent.started agent=qa` event. PR exists without QA trace. No `agent.started agent=archivist` in session. | #723 |
| **Hygiene-only strategy approved** — CEO approves Strategist plan with no growth hypothesis | `ceo-verdict-strategy.md` contains "PLAN APPROVED" but `current.md` has no `**Growth dimension:**` tags. All hypotheses are tests/lint/cleanup. | — |
| **Self-judged early exit** — CEO exits mid-cycle with rationalization | `sprint.completed` event with fewer `factory finalize` calls than approved hypotheses. Exit text contains "good stopping point" or "beyond the scope of a single session". | playbook ceo-00007, ceo-00008 |
| **Strategy file mutation during resume** — On respawn, CEO overwrites `current.md` instead of preserving the build plan | `current.md` diff shows it changed from build-plan format (Phase headings) to improve-mode format (observations/hypotheses). No `agent.started agent=strategist` event corresponding to the change. | #783 (secondary) |

---

## 6. Interaction Protocol

**How results are communicated:**
- CEO reads agent output from `.factory/reviews/<role>-latest.md` (auto-saved by `factory agent`)
- For tagged researchers: `.factory/reviews/researcher-<tag>-latest.md`
- CEO writes verdicts to `.factory/reviews/ceo-verdict-<role>.md`

**Verdict format (every CEO review gate):**
```markdown
## CEO Review: <Role> Agent
- **Verdict:** PROCEED | REDIRECT | ABORT
- **Rationale:** <why — cite specific evidence from agent output>
- **Issues found:** <list, or "none">
- **Instructions for next step:** <what to tell next agent, or corrections for re-invoke>
```

**Review criteria by role:**

| Role | Check for |
|---|---|
| Researcher | Covered right topics? Enough depth? Web research included? Gaps? **No calendar-time estimates** — REDIRECT if present. |
| Strategist | Plan aligns with goals? Phases right-sized? **At least one growth hypothesis?** **No calendar-time estimates** — REDIRECT if present. |
| Builder | PR matches plan? No scope creep? Tests included? CLAUDE.md followed? |
| QA | All 3 sections present (Health, Review, Adversarial QA)? Verdict structured? Issues have file:line? Feature actually executed (not just claimed)? |

**Experiment finalization notes format:**
- Keep: `--notes "ceo:keep score_delta=+0.05 key_improvement=<what> qa_iterations=$QA_ITERATION"`
- Revert: `--notes "ceo:revert reason=<why> qa_iterations=$QA_ITERATION"`
- Error: `--notes "ceo:error builder_failed=true reason=<summary>"`
