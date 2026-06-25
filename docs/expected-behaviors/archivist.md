# Archivist Agent — Expected Behavior Specification

## 1. Identity & Responsibility

The Archivist agent is the Software Factory's institutional memory keeper. It produces dual output — human-readable markdown AND structured JSON sidecars — for every experiment. It maintains the CEO's cross-cycle memory file (`memory.json`), records cross-project patterns, proposes playbook improvements from high-impact experiments, and regenerates the performance report after each archival. The Archivist always runs on the `haiku` model and always executes asynchronously (fire-and-forget) except at cycle end where it blocks.

**What the Archivist IS:**
- A dual-format recorder (markdown + JSON sidecar for every experiment)
- A cross-cycle memory maintainer (`memory.json` with patterns, anti-patterns, agent performance)
- A playbook improvement proposer (via `playbook_proposals` in JSON sidecars)
- A performance report regenerator (runs `factory report-update` after every archival)

**What the Archivist IS NOT:**
- NOT a decision-maker — it records outcomes, it does not decide keep/revert
- NOT an analyst — it records observations from experiment data, it does not research or investigate
- NOT a code modifier — it writes ONLY to `.factory/archive/`, never to source code
- NOT a quality gate — it does not verify or test the Builder's work
- NOT a strategist — it does not generate hypotheses or scope work

**Relationship to other agents:**
- **CEO** spawns the Archivist at two points: (1) fire-and-forget after each experiment verdict, (2) blocking at cycle end for final archival. CEO does NOT wait for fire-and-forget invocations.
- **Builder/QA/Evaluator** produce the experiment data the Archivist records — the Archivist reads their outputs but does not interact with them
- **Strategist** benefits from Archivist's `memory.json` and pattern records in future cycles, but does not interact directly during the current cycle
- **All other agents** are upstream data producers — the Archivist is always the last agent in any workflow phase

---

## 2. Per-Workflow Behavior

### Workflow: Build

The Archivist is invoked twice in the Build workflow:

#### Invocation 1: Archivist Plan (Phase 3)

**Phase:** Phase 3 — Archivist Plan (after Strategist approval, before Builder)
**Spawned by:** CEO, fire-and-forget via:
```bash
factory agent archivist --task "Archive the approved research and strategy.
Read: .factory/strategy/current.md
Write output to: .factory/archive/plan.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
**Inputs received:**
- `.factory/strategy/current.md` — the CEO-approved build plan (Strategist output)
- `.factory/strategy/research-combined.md` — combined research outputs (from parallel researchers)
- `.factory/reviews/ceo-verdict-strategy.md` — CEO's strategy approval

**Expected process (ordered steps):**
1. Read the approved build plan from `.factory/strategy/current.md`
2. Read combined research from `.factory/strategy/research-combined.md`
3. Write archived plan to `.factory/archive/plan.md` with frontmatter (tags, project, source, date)
4. Run `factory report-update "$PROJECT_PATH"`

**Expected outputs/artifacts:**
- `.factory/archive/plan.md` — archived research and strategy for this build cycle

**Handoff:** None — fire-and-forget. CEO continues immediately to Builder without waiting. The Archivist runs concurrently with the Builder phase.

#### Invocation 2: Archivist Build (Phase 6)

**Phase:** Phase 6 — Archivist Build (after Evaluator, final phase)
**Spawned by:** CEO, fire-and-forget via:
```bash
factory agent archivist --task "Archive the build phase results.
Read: .factory/reviews/evaluator-latest.md
Write output to: .factory/archive/build.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
**Inputs received:**
- `.factory/reviews/evaluator-latest.md` — Evaluator's score report
- `.factory/reviews/builder-latest.md` — Builder's output (available on disk)
- `.factory/reviews/ceo-verdict-build.md` — CEO's build review

**Expected process (ordered steps):**
1. Read evaluator results from `.factory/reviews/evaluator-latest.md`
2. Read builder output from `.factory/reviews/builder-latest.md`
3. Write build results archive to `.factory/archive/build.md`
4. Run `factory report-update "$PROJECT_PATH"`

**Expected outputs/artifacts:**
- `.factory/archive/build.md` — archived build phase results

**Handoff:** None — fire-and-forget. This is the last phase of the Build workflow.

---

### Workflow: Improve

**Phase:** Phase 6 — Archivist (after `factory finalize`, final phase)
**Spawned by:** CEO, fire-and-forget via:
```bash
factory agent archivist --task "Archive experiment results and learnings.
Read: .factory/experiments/verdict.json
Write output to: .factory/archive/experiment.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
**Inputs received:**
- `.factory/experiments/verdict.json` — experiment verdict data (experiment ID, hypothesis, scores, verdict)
- Experiment context: ID, hypothesis text, score_before, score_after, delta, CEO verdict (keep/revert), CEO rationale
- PR number and issue number (from experiment history)

**Expected process (ordered steps):**
1. Read experiment verdict from `.factory/experiments/verdict.json`
2. Read experiment history and context
3. **Write experiment notes (dual output):**
   - Markdown: `.factory/archive/experiments/{project}-{NNN}.md` with frontmatter (tags, project, experiment_id, verdict, score_delta, date, source)
   - JSON sidecar: `.factory/archive/experiments/{NNN}.json` with structured fields (experiment_id, hypothesis, category, verdict, scores, dimensions_changed, learned, anti_patterns, playbook_proposals, issue, pr, date)
4. **Update CEO memory file:** Read `.factory/archive/memory.json` (create with `[]` if missing), check for duplicates, append new entries with >=2 experiments as evidence, keep under 50 entries
5. **Update patterns:** If cross-project patterns observed, append to `.factory/archive/patterns/patterns.md`
6. Run `factory report-update "$PROJECT_PATH"`

**Expected outputs/artifacts:**
- `.factory/archive/experiments/{project}-{NNN}.md` — human-readable experiment notes
- `.factory/archive/experiments/{NNN}.json` — structured JSON sidecar
- `.factory/archive/memory.json` — updated cross-cycle memory (appended, not overwritten)
- `.factory/archive/patterns/patterns.md` — updated patterns (if applicable)
- Performance report regenerated via `factory report-update`

**Handoff:** None — fire-and-forget after each experiment verdict. At cycle end, the CEO invokes the Archivist as a blocking call (with `wait`) to ensure all experiments are recorded and the final cycle summary is complete.

**Improve-specific notes:**
- The Archivist fires after EVERY experiment verdict (keep or revert), not just at cycle end
- Post-verdict archival is async (fire-and-forget with `&`)
- Cycle-end archival is blocking (no `&`, CEO waits)
- If the fire-and-forget archival missed an experiment, the blocking cycle-end archival catches the gap

---

### Workflow: Research

**Phase:** Phase 5 — Archivist (after `factory finalize`, before Plateau Gate)
**Spawned by:** CEO, fire-and-forget via:
```bash
factory agent archivist --task "Archive experiment results and learnings.
Read: .factory/experiments/verdict.json
Write output to: .factory/archive/experiment.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
**Inputs received:**
- Same as Improve, plus:
- Research mode context: target metric, mutable/fixed surfaces, failure analysis data
- Failure mode classification from `.factory/strategy/failure_analysis.md`

**Expected process (ordered steps):**
- Same as Improve (dual output experiment notes, memory update, patterns, report-update)
- **Additional for research mode:**
  - Record which failure mode the hypothesis targeted
  - Record whether the intervention improved the target metric
  - Write source notes to `.factory/archive/sources/{source-name}.md` if research findings are worth preserving

**Expected outputs/artifacts:**
- Same as Improve
- `.factory/archive/sources/{source-name}.md` — per-finding source notes (if applicable)

**Handoff:** None — fire-and-forget. After the Archivist fires, the Research workflow runs the Plateau Gate to check if scores are still improving. If RELOOP, the entire cycle restarts from baseline. The Archivist's work is independent of the plateau decision.

---

### Workflow: Refine

**Phase:** Phase 4 — Archivist (after `factory finalize`, final phase)
**Spawned by:** CEO, fire-and-forget via:
```bash
factory agent archivist --task "Archive refinement experiment results and learnings.
Read: .factory/experiments/verdict.json
Write output to: .factory/archive/refinement.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
**Inputs received:**
- `.factory/experiments/verdict.json` — refinement experiment verdict
- Refinement context: user's original request, Refiner's classification (tier), Builder's implementation, QA verification results

**Expected process (ordered steps):**
1. Read experiment verdict from `.factory/experiments/verdict.json`
2. Write dual output experiment notes (markdown + JSON sidecar)
3. Write refinement archive to `.factory/archive/refinement.md`
4. Update CEO memory file if applicable
5. Run `factory report-update "$PROJECT_PATH"`

**Expected outputs/artifacts:**
- `.factory/archive/refinement.md` — archived refinement results
- `.factory/archive/experiments/{project}-{NNN}.md` — experiment notes (markdown)
- `.factory/archive/experiments/{NNN}.json` — experiment notes (JSON sidecar)
- `.factory/archive/memory.json` — updated if new patterns detected

**Handoff:** None — fire-and-forget. This is the last phase of the Refine workflow.

---

### Workflow: Meta

**Phase:** Phase 3 — Archivist (after `factory ace` applies playbook edits)
**Spawned by:** CEO, fire-and-forget via:
```bash
factory agent archivist --task "Archive playbook evolution results.
Read: .factory/archive/playbooks-applied.md
Write output to: .factory/archive/meta.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
**Inputs received:**
- `.factory/archive/playbooks-applied.md` — record of which playbook edits were applied by ACE
- Strategist's playbook diffs from `.factory/strategy/playbook-diffs.md`
- User approval context (what the user approved/rejected at the steering point)

**Expected process (ordered steps):**
1. Read applied playbook changes from `.factory/archive/playbooks-applied.md`
2. Write meta archive to `.factory/archive/meta.md` recording:
   - Which playbook rules were added/removed
   - Which agent roles were affected
   - What evidence supported the changes
3. Update CEO memory file with meta-learning insights
4. Run `factory report-update "$PROJECT_PATH"`

**Expected outputs/artifacts:**
- `.factory/archive/meta.md` — archived playbook evolution results

**Handoff:** None — fire-and-forget. CEO continues immediately to Test Collect phase without waiting.

---

## 3. Invariants (MUST always hold)

1. **Write ONLY to `.factory/archive/`:** "Write ONLY to `.factory/archive/` — NEVER to any other directory." (archivist.md:160) The Archivist's entire write domain is restricted to the archive directory.

2. **Dual output for experiment notes:** "Always produce BOTH markdown AND JSON for experiment notes." (archivist.md:161) Every experiment archival produces both `.factory/archive/experiments/{project}-{NNN}.md` AND `.factory/archive/experiments/{NNN}.json`. Producing only one format is a violation.

3. **Valid JSON:** "JSON must be valid — use proper escaping, no trailing commas." (archivist.md:162) The JSON sidecar must parse cleanly with `json.loads()`.

4. **Performance report regeneration:** "After writing archive notes, always run `factory report-update`." (archivist.md:164) Every Archivist invocation ends with `factory report-update "$PROJECT_PATH"`.

5. **Memory.json constraints:**
   - "Only add entries with >=2 experiments as evidence." (archivist.md:121)
   - "Check existing entries before adding — don't duplicate." (archivist.md:122)
   - "Keep the array under 50 entries — if over, remove the oldest entries with the fewest evidence items." (archivist.md:123)

6. **JSON sidecar field rules:**
   - `dimensions_changed`: "only dimensions where score moved >=0.05. Value is `[before, after]`." (archivist.md:83)
   - `learned`: "one sentence — the single most useful thing from this experiment." (archivist.md:84)
   - `anti_patterns`: "list of things that didn't work or should be avoided. Empty list if none." (archivist.md:85)
   - `playbook_proposals`: "only for high-impact experiments (score_delta >= 0.03 or clear pattern)." (archivist.md:86) Empty list if none.

7. **Complete quickly:** "Complete quickly — you run async and should not block the workflow." (archivist.md:163) The Archivist runs on haiku model with a 300s timeout and should finish well within that window.

8. **Vault fallback:** "If $FACTORY_VAULT_PATH is not set or is empty, write to .factory/ within the project directory instead. Do NOT fall back to the user's personal Obsidian vault." (playbook arch-00002)

---

## 4. Constraints & Forbidden Actions

- **MUST NOT** write to any directory outside `.factory/archive/` — no source code, no `.factory/strategy/`, no `.factory/reviews/`
- **MUST NOT** modify `eval/score.py` or any file outside the archive
- **MUST NOT** produce markdown-only experiment notes — JSON sidecar is mandatory
- **MUST NOT** produce JSON-only experiment notes — markdown is mandatory
- **MUST NOT** add memory.json entries with fewer than 2 experiments as evidence
- **MUST NOT** duplicate existing memory.json entries — check before adding
- **MUST NOT** let memory.json exceed 50 entries — evict oldest/weakest if over
- **MUST NOT** include `playbook_proposals` for low-impact experiments (score_delta < 0.03 and no clear pattern) — use empty list
- **MUST NOT** include dimensions in `dimensions_changed` where score moved less than 0.05
- **MUST NOT** block the workflow (except at cycle-end final archive) — all mid-cycle archival is fire-and-forget
- **MUST NOT** skip `factory report-update` after writing archive notes
- **MUST NOT** fall back to user's personal Obsidian vault when `$FACTORY_VAULT_PATH` is unset — use `.factory/` instead (playbook arch-00002)
- **MUST NOT** produce invalid JSON — no trailing commas, proper escaping

---

## 5. Failure Modes & Diagnostic Signals

| Failure mode | Trace signal | Example issue |
|---|---|---|
| **Missing JSON sidecar** — Archivist writes markdown experiment notes but skips the JSON sidecar | `.factory/archive/experiments/{NNN}.json` does not exist after archival. Only `.factory/archive/experiments/{project}-{NNN}.md` present. Downstream tooling that reads JSON sidecars fails. | Archivist produces markdown only, `factory insights` can't compute cross-experiment stats |
| **Invalid JSON** — Archivist produces a JSON sidecar with syntax errors (trailing commas, unescaped quotes) | `json.loads()` throws on `.factory/archive/experiments/{NNN}.json`. `factory report-update` fails with parse error. | Archivist writes JSON with trailing comma in `anti_patterns` array |
| **memory.json overflow** — Archivist keeps appending without evicting, exceeding 50 entries | `wc -l .factory/archive/memory.json` shows array length > 50. Memory becomes less useful as signal-to-noise ratio drops. | Archivist adds 10 entries per cycle for 6 cycles without eviction |
| **Duplicate memory entries** — Archivist adds entries that duplicate existing patterns | `.factory/archive/memory.json` contains entries with near-identical `text` fields. Evidence arrays overlap significantly. | Archivist adds "Observability experiments have high keep rate" when an identical entry already exists |
| **Skipped report-update** — Archivist writes archive notes but doesn't run `factory report-update` | No `Bash` tool call with `factory report-update` in Archivist trace. `.factory/performance_report.json` timestamp doesn't update after archival. | Archivist exits after writing notes, performance report is stale for downstream consumers |
| **Vault path leakage** — Archivist writes to user's personal Obsidian vault instead of `.factory/archive/` | `Write` tool calls target paths outside `.factory/archive/` (e.g., `~/Obsidian/vault/...`). Archive files appear in unexpected locations. | `$FACTORY_VAULT_PATH` points to personal vault, Archivist writes experiment notes there |
| **Blocking when should be async** — Archivist runs synchronously mid-cycle, blocking the CEO from continuing | No `&` in the Bash command. CEO's next agent invocation timestamp shows delay matching Archivist runtime. Workflow wall-clock time increases by 60-120s per archival. | CEO waits for Archivist to finish before spawning next Builder |
| **Silent failure (fire-and-forget)** — Archivist crashes during async execution, no archive produced, gap not caught until cycle end | No `.factory/archive/experiments/{NNN}.md` or `.json` for an experiment that had a verdict. Cycle-end blocking archive finds gaps. `agent.failed agent=archivist` event in `.factory/events.jsonl`. | Archivist hits timeout at 300s, experiment notes never written, caught at cycle-end blocking archive |

---

## 6. Interaction Protocol

### Results communication
- Archivist stdout is captured to the output path specified in the `--task` argument (varies by workflow):
  - Build: `.factory/archive/plan.md` (Phase 3) or `.factory/archive/build.md` (Phase 6)
  - Improve: `.factory/archive/experiment.md`
  - Research: `.factory/archive/experiment.md`
  - Refine: `.factory/archive/refinement.md`
  - Meta: `.factory/archive/meta.md`
- The CEO does NOT read Archivist output for decision-making (fire-and-forget) — except at cycle-end blocking archival

### Execution model
- **Model:** Always `haiku` (specified via `--model haiku`)
- **Timeout:** Always 300 seconds
- **Mid-cycle:** Always fire-and-forget (shell `&`, no `wait`)
- **Cycle-end:** Blocking (no `&`) — CEO waits for completion to ensure all experiments are archived before cycle exits
- **Invocation:** `factory agent archivist --task "..." --project "$PROJECT_PATH" --timeout 300 --model haiku &`

### Output file formats

**Markdown experiment note** (`.factory/archive/experiments/{project}-{NNN}.md`):
```markdown
---
tags: [factory, experiment, {project}]
project: {project}
experiment_id: {id}
verdict: {verdict}
score_delta: {delta}
date: {date}
source: factory-archivist
---

# Experiment #{id}: {hypothesis}

## Result
**{VERDICT}** — score changed from {before} to {after} ({delta})

## What Changed
{summary}

## What We Learned
{key insight from this experiment}

## Links
- Issue: #{issue}
- PR: #{pr}
```

**JSON sidecar** (`.factory/archive/experiments/{NNN}.json`):
```json
{
  "experiment_id": 42,
  "hypothesis": "Add structured logging",
  "category": "EXPLOIT",
  "verdict": "keep",
  "score_before": 0.72,
  "score_after": 0.80,
  "score_delta": 0.08,
  "dimensions_changed": {"observability": [0.4, 0.7]},
  "ceo_rationale": "Logging coverage jumped 40%, no regressions",
  "learned": "structlog.get_logger() at module level is the pattern",
  "anti_patterns": ["Don't mix print() and structlog"],
  "playbook_proposals": [
    {
      "role": "builder",
      "type": "DO",
      "content": "Use structlog.get_logger() at module level",
      "confidence": "high"
    }
  ],
  "issue": 42,
  "pr": 43,
  "date": "2026-06-21"
}
```

**CEO memory file** (`.factory/archive/memory.json`):
```json
[
  {
    "type": "pattern",
    "text": "Observability experiments have 95% keep rate",
    "evidence": [27, 33, 42],
    "date": "2026-06-21"
  }
]
```
Memory types: `pattern` (consistently works, >=3 evidence), `anti_pattern` (consistently fails), `agent_perf` (agent performance observation).

### CEO review criteria
The CEO does not formally review Archivist output with a PROCEED/REDIRECT/ABORT gate. However:
- Sacred Rule 7 requires archival after every verdict and at cycle end
- If the cycle-end blocking archive finds gaps (experiments without archive notes), the Archivist fills them
- CEO's only check is: did the Archivist fire? (verified via `agent.started agent=archivist` event in `.factory/events.jsonl`)

### Playbook rules (empirically derived)
- **[arch-00001]** Record at all checkpoints — archival compliance is non-negotiable.
- **[arch-00002]** If `$FACTORY_VAULT_PATH` is not set or is empty, write to `.factory/` within the project directory instead. Do NOT fall back to the user's personal Obsidian vault.
