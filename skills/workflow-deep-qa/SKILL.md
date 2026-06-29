---
name: workflow-deep-qa
description: "Deep QA mode — decomposed QA verification pipeline with 3 sequential specialists (health check, code review, adversarial QA). Each specialist is followed by a CEO gate for early termination. Posts verdict as GitHub PR review. Use when the user says 'deep qa', 'detailed qa', or wants granular QA with per-dimension gating."
disable-model-invocation: true
argument-hint: "<project_path> --pr <number>"
---

# Deep Qa Workflow

The user wants: **$ARGUMENTS**

**Output constraint:** Your ONLY GitHub output artifact is the `factory review` command in the final step. Do NOT run `gh pr comment`, `gh issue comment`, or post any other comments on the PR. All analysis stays in .factory/reviews/ files.

## Phase 1: Qa — Health Checker

```bash
factory agent qa --task "Run the health check ONLY — do NOT perform code review or adversarial testing. Execute 'factory eval {project_path}', parse the JSON output, extract the composite score and per-dimension breakdown. Compare against the baseline score (score_before). Calculate the delta, check threshold compliance. Write a structured report to .factory/reviews/health-check.md with a score table, composite score, delta, and threshold result. If eval fails completely (no valid score), report REVERT immediately.
Write output to: .factory/reviews/health-check.md" --project "$PROJECT_PATH" --timeout 1800
```

### CEO Review — Health

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/health-check.md`
3. Assess: Read health check results at .factory/reviews/health-check.md. If eval completely failed (no valid composite score), emit HALT — this triggers immediate REVERT, skipping code review and adversarial testing. If eval passed and composite score is valid, emit PROCEED. Do NOT RELOOP — health check is a deterministic measurement, no fix loop in deep-qa mode.
4. Write verdict to `.factory/reviews/ceo-verdict-health.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

## Phase 2: Qa — Code Reviewer

```bash
factory agent qa --task "Perform code review ONLY — do NOT run eval or adversarial testing. Get changed files via 'git diff --name-only <baseline>..HEAD', then read each file's diff individually via 'git diff <baseline>..HEAD -- <file>'. Do NOT run 'gh pr diff' (too large). Evaluate against the 7-category checklist: correctness, security, edge cases, missing tests, style, scope compliance, guardrail compliance. Check spec fidelity via 'gh issue view <issue_number>'. Check plan completion against .factory/strategy/current.md. If research mode: verify no fixed_surfaces modified. Write structured results to .factory/reviews/code-review.md.
Write output to: .factory/reviews/code-review.md" --project "$PROJECT_PATH" --timeout 1800
```

### CEO Review — Review

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/code-review.md`
3. Assess: Read code review results at .factory/reviews/code-review.md. Count critical issues in the Issues section (severity = [Critical]). If critical_count > 0, emit HALT — this triggers ISSUES_FOUND verdict, skipping adversarial testing. If critical_count == 0, emit PROCEED to adversarial testing. Do NOT RELOOP — no fix loop in deep-qa mode.
4. Write verdict to `.factory/reviews/ceo-verdict-review.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

## Phase 3: Qa — Adversarial Tester

```bash
factory agent qa --task "Perform adversarial QA ONLY — do NOT re-run eval, lint, or type checking. Switch to skeptical user identity. Determine project type from factory.md and README.md (CLI/TUI/API/Library/Research/UI). Derive a test plan from acceptance criteria via 'gh issue view <issue_number>' BEFORE executing. Run the smoke test from factory.md. Execute type-aware feature testing matching the detected project type. Verify all acceptance criteria. Check Builder's claimed blockers. Write structured results to .factory/reviews/adversarial-qa.md with project type, test plan, smoke test result, feature tests, edge cases, acceptance criteria verification, and adversarial verdict (PASS/FAIL). When in doubt, FAIL — burden of proof is on the Builder.
Write output to: .factory/reviews/adversarial-qa.md" --project "$PROJECT_PATH" --timeout 1800
```

### CEO Review — Adversarial

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/adversarial-qa.md`
3. Assess: Read adversarial QA results at .factory/reviews/adversarial-qa.md. Check the Adversarial Verdict line at the bottom. If verdict is FAIL, emit HALT — this triggers FAIL verdict. If verdict is PASS, emit PROCEED to verdict synthesis. Do NOT RELOOP — adversarial tester is a one-shot evaluation in deep-qa mode.
4. Write verdict to `.factory/reviews/ceo-verdict-adversarial.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

## Step: Join Verdict

```bash
cat $PROJECT_PATH/.factory/reviews/health-check.md $PROJECT_PATH/.factory/reviews/code-review.md $PROJECT_PATH/.factory/reviews/adversarial-qa.md > $PROJECT_PATH/.factory/reviews/qa-latest.md
```

### Gate — Precheck (Automated)

```bash
factory precheck $PROJECT_PATH --score-before 0 --score-after 0
```

- **PROCEED** → continue to `post_review`

If gate fails: the change violated a constraint or score regressed. Route to `post_review` for error handling.

## Step: Post Review

```bash
factory review --verdict $VERDICT --pr $PR_NUMBER --score-before $SCORE_BEFORE --score-after $SCORE_AFTER
```
