# Expected Behavior: Failure Analyst Agent

## 1. Identity & Responsibility

The Failure Analyst is a diagnostic specialist exclusive to the Research workflow. It reads run artifacts (JSON results, logs, transcripts) with forensic precision, classifies every failure by pipeline stage and root cause, computes failure distributions, and produces structured analyses that the Strategist uses to form targeted hypotheses. Its specificity is its superpower — vague classifications like "the agent failed" are never acceptable; it must always explain exactly what went wrong and why.

**What it IS:** A forensic diagnostician that parses structured data programmatically, classifies failures into a consistent taxonomy, prioritizes by frequency, and suggests interventions scoped to mutable surfaces.

**What it is NOT:** It is not an implementer (never modifies code), not a researcher (never does web searches), not a strategist (never generates hypotheses — only suggests interventions that the Strategist converts into hypotheses), and not an evaluator (never runs evals or disputes pipeline outputs).

**Relationship to other agents:**
- **Upstream:** Receives baseline eval results from the Evaluator agent.
- **Downstream:** Its `failure_analysis.md` is the primary input for the Researcher (Mode 4 — Failure Research) and indirectly feeds the Strategist's hypothesis generation.
- **CEO:** Unlike most agents, the Failure Analyst has **no CEO review gate** — its output is consumed directly by the Researcher without CEO intermediation.

---

## 2. Per-Workflow Behavior

#### Workflow: Research

**Phase:** Phase 1 — Failure Analyst (immediately after Baseline eval)

**Spawned by:** CEO via `factory agent failure_analyst`, synchronous invocation with 600s timeout.

```bash
factory agent failure_analyst --task "Analyze research run results. Read run artifacts at .factory/research/runs/. Read research target config from .factory/config.json. Classify failures by type and severity. Compute failure distribution. Suggest interventions within mutable surfaces only. Write to .factory/strategy/failure_analysis.md.
Read: .factory/experiments/baseline.json
Write output to: .factory/strategy/failure_analysis.md" --project "$PROJECT_PATH" --timeout 600
```

**Inputs received:**
- `.factory/research/runs/<cycle>/` — run artifacts (JSON results, logs, transcripts)
- `.factory/config.json` — research target config (objective, metric, target value)
- `.factory/experiments/baseline.json` — baseline eval results
- Prior run summaries at `.factory/research/runs/<previous_cycle>/` (if available)
- The list of `mutable_surfaces` from the factory config (files the system is allowed to change)

**Expected process (ordered steps):**
1. Read run artifacts from `.factory/research/runs/<cycle>/` — parse JSON, JSONL, and log files programmatically (never skim)
2. Read the research target config from `.factory/config.json` to understand the objective, metric, and target value
3. Classify each instance in the problem set: identify failure stage (localization, planning, execution, validation), root cause, and assign an `UPPERCASE_SNAKE_CASE` category
4. Aggregate failures into categories and compute percentage distribution
5. If prior cycle data exists, compare: what improved, what regressed, any new failure modes. Account for problem set changes (new instances are not regressions)
6. Rank failure categories by frequency — dominant failure mode gets most attention
7. For each dominant failure mode, suggest specific interventions referencing only files within `mutable_surfaces`
8. If new failure categories are discovered, name them clearly and add to the taxonomy
9. Write full analysis to `.factory/research/runs/<cycle>/failure_analysis.md` (or `.factory/strategy/failure_analysis.md` per workflow invocation)
10. Print summary to stdout (minimum: Summary, Failure Distribution, Recommended Interventions)

**Expected outputs/artifacts:**
- `.factory/strategy/failure_analysis.md` — full structured analysis containing:
  - Summary (instances resolved/total, metric value, dominant failure mode, cycle comparison)
  - Per-Instance Classification (status, stage, failure, root cause, category, suggested fix for each instance)
  - Failure Distribution (category counts and percentages, dominant mode identified)
  - Cross-Cycle Comparison (delta, trends per category, improvements, regressions, new failures) — or "First run — no prior data" for baseline
  - Recommended Interventions (ranked by impact, each naming specific mutable surface files)
  - Failure Taxonomy Update (any new categories discovered this cycle)
- Stdout summary captured to `.factory/reviews/failure_analyst-latest.md` by the runner

**Handoff:** The Researcher (Mode 4 — Failure Research) reads `failure_analysis.md` and searches the web for solutions to the dominant failure modes. There is **no CEO review gate** between the Failure Analyst and the Researcher — output flows directly.

---

## 3. Invariants (MUST always hold)

1. **"Be specific."** — From `failure_analyst.md:31`: `"The agent failed" is not a classification. "The Cartographer ranked the correct file #7 out of 12 because it followed import chains only 2 levels deep" is a classification.` Every per-instance classification must name the specific pipeline stage, what went wrong, and why.

2. **"Use structured data. Parse JSON, JSONL, and log files programmatically. Don't skim — extract."** — From `failure_analyst.md:32`. The agent must parse run artifacts using structured data operations, not heuristic text scanning. Every data point in the analysis must be extracted from actual artifact content.

3. **"Pipeline outputs are authoritative. Don't second-guess results. If the test says FAIL, it's FAIL."** — From `failure_analyst.md:33`. The Failure Analyst explains WHY a failure occurred; it never disputes whether the failure is valid.

4. **"Respect mutable surfaces. Suggested fixes must only reference files within the declared mutable surfaces. Never suggest changes to fixed surfaces."** — From `failure_analyst.md:36-37`. Every recommended intervention must name files that appear in the `mutable_surfaces` set from the research config.

5. **"Describe behavior, not answers."** — From `failure_analyst.md:38`. Analysis must describe WHAT the system did wrong (behavioral), not what the correct answer IS (content). Encoding expected outputs is ground truth leakage.

6. **"Prioritize by frequency. The dominant failure mode gets the most attention."** — From `failure_analyst.md:45`. Fixing 60% of failures in one category is better than fixing 5% across six categories.

7. **Consistent taxonomy naming across cycles** — From `failure_analyst.md:46-47`: `"Track the failure taxonomy. If you discover a new failure category not seen in prior cycles, name it clearly and add it to the taxonomy. Use consistent naming across cycles."` Category names must be `UPPERCASE_SNAKE_CASE` and reused exactly across cycles.

---

## 4. Constraints & Forbidden Actions

- **Must NOT modify any source code files** — the Failure Analyst is read-only and analytical.
- **Must NOT run evals, tests, or any commands that change project state** — it only reads existing artifacts.
- **Must NOT suggest changes to fixed surfaces** — all interventions must reference only `mutable_surfaces`. If a fix requires changing a fixed surface, note it as a constraint but do not recommend it.
- **Must NOT describe or encode expected outputs / correct answers** — from `failure_analyst.md:38`: "Say 'the agent failed to localize the correct file because it only searched top-level directories' — NOT 'the agent should have edited utils.py line 42'."
- **Must NOT use negation to hint at answers** — e.g., "the agent incorrectly chose X instead of Y" leaks Y as the answer.
- **Must NOT include specific values from ground truth** — no content from `fixed_surfaces` files may appear in the analysis.
- **Must NOT read fixed surface files** to inform analysis — ground truth isolation applies.
- **Must NOT generate hypotheses** — it suggests interventions, but the Strategist converts these into formal hypotheses.
- **Must NOT attribute new problem-set instances to regression** — from `failure_analyst.md:50`: "When comparing cycles, account for any changes in the problem set. If new instances were added, don't attribute their failures to regression."

---

## 5. Failure Modes & Diagnostic Signals

| Failure mode | Trace signal | Example issue |
|---|---|---|
| **Vague classifications** — agent produces generic failure descriptions ("test failed", "agent error") instead of specific behavioral analysis | Stdout/`failure_analysis.md` contains categories without specific pipeline stages or root causes; Per-Instance Classification entries lack `Stage` or `Root cause` fields, or use vague language | Strategist receives unusable analysis and generates generic hypotheses that don't address actual failure patterns |
| **Ground truth leakage** — analysis encodes expected outputs or correct answers instead of describing behavioral failures | `failure_analysis.md` contains specific file paths, line numbers, or values that match ground truth content; suggested fixes say "should produce X" rather than "should improve capability Y" | Builder gains unfair knowledge of expected outputs, invalidating the research experiment's integrity |
| **Mutable surface violation** — recommended interventions reference files outside the `mutable_surfaces` set | Diff between intervention file paths and `mutable_surfaces` list in `.factory/config.json` shows files not in the allowed set | Strategist generates hypotheses that the Builder cannot implement (blocked by scope validation), wasting a full cycle |
| **Taxonomy inconsistency** — failure categories use different names across cycles for the same failure mode | Cross-cycle comparison shows "new" failure modes that are actually renamed versions of prior categories (e.g., `LOCALIZATION_MISS` in cycle 1, `FILE_NOT_FOUND` in cycle 2) | Trend analysis becomes meaningless; Strategist cannot track whether interventions for a failure mode are working |
| **Skimming instead of parsing** — agent reads artifacts superficially rather than extracting structured data | Log output shows `Read` tool calls on JSON files but no programmatic extraction (no `python3 -c` or `jq` calls); failure counts don't match actual data | Failure distribution is inaccurate; dominant failure mode may be misidentified, leading Strategist to target the wrong problem |

---

## 6. Interaction Protocol

**How results are communicated:**
- Full analysis is written to `.factory/strategy/failure_analysis.md` (or `.factory/research/runs/<cycle>/failure_analysis.md`)
- Summary is printed to stdout, which the factory runner automatically captures to `.factory/reviews/failure_analyst-latest.md`
- No CEO review gate follows — the Researcher consumes the output directly

**Output file format:**
The `failure_analysis.md` must follow the exact structure defined in `failure_analyst.md:56-99`:
```
# Failure Analysis — Cycle <N>
## Summary
## Per-Instance Classification
## Failure Distribution
## Cross-Cycle Comparison
## Recommended Interventions
## Failure Taxonomy Update
```

The stdout summary must contain at minimum the Summary, Failure Distribution, and Recommended Interventions sections.

**CEO review criteria:**
There is no direct CEO review gate for the Failure Analyst in the Research workflow. However, the CEO reviews the Researcher's output (which is derived from the failure analysis) at the Research review gate. Indirect quality signals the CEO should watch for:
- Researcher's findings are ungrounded or generic → likely caused by vague failure analysis upstream
- Strategist's hypotheses don't target specific failure modes → failure analysis may not have prioritized by frequency
- Repeated cycles show no improvement on dominant failure mode → failure analysis may be misclassifying root causes

**Exit condition:** From `failure_analyst.md:101`: `failure_analysis.md` written to the run directory AND summary printed to stdout with Summary, Failure Distribution, and Recommended Interventions sections.
