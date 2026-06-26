---
name: workflow-refine
description: "Refine mode — lightweight pipeline for user-directed refinements. Use when the user says 'refine X', passes --refine, or wants a targeted change without the overhead of research and multi-hypothesis cycles. Classifies the request, implements with Builder, verifies with QA, and archives."
disable-model-invocation: true
argument-hint: "<project_path> --refine "<request>""
---

# Refine Workflow

The user wants: **$ARGUMENTS**

## Phase 1: Refiner


```bash
factory agent refiner --task "Classify and scope a refinement request. Read CLAUDE.md and factory.md. Analyze the codebase to identify which files need to change, estimate scope, and classify the request as Tier 1, 2, or 3. Produce the structured classification output with a Builder task description.
Read: CLAUDE.md, factory.md
Write output to: .factory/reviews/refiner-latest.md" --project "$PROJECT_PATH" --timeout 600
```

### CEO Review — Refiner

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/refiner-latest.md`
3. Assess: Review Refiner classification. Is the tier classification reasonable? Are the identified files correct? Is the Builder task description specific enough? REDIRECT if the classification is wrong.
4. Write verdict to `.factory/reviews/ceo-verdict-refiner.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `refiner` (max 3 iterations)*

### Gate — Tier (Automated)

```bash
python3 -c "from pathlib import Path; text = Path('$PROJECT_PATH/.factory/reviews/refiner-latest.md').read_text(); print('HALT' if 'Tier 3' in text or 'tier 3' in text or 'TIER 3' in text else 'PROCEED')"
```

## Step: Begin


```bash
factory begin $PROJECT_PATH --hypothesis "Refine: user refinement request"
```

## Step: Create Issue


```bash
gh issue create --title "Refine: refinement request" --label "refinement" --body "Factory refinement experiment."
```

## Phase 2: Builder


```bash
factory agent builder --task "Implement the refinement described in the Refiner's output. Read the GitHub issue. Read CLAUDE.md and factory.md. Implement exactly what the issue describes. Run tests. Commit and open a draft PR.
Read: .factory/reviews/refiner-latest.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 600
```

## Phase 3: Reviewer — Qa


```bash
factory agent qa --task "Verify the refinement. Run all 3 verification sections: 1. Health Check — run factory eval. Report composite score and delta. 2. Code Review — read PR diff, evaluate 7-category checklist. Run factory guard with --check-scope. 3. Adversarial QA — run/test the project, verify the refinement works.
Read: .factory/reviews/builder-latest.md
Write output to: .factory/reviews/qa-latest.md" --project "$PROJECT_PATH" --timeout 600
```

### CEO Review — Qa

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/qa-latest.md`
3. Assess: Read QA output. Did all verification sections pass? Are there issues that need Builder fixes? REDIRECT to Builder if issues found (max 3 iterations).
4. Write verdict to `.factory/reviews/ceo-verdict-qa.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder` (max 3 iterations)*

### Gate — Precheck (Automated)

```bash
factory precheck $PROJECT_PATH --score-before 0 --score-after 0
```

## Step: Finalize


```bash
factory finalize $PROJECT_PATH --id 1 --verdict keep --hypothesis 'Refine: request'
```

## Phase 4: Archivist


```bash
factory agent archivist --task "Archive refinement experiment results and learnings.
Read: .factory/experiments/verdict.json
Write output to: .factory/archive/refinement.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*
