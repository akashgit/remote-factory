---
name: workflow-meta
description: "Meta mode — cross-project insights, playbook evolution, and test pruning. Use when the user says 'meta', 'self-improve', 'evolve playbooks', or wants to improve the factory's own agents."
disable-model-invocation: true
argument-hint: "<project_path>"
---

# Meta Workflow

The user wants: **$ARGUMENTS**

## Step: Insights

```bash
factory insights $PROJECT_PATH
```

## Phase 1: Researcher

```bash
factory agent researcher --task "Read cross-project insights at .factory/strategy/insights.md and current playbooks. Identify recurring patterns, anti-patterns, and improvement opportunities. Compare agent performance across projects. Write findings to .factory/strategy/research-local.md.
Read: .factory/strategy/insights.md
Write output to: .factory/strategy/research-local.md" --project "$PROJECT_PATH" --timeout 600
```

### CEO Review — Research

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/strategy/research-local.md`
3. Assess: Are cross-project patterns well-supported by data? Are proposed improvements actionable? Any blind spots?
4. Write verdict to `.factory/reviews/ceo-verdict-research.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `researcher` (max 3 iterations)*

## Phase 2: Strategist

```bash
factory agent strategist --task "Propose specific playbook edits based on cross-project research. For each agent role, propose DO/DON'T bullet additions or removals with supporting evidence from experiment data. Write diffs to .factory/strategy/playbook-diffs.md.
Read: .factory/strategy/research-local.md
Write output to: .factory/strategy/playbook-diffs.md" --project "$PROJECT_PATH" --timeout 600
```

### Steering Point — User (User Approval)

Present findings to the user. Wait for approval or feedback.
- **Approve** → proceed to next step
- **Feedback** → re-run the previous step with corrections

*On RELOOP: return to `strategist` (max 3 iterations)*

## Step: Apply Playbooks

```bash
factory ace $PROJECT_PATH
```

## Phase 3: Archivist

```bash
factory agent archivist --task "Archive playbook evolution results.
Read: .factory/archive/playbooks-applied.md
Write output to: .factory/archive/meta.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*

## Step: Test Collect

```bash
pytest --co -q 2>/dev/null || true
```

## Phase 4: Test Researcher

```bash
factory agent researcher --task "Analyze test inventory for redundant, dead, or flaky tests. Identify tests that overlap, test nothing meaningful, or are consistently flaky. Write findings to .factory/strategy/test-analysis.md with specific test names and reasons for removal.
Read: .factory/strategy/test-inventory.md
Write output to: .factory/strategy/test-analysis.md" --project "$PROJECT_PATH" --timeout 600
```

### Steering Point — Test Prune (User Approval)

Present findings to the user. Wait for approval or feedback.
- **Approve** → proceed to next step
- **Feedback** → re-run the previous step with corrections

*On RELOOP: return to `test_researcher` (max 3 iterations)*

## Phase 5: Test Builder

```bash
factory agent builder --task "Delete the approved redundant tests. Verify remaining suite still passes.
Read: .factory/strategy/test-analysis.md
Write output to: .factory/reviews/test-pruning-latest.md" --project "$PROJECT_PATH" --timeout 1800
```

## Phase 6: Qa Verify

```bash
factory agent qa --task "Verify the test suite still passes after pruning. Run health check and confirm no regressions. Write results to .factory/reviews/qa-verify-latest.md
Read: .factory/reviews/test-pruning-latest.md
Write output to: .factory/reviews/qa-verify-latest.md" --project "$PROJECT_PATH" --timeout 1800
```

### CEO Review — Qa Verify

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/qa-verify-latest.md`
3. Assess: Review QA verification of test pruning. PROCEED if tests still pass. RELOOP to test_builder (max 3 iterations) if regressions found.
4. Write verdict to `.factory/reviews/ceo-verdict-qa-verify.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `test_builder` (max 3 iterations)*
