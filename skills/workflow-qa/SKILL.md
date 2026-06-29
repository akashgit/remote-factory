---
name: workflow-qa
description: "QA mode — run the QA verification pipeline against a PR. Spawns QA Agent (health check + code review + adversarial QA), CEO review gate, precheck, and posts verdict as GitHub PR review."
disable-model-invocation: true
argument-hint: "<project_path> --pr <number>"
---

# Qa Workflow

The user wants: **$ARGUMENTS**

**Output constraint:** Your ONLY GitHub output artifact is the `factory review` command in the final step. Do NOT run `gh pr comment`, `gh issue comment`, or post any other comments on the PR. All analysis stays in .factory/reviews/ files.

## Phase 1: Qa

```bash
factory agent qa --task "Run health check (factory eval + score delta), code review (correctness, architecture, edge cases, security), and adversarial QA (run/test the built feature). Write results to .factory/reviews/qa-latest.md
Write output to: .factory/reviews/qa-latest.md" --project "$PROJECT_PATH" --timeout 1800
```

### CEO Review — Qa

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/qa-latest.md`
3. Assess: Review QA results. PROCEED if all checks pass. HALT if issues found — no fix loop in QA mode.
4. Write verdict to `.factory/reviews/ceo-verdict-qa.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

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
