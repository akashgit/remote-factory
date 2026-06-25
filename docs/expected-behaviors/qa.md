# QA Agent — Expected Behavior Specification

## 1. Identity & Responsibility

The QA Agent is the Software Factory's single quality gate between the Builder's work and a keep/revert decision. It performs three sequential verification sections: Health Check (run eval and report scores), Code Review (7-category structured checklist against the PR diff), and Adversarial QA (real user testing of the feature). The QA Agent is strictly read-only — it observes, measures, tests, and reports, but never modifies source files.

**What the QA Agent IS:**
- A multi-section verification pipeline (Health Check + Code Review + Adversarial QA)
- A read-only observer that uses Bash, Read, Grep, and Glob tools only
- A structured reporter that produces CLEAN / ISSUES_FOUND / REVERT verdicts
- An adversarial tester that switches into "skeptical user" mode for Section 3

**What the QA Agent IS NOT:**
- NOT a fixer — it does not modify source files or fix bugs (that's the Builder's job)
- NOT a decision-maker — it reports findings, the CEO decides keep/revert
- NOT a researcher — it does not search the web or analyze improvement opportunities
- NOT the owner of the iteration loop — the CEO owns the Builder-QA iteration cycle
- NOT a re-runner of tests already covered — Section 3 does NOT re-run pytest/lint/mypy (Section 1 did that)

**Relationship to other agents:**
- **CEO** spawns the QA Agent after Builder completes, reviews QA output, and makes keep/revert decision. CEO may REDIRECT to Builder if QA finds issues (max 2-3 iterations depending on workflow).
- **Builder** produces the PR that QA verifies — QA reads the Builder's output and PR diff, but does not interact with the Builder directly
- **Evaluator** (in some workflows) runs eval separately — when QA runs the Health Check, it subsumes the Evaluator's role
- **Archivist** records experiment outcomes after CEO's verdict — QA does not interact with Archivist

---

## 2. Per-Workflow Behavior

### Workflow: Improve

**Phase:** The QA Agent is invoked implicitly through the Evaluator phase (Phase 5) and the CEO's review gate. In the Improve workflow, the QA role is split: the Evaluator runs `factory eval` (Health Check), and the CEO reads the results. The full 3-section QA (Health Check + Code Review + Adversarial QA) is triggered when the CEO dispatches the QA Agent explicitly for verification.

**Spawned by:** CEO, synchronously. When invoked as the full QA agent:
```bash
factory agent qa --task "Verify the Builder's work. Run all 3 verification sections:
1. Health Check — run factory eval $PROJECT_PATH, report composite score and delta vs baseline.
2. Code Review — read PR diff file-by-file, evaluate 7-category checklist.
3. Adversarial QA — run/test the project as a skeptical user.
Read: .factory/reviews/builder-latest.md
Write output to: .factory/reviews/qa-latest.md" --project "$PROJECT_PATH" --timeout 600
```

**Inputs received:**
- Project path and experiment context
- PR number and hypothesis
- Baseline score (`score_before`) for comparison
- QA iteration number (1-3) — the CEO owns the iteration loop
- `.factory/reviews/builder-latest.md` — Builder's output
- `factory.md` — factory config with scope, guards, eval command, smoke test
- GitHub issue with acceptance criteria

**Expected process (ordered steps):**

**Section 1: Health Check**
1. Run eval: `factory eval $PROJECT_PATH`
2. Parse JSON output: Extract composite score, per-dimension breakdown, pass/fail status
3. Compare against baseline: Calculate delta vs `score_before`
4. Report score direction: Improved, regressed, or unchanged — and by how much
5. Check threshold: Does `score_after` meet the configured threshold?
6. **GATE:** If eval fails completely (no valid score), report REVERT immediately. Do NOT proceed to Sections 2 or 3.

**Section 2: Code Review**
1. Get list of changed files: `git diff --name-only <baseline>..HEAD`
2. **CRITICAL: Do NOT run `gh pr diff`.** Read each changed file's diff individually: `git diff <baseline>..HEAD -- <file>`
3. **MANDATORY: Read every changed file's diff before writing any checklist result.** Do NOT skim and fill a template.
4. Evaluate against the 7-category checklist with specific `file:line` evidence for each category
5. Spec fidelity check: `gh issue view <issue_number>` — verify PR implements ALL acceptance criteria. Flag scope shrinkage.
6. Surface constraint checks (research mode only): Verify no `fixed_surfaces` files in `git diff --name-only`. Run `factory guard $PROJECT_PATH --baseline $BASELINE_SHA --check-surfaces`.
7. **GATE:** If any **critical** issues found, STOP. Do NOT proceed to adversarial testing. Report ISSUES_FOUND or REVERT immediately.

**Section 3: Adversarial QA (MANDATORY)**
1. **Switch identity** — become a skeptical user who does NOT trust the Builder
2. Determine project type (UI/Frontend, CLI one-off, CLI interactive, API/Server, Library, Research)
3. Derive test plan from acceptance criteria: `gh issue view <issue_number>` — write concrete test scenarios BEFORE executing
4. Run smoke test from `factory.md`: `grep -A2 "## Smoke Test" factory.md` — if fails, report FAIL immediately
5. Execute type-aware feature testing (CLI commands, tmux for TUI, curl for APIs, Playwright for UI)
6. Verify acceptance criteria — for each criterion, provide command + output, mark VERIFIED or NOT_VERIFIED
7. Check Builder's claimed blockers — test whether they are real

**Expected outputs/artifacts:**
- `.factory/reviews/qa-latest.md` — structured report with all 3 sections
- Final verdict: CLEAN | ISSUES_FOUND: N | REVERT

**Handoff:** CEO reads `.factory/reviews/qa-latest.md`. On CLEAN, CEO proceeds to finalize with `keep` verdict. On ISSUES_FOUND with non-fatal issues, CEO may proceed or REDIRECT to Builder. On REVERT, CEO finalizes with `revert` verdict. CEO may iterate Builder-QA up to 2-3 times.

---

### Workflow: Research

**Phase:** Same as Improve — QA Agent verifies the Builder's implementation of a research hypothesis.

**Spawned by:** CEO, synchronously (same invocation pattern as Improve).

**Inputs received:**
- Same as Improve, plus:
- Research mode constraints: `fixed_surfaces` and `mutable_surfaces` lists
- Hypothesis targeting a specific failure mode

**Expected process (ordered steps):**
- Same 3 sections as Improve, with these additions:
- **Section 2 additional step:** Surface constraint checks are MANDATORY (not optional):
  - Verify no `fixed_surfaces` files appear in `git diff --name-only`
  - Run `factory guard $PROJECT_PATH --baseline $BASELINE_SHA --check-surfaces`
  - Any `fixed_surface` violation is automatically **critical** → REVERT
- **Section 3 additional consideration:** Test whether the hypothesis actually addresses the targeted failure mode, not just whether it doesn't break things

**Expected outputs/artifacts:**
- `.factory/reviews/qa-latest.md` — same structure as Improve

**Handoff:** Same as Improve. After QA + finalize + archivist, the Research workflow runs the Plateau Gate to decide whether to RELOOP for another experiment cycle.

---

### Workflow: Refine

**Phase:** Phase 3 — Reviewer (QA role, invoked as `reviewer` agent)

**Spawned by:** CEO, synchronously via:
```bash
factory agent reviewer --task "Verify the refinement. Run all 3 verification sections: 1. Health Check — run factory eval. Report composite score and delta. 2. Code Review — read PR diff, evaluate 7-category checklist. Run factory guard with --check-scope. 3. Adversarial QA — run/test the project, verify the refinement works.
Read: .factory/reviews/builder-latest.md
Write output to: .factory/reviews/qa-latest.md" --project "$PROJECT_PATH" --timeout 600
```

**Inputs received:**
- `.factory/reviews/builder-latest.md` — Builder's output for the refinement
- `.factory/reviews/refiner-latest.md` — Refiner's classification (tier, files, scope)
- GitHub issue labeled "refinement"
- `CLAUDE.md`, `factory.md` — project config

**Expected process (ordered steps):**
- Same 3 sections as Improve, with these modifications:
- **Section 2 additional check:** Run `factory guard $PROJECT_PATH --check-scope` (explicit scope verification for refinements)
- **Section 3:** Verify the specific refinement works — test the narrow change the user requested

**Expected outputs/artifacts:**
- `.factory/reviews/qa-latest.md` — same structured report

**Handoff:** CEO reads QA output at the QA Gate. On PROCEED, continues to precheck. On REDIRECT, CEO re-invokes the **Builder** (not the QA Agent) with corrections. The Refine workflow allows up to 3 Builder re-invocations via QA RELOOP — this is more iterations than the standard 2 REDIRECTs in other workflows.

**Refine-specific notes:**
- The QA Agent is invoked as `reviewer` role (not `qa`) in the Refine workflow
- Output goes to `.factory/reviews/qa-latest.md` (same path regardless of role name)
- CEO verdict is written to `.factory/reviews/ceo-verdict-qa.md`

---

## 3. Invariants (MUST always hold)

1. **Read-only:** "You MUST NOT modify any source files. Tools: Bash, Read, Grep, Glob." (qa.md:330) The QA Agent never writes to source code, `eval/score.py`, or `.factory/` contents.

2. **Sequential section execution with gates:** Sections execute in order (1 → 2 → 3). Section 1 failure (no valid score) → immediate REVERT, skip Sections 2 and 3. Section 2 critical issue → immediate ISSUES_FOUND/REVERT, skip Section 3. Section 3 is MANDATORY if Sections 1 and 2 pass.

3. **Adversarial testing is mandatory:** "Section 3 MUST include real execution of the project — running CLI commands, starting servers, launching tmux sessions. Reading files and checking if sections exist is NOT adversarial testing." (qa.md:331) Every adversarial test needs evidence: command + output. A test without evidence is NOT_VERIFIED.

4. **No pytest/lint/mypy in Section 3:** "Do NOT re-run pytest, lint, or type checking. The health check already did that. Your job is to test the feature as a real user would." (qa.md:122-123) Section 3 tests user-facing behavior, not code hygiene.

5. **Diff reading protocol:** "CRITICAL: Do NOT run `gh pr diff`. The full PR diff is too large and will crash the output parser." (qa.md:59) Changed files are read individually: `git diff --name-only <baseline>..HEAD` then `git diff <baseline>..HEAD -- <file>` per file.

6. **When in doubt, FAIL:** "The burden of proof is on the Builder, not on you." (qa.md:299) Adversarial verdict defaults to FAIL on uncertainty.

7. **Clean up resources:** "Kill any servers, tmux sessions, or background processes you start." (qa.md:333) The QA Agent must not leave orphaned processes.

8. **Stateless iteration:** "The CEO owns the Builder → QA iteration loop." (qa.md:334) The QA Agent does not decide whether to re-invoke the Builder or how many iterations have occurred.

---

## 4. Constraints & Forbidden Actions

- **MUST NOT** modify any source files — strictly read-only
- **MUST NOT** modify `eval/score.py` or any file in `.factory/`
- **MUST NOT** run `gh pr diff` — crashes the output parser; use per-file `git diff` instead
- **MUST NOT** re-run pytest, lint, or mypy in Section 3 — Section 1 already covered these
- **MUST NOT** make keep/revert decisions — report findings only, CEO decides
- **MUST NOT** skip Section 3 (Adversarial QA) — it is mandatory when Sections 1 and 2 pass
- **MUST NOT** write adversarial test results without real execution evidence (command + output)
- **MUST NOT** fill in the 7-category checklist without reading every changed file's diff first
- **MUST NOT** report a high eval score as proof of correctness for integration code — "Eval measures code hygiene (tests exist, lint passes, types check), NOT whether the code actually works against external systems." (playbook qa-00003)
- **MUST NOT** count mock-only test suites as evidence of integration correctness — "If 0% of tests hit real external services, flag that integration correctness is untested." (playbook qa-00004)
- **MUST NOT** leave servers, tmux sessions, or background processes running after testing

---

## 5. Failure Modes & Diagnostic Signals

| Failure mode | Trace signal | Example issue |
|---|---|---|
| **Skipped adversarial testing** — QA reports CLEAN without executing Section 3, or Section 3 contains no real commands | No `Bash` tool calls in Section 3 portion of trace. Output contains "PASS" verdicts without command+output evidence. Missing tmux/curl/CLI invocations. | QA reads source files and says "the function looks correct" without running it |
| **`gh pr diff` crash** — QA runs `gh pr diff` on a large PR, output parser crashes or truncates | `Bash` tool call with `gh pr diff` command. Tool result is truncated or error. Subsequent Section 2 checklist is incomplete or missing evidence. | QA runs `gh pr diff` on a 2000-line PR, output truncated at 500 lines, review misses critical issues in later files |
| **False CLEAN verdict** — QA reports CLEAN despite missing acceptance criteria or untested edge cases | Section 3 shows fewer test scenarios than issue acceptance criteria. Some criteria marked VERIFIED without command evidence. Coverage count `N/M` where N < M but verdict is CLEAN. | QA marks 3/5 acceptance criteria as VERIFIED and 2 as "not applicable" without testing, reports CLEAN |
| **Mock-only integration approval** — QA approves code that only has mock tests, no real integration testing | Section 2 "Missing tests" shows PASS. No commands testing real external services. `.env` with credentials exists but no test uses them. Eval score is high but integration untested. | QA approves a Playwright scraper where all tests mock the browser — code passes lint/type/tests but has never run against the real site |
| **Orphaned processes** — QA starts servers or tmux sessions for testing but doesn't clean up | `Bash` tool calls with `&` or `tmux new-session` without corresponding `kill`/`tmux kill-session`. Background process IDs not tracked. | QA starts `npm run dev &` for frontend testing, tests fail, QA exits without `kill $PID` |
| **Plan coverage gap** — QA doesn't verify that all planned items from `strategy/current.md` were implemented | No `Read` tool call for `strategy/current.md` in QA trace. Section 2 "Scope compliance" PASS without plan-to-diff comparison. Builder deferred items not flagged. | Builder implements 8/10 planned items, QA reviews only the diff without checking the plan, reports CLEAN (#771) |

---

## 6. Interaction Protocol

### Results communication
- QA Agent stdout is captured to `.factory/reviews/qa-latest.md` by the `factory agent` runner
- The structured verdict at the end of the report is what the CEO uses for decision-making

### Output file format

**`.factory/reviews/qa-latest.md`** — structured markdown with these sections:

```markdown
## Health Check

| Dimension | Score | Weight | Status |
|-----------|-------|--------|--------|
| tests     | 1.00  | 0.50   | PASS   |
| ...       | ...   | ...    | ...    |

**Composite:** <score> (delta: <+/-change> vs baseline <score_before>)
**Threshold:** <threshold> — <PASS|FAIL>

## Code Review

### Checklist
- Correctness: PASS | FAIL — <evidence with file:line>
- Security: PASS | FAIL — <evidence>
- Edge cases: PASS | FAIL — <evidence>
- Missing tests: PASS | FAIL — <evidence>
- Style: PASS | FAIL — <evidence>
- Scope: PASS | FAIL — <evidence>
- Guardrails: PASS | FAIL — <evidence>

### Spec Fidelity
- Acceptance criteria met: N/M
- Scope shrinkage: <none | list of missing items>

### Issues
1. [<severity>] [<category>] <file>:<line> — <description>

## Adversarial QA

### Project Type
<type> — <how detected>

### Test Plan
<written before executing>

### Smoke Test
- **Command:** `<cmd>`
- **Result:** PASS | FAIL | NOT_CONFIGURED
- **Output:** <snippet>

### Feature Tests
1. **Scenario:** <desc>
   - **Command:** `<cmd>`
   - **Expected:** <what should happen>
   - **Actual:** <what happened>
   - **Result:** PASS | FAIL

### Edge Cases
1. <test> — PASS | FAIL (<detail>)

### Acceptance Criteria
- [ ] <criterion> — VERIFIED | NOT_VERIFIED (<evidence>)

---
**Adversarial Verdict:** PASS | FAIL

---

**Verdict:** CLEAN | ISSUES_FOUND: <N> | REVERT

### Summary
- **Health:** <composite_score> (delta: <change>)
- **Code Review:** <N> issues (<critical_count> critical, <important_count> important, <minor_count> minor)
- **Adversarial QA:** <pass_count>/<total_count> checks passed
- **E2E:** PASS | FAIL | SKIPPED

### Issue List (if ISSUES_FOUND)
1. [<severity>] [<category>] <file>:<line> — <description>
```

### Issue severity levels
- **Critical** — blocks merge: bugs causing runtime failure, security vulnerabilities, data corruption, fixed surface violation
- **Important** — should fix: edge cases not handled, missing error handling, logic gaps
- **Minor** — nice to fix: style, naming, minor duplication

### Verdict decision rules
- **CLEAN** — Health check passes, zero code review issues, adversarial verdict is PASS
- **ISSUES_FOUND: N** — Issues found but none fatal. N = total count across all sections
- **REVERT** — Score regression below threshold, critical code review issues, fixed surface violation, or adversarial verdict is FAIL on critical feature

### CEO review criteria
The CEO reads the QA output and decides:
1. Did all 3 verification sections execute?
2. Are there issues that need Builder fixes?
3. Is the adversarial verdict PASS or FAIL?
4. Does the composite score meet the threshold?

### Playbook rules (empirically derived)
- **[qa-00001]** When reviewing browser automation code, explicitly flag that selectors cannot be verified without running against the real site. Add a review comment: "UNVERIFIED: These selectors need manual E2E testing."
- **[qa-00002]** When the project has a `.env` with credentials, check whether any tests actually use those credentials against real external services. If all tests use mocks, flag that integration correctness is UNTESTED.
- **[qa-00003]** Don't report a high eval score as proof of correctness for integration code.
- **[qa-00004]** Don't count mock-only test suites as evidence of integration correctness. If 0% of tests hit real external services, flag that integration correctness is untested.
