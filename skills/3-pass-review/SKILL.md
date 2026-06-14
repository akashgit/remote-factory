---
name: 3-pass-review
description: Run a 3-round adversarial review on a GitHub PR with 3 independent reviewers. Each round spawns all reviewers in parallel. In rounds 2 and 3, each reviewer gets the other reviewers' prior findings cross-pollinated into their context, so they can challenge, validate, and go deeper. Use when the user asks for a "3-pass review", "deep review", "iterative review", or wants a thorough multi-pass review of a PR.
disable-model-invocation: true
argument-hint: "<PR number or URL>"
---

# 3-Pass Adversarial PR Review

Three independent reviewers, three rounds. In each subsequent round, a reviewer sees its own full prior trajectory but only **summaries** of the other reviewers' findings — this preserves each reviewer's depth while cross-pollinating awareness without anchoring or context bloat.

```
Round 1: A1, B1, C1 review independently (blind)
              ↓ parent writes a summary of each reviewer's findings
Round 2: A2 sees A1 (full) + summaries of B1, C1
         B2 sees B1 (full) + summaries of A1, C1
         C2 sees C1 (full) + summaries of A1, B1
              ↓ parent writes a summary of each reviewer's round 2 findings
Round 3: A3 sees A1+A2 (full) + summaries of B1, B2, C1, C2
         B3 sees B1+B2 (full) + summaries of A1, A2, C1, C2
         C3 sees C1+C2 (full) + summaries of A1, A2, B1, B2
              ↓ collect final findings
Final:   Parent consolidates all 3 rounds into verdict
```

## When to Use

- User asks for "3-pass review", "deep review", "iterative review", "thorough review"
- Any PR where a single pass isn't enough
- When you want independent perspectives that converge through cross-challenge

## How to Run

### Step 1: Gather PR Context

Parse the PR number from `$ARGUMENTS`. If a URL was given, extract the number from it.

Fetch the PR metadata and full diff:
```bash
gh pr view <number> --json title,body,additions,deletions,changedFiles
gh pr diff <number>
```

### Step 2: Detect Language and Select Reviewers

Determine the dominant language from file extensions:
```bash
gh pr diff <number> --name-only
```

Pick 3 reviewers based on the diff content:
- **Reviewer A** (code quality): bugs, logic errors, DRY, edge cases, error handling
- **Reviewer B** (language-specific): selected by dominant language
  - Python → type hints, PEP 8, async correctness, Django/FastAPI idioms
  - TypeScript/JS → type safety, async patterns, Node/web security
  - Rust → ownership, lifetimes, error handling, unsafe usage
  - Go → goroutine safety, error handling, interface design
  - Java → Spring Boot, JPA, concurrency, layered architecture
  - Markdown/docs/prompts → clarity, completeness, consistency
  - Mixed → pick the dominant language by file count
- **Reviewer C** (security): injection, auth, path traversal, secrets, unsafe operations

### Step 3: Round 1 — Independent Blind Review

Spawn all 3 reviewers in a **single message** (so they run in parallel). Each reviewer MUST:
- Have a unique `name` (e.g., `reviewer-a`, `reviewer-b`, `reviewer-c`)
- Set `run_in_background: true`
- Receive the full PR metadata and diff
- Be told: **"This is round 1 of 3. You are one of 3 independent reviewers. You cannot see the others' findings. Review independently."**

Each reviewer's prompt should include:

```
You are reviewing PR #<number>.

**Title:** <title>
**Description:** <description>
**Changed files:** <count> (+<additions>, -<deletions>)

**Full diff:**
<paste complete diff here>

## Instructions

This is ROUND 1 of 3 of an adversarial review. You are one of 3 independent reviewers. You CANNOT see the others' findings yet.

Review the PR through your lens:
<lens-specific instructions>

Provide findings as a numbered list with severity ratings: [CRITICAL], [HIGH], [MEDIUM], [LOW], [INFO].
Include file:line references where applicable.

Do NOT modify any files. This is a read-only review.
End with a one-line verdict: APPROVE, REQUEST_CHANGES, or COMMENT.
```

Wait for all 3 to complete. Collect and label each reviewer's full output.

**Summarization step (MANDATORY after Round 1):** For each reviewer's full output, write a concise summary (3-8 bullet points) capturing: the verdict, each finding with its severity and file:line reference, and the reviewer's key concern. These summaries are what get cross-pollinated to the OTHER reviewers. The full outputs are only sent back to the reviewer who wrote them.

### Step 4: Round 2 — Cross-Pollinated Deep Review

Use `SendMessage` to **continue** each reviewer (preserving their full context). Each reviewer receives:
- Its OWN round 1 findings are already in its context (via SendMessage continuation)
- The OTHER two reviewers' round 1 findings as **summaries only**

Send to reviewer-a:
```
## Round 2 of 3 — Cross-Pollinated Deep Review

Here are condensed summaries of the other two reviewers' round 1 findings. You have NOT seen these before.

### Reviewer B (language specialist) — Round 1 summary:
<paste reviewer B's round 1 SUMMARY>

### Reviewer C (security) — Round 1 summary:
<paste reviewer C's round 1 SUMMARY>

Now review the PR again with this additional context:
1. Do you agree or disagree with their findings? Challenge anything you think is wrong.
2. Did their findings make you notice something YOU missed in round 1?
3. Go deeper on your area of expertise — what did you gloss over the first time?

Provide NEW findings only (don't repeat your round 1). Use severity ratings.
End with an updated verdict.
```

Send equivalent messages to reviewer-b (with A+C summaries) and reviewer-c (with A+B summaries).

Wait for all 3 to complete. Collect round 2 findings.

**Summarization step (MANDATORY after Round 2):** Same as after Round 1 — write a concise summary for each reviewer's round 2 output.

### Step 5: Round 3 — Adversarial Stress Test

Use `SendMessage` again to continue each reviewer. Each reviewer's own full trajectory is already in its context. Each receives **summaries only** from all other reviewers' rounds 1 and 2.

Send to each reviewer:
```
## Round 3 of 3 — Adversarial Stress Test

Here are summaries of ALL findings from the other reviewers across rounds 1 and 2:

### Reviewer [X] — Round 1 summary:
<summary>
### Reviewer [X] — Round 2 summary:
<summary>

### Reviewer [Y] — Round 1 summary:
<summary>
### Reviewer [Y] — Round 2 summary:
<summary>

Final round. Try to BREAK the PR:
1. What assumptions does this code make that might not hold?
2. What are the boundary conditions nobody tested?
3. Race conditions, concurrency issues, TOCTOU?
4. What if dependencies are unavailable or behave differently?
5. Can a malicious actor exploit any of these changes?
6. Look at every finding marked [LOW] or [INFO] across all reviewers — should any be escalated?

Provide NEW findings only. Use severity ratings.
End with your FINAL verdict: APPROVE, REQUEST_CHANGES, or COMMENT.
```

Wait for all 3 to complete.

### Step 6: Consolidate and Report

Present a unified report to the user:

```
## 3-Pass Adversarial Review: PR #<number>

### Reviewer Verdicts
| Reviewer | Round 1 | Round 2 | Round 3 (Final) |
|----------|---------|---------|-----------------|
| A (code quality) | ... | ... | ... |
| B (language) | ... | ... | ... |
| C (security) | ... | ... | ... |

### All Findings by Severity
**CRITICAL:**
- ...

**HIGH:**
- ...

**MEDIUM:**
- ...

**LOW / INFO:**
- ...

### Consensus
<what all 3 reviewers agreed on>

### Disagreements
<where reviewers challenged each other's findings>

### Overall Recommendation
<APPROVE / REQUEST_CHANGES / COMMENT based on majority + severity>

### Summary
<one paragraph: most important thing the author should address>
```

Offer to post the review as a comment on the PR.

## Notes

- The cross-pollination is what makes this powerful. Round 1 is independent (no anchoring). Round 2 forces each reviewer to engage with perspectives they didn't consider. Round 3 is adversarial — trying to break what survived two rounds.
- Use `SendMessage` with the reviewer's `name` to continue them — this preserves their full context. Do NOT spawn new agents for rounds 2 and 3.
- Each reviewer accumulates context across all 3 rounds, so by round 3 they have deep familiarity with the PR AND all other reviewers' findings.
- For very large PRs (>1000 lines), consider splitting the diff into logical chunks and reviewing each chunk separately, or just spawning more reviewers per round.
