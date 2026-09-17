---
name: workflow-pr-review
description: "Run the pr-review workflow."
disable-model-invocation: true
argument-hint: "<project_path>"
---

# Pr Review Workflow

The user wants: **$ARGUMENTS**

## Phase 1: Researcher — Fetch Pr

```bash
factory agent researcher --task "You are a PR data fetcher. Your job is to gather all context for a pull request and write it to a single file.

The PR number to review is provided in the context/arguments below this prompt. Extract the numeric PR number from it.

Run these steps using bash commands:

1. Create the output directory:
   mkdir -p $PROJECT_PATH/.factory/reviews

2. Fetch PR metadata (replace <PR_NUMBER> with the actual number):
   gh pr view <PR_NUMBER> --json number,title,body,author,baseRefName,headRefName,files,url,additions,deletions,labels

3. Fetch the full PR diff:
   gh pr diff <PR_NUMBER>

4. Extract linked issues from the PR body. Look for patterns like 'fixes #123', 'closes #456', 'resolves #789' (case-insensitive). For each linked issue number, fetch it:
   gh issue view <ISSUE_NUMBER> --json title,body,labels,comments

5. Write ALL gathered data to $PROJECT_PATH/.factory/reviews/pr-context.md with this structure:
   # PR Context for Review
   ## PR Metadata
   <paste JSON metadata here>
   ## PR Diff
   ```diff
   <paste full diff here>
   ```
   ## Linked Issues
   ### Issue #<N>
   <paste issue JSON here>

IMPORTANT: The output file MUST contain '## PR Metadata' and '## PR Diff' sections. Do not skip any step.
Write output to: .factory/reviews/pr-context.md" --project "$PROJECT_PATH" --timeout 120
```

```bash
# Artifact verification: fetch_pr
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/pr-context.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: fetch_pr: .factory/reviews/pr-context.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: fetch_pr: .factory/reviews/pr-context.md is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 100 ] && echo "VERIFY FAIL: fetch_pr: .factory/reviews/pr-context.md smaller than 100 bytes" && _vfail=1
[ -f "$_f" ] && ! grep -qE 'PR\ Metadata|PR\ Diff' "$_f" && echo "VERIFY FAIL: fetch_pr: .factory/reviews/pr-context.md missing required sentinel (PR Metadata, PR Diff)" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=fetch_pr" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: fetch_pr artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=fetch_pr" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Phase 2: Researcher Pr

```bash
factory agent researcher --task "You are reviewing a pull request. Read the PR context at .factory/reviews/pr-context.md.

Your task:
1. Parse the PR diff and identify every file changed, with a summary of what each change does
2. Read the linked issue(s) and summarize the problem being solved
3. Identify the code areas surrounding the changes — read the full source files that were modified to understand the broader context
4. Note any related modules, imports, or dependencies affected
5. Identify what the ROOT CAUSE of the reported issue is (from reading the issue description and the surrounding code)
6. Note whether the PR changes tests, and if so, what they verify

Write your analysis to .factory/reviews/pr-research.md with these sections:
## Issue Summary
## Root Cause Hypothesis
## Changed Files Analysis
## Surrounding Context
## Test Changes

Read: .factory/reviews/pr-context.md
Write output to: .factory/reviews/pr-research.md" --project "$PROJECT_PATH" --timeout 600
```

```bash
# Artifact verification: researcher_pr
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/pr-research.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: researcher_pr: .factory/reviews/pr-research.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: researcher_pr: .factory/reviews/pr-research.md is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 200 ] && echo "VERIFY FAIL: researcher_pr: .factory/reviews/pr-research.md smaller than 200 bytes" && _vfail=1
[ -f "$_f" ] && ! grep -qE '\#\#\ Changed\ Files\ Analysis|\#\#\ Issue\ Summary' "$_f" && echo "VERIFY FAIL: researcher_pr: .factory/reviews/pr-research.md missing required sentinel (## Changed Files Analysis, ## Issue Summary)" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=researcher_pr" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: researcher_pr artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=researcher_pr" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Phase 3: Code Reviewer — Reviewer Pr

```bash
factory agent code_reviewer --task "You are a specialist code reviewer focused on root-cause analysis and solution validation.

Read the PR context at .factory/reviews/pr-context.md and the research analysis at .factory/reviews/pr-research.md.

Perform TWO analyses:

### Analysis 1: Root Cause vs Symptom
Determine whether this PR fixes the ROOT CAUSE of the issue or just treats SYMPTOMS. Look for these symptom-fix patterns:
- Adding try/except around a crash without fixing why it crashes
- Adding null checks without fixing why the value is null
- Adding retries without fixing why the operation fails
- Hardcoding values that should be computed
- Suppressing errors/warnings instead of resolving them
- Adding special-case handling that should be a general fix

### Analysis 2: Solution Validation
Determine whether this PR actually SOLVES the stated problem. Check:
- Does the change address every aspect of the issue description?
- Are there edge cases the fix doesn't handle?
- Could the fix introduce new problems?
- Do the tests (if any) actually verify the fix works?
- Is the fix complete, or does it need follow-up work?

Write your review to .factory/reviews/reviewer-draft.md with EXACTLY this structure:

# PR Review: #<number> - <title>

## Summary
Brief overview of PR intent and scope.

## Root Cause Analysis
- **Finding:** Root cause vs symptom assessment
- **Evidence:** Specific code locations and reasoning
- **Rating:** One of: ✅ Addresses root cause | ⚠️ Partial | ❌ Symptom-only fix

## Solution Validation
- **Finding:** Does it solve the stated problem?
- **Evidence:** How the changes map to the issue requirements
- **Rating:** One of: ✅ Solves problem | ⚠️ Partial solution | ❌ Does not solve

## Recommendations
Specific, actionable improvements (if any). Reference exact files and line ranges.

## Approval Status
One of: ✅ Approve | ⚠️ Approve with comments | ❌ Request changes

Read: .factory/reviews/pr-context.md, .factory/reviews/pr-research.md
Write output to: .factory/reviews/reviewer-draft.md" --project "$PROJECT_PATH" --timeout 900
```

```bash
# Artifact verification: reviewer_pr
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/reviewer-draft.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: reviewer_pr: .factory/reviews/reviewer-draft.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: reviewer_pr: .factory/reviews/reviewer-draft.md is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 300 ] && echo "VERIFY FAIL: reviewer_pr: .factory/reviews/reviewer-draft.md smaller than 300 bytes" && _vfail=1
[ -f "$_f" ] && ! grep -qE '\#\#\ Root\ Cause\ Analysis|\#\#\ Solution\ Validation|\#\#\ Approval\ Status' "$_f" && echo "VERIFY FAIL: reviewer_pr: .factory/reviews/reviewer-draft.md missing required sentinel (## Root Cause Analysis, ## Solution Validation, ## Approval Status)" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=reviewer_pr" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: reviewer_pr artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=reviewer_pr" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

### CEO Review — Review

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/pr-research.md`, `.factory/reviews/reviewer-draft.md`
3. Assess: Evaluate the PR review draft at .factory/reviews/reviewer-draft.md against these quality criteria:

1. **Root Cause Analysis present:** The review has a Root Cause Analysis section with a Finding, Evidence, and Rating.
2. **Solution Validation present:** The review has a Solution Validation section with a Finding, Evidence, and Rating.
3. **Evidence-based:** The review references specific code locations (file names, function names, or line references) — not just opinions.
4. **Actionable:** If recommendations exist, they are concrete and specific (not vague advice like 'consider improving').
5. **Balanced:** The review acknowledges what the PR does well, not just criticism.

PROCEED if all 5 criteria are met.
RELOOP to reviewer_pr with specific feedback if any criterion fails. State which criteria failed and why.
HALT only if the review is fundamentally broken (e.g., empty, irrelevant, or reviews the wrong PR).
4. Write verdict to `.factory/reviews/ceo-verdict-review.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `reviewer_pr` (max 3 iterations)*

## Phase 4: Archivist — Finalize Pr

```bash
factory agent archivist --task "Read the approved PR review draft at .factory/reviews/reviewer-draft.md.

Copy its content to .factory/reviews/pr-review.md, preserving the exact structure. Make only minimal formatting improvements:
- Ensure markdown headers are properly formatted
- Ensure rating emojis (✅/⚠️/❌) are present
- Add a timestamp line at the top: 'Reviewed: YYYY-MM-DD'
- Do NOT change any findings, evidence, ratings, or recommendations

Write the final review to .factory/reviews/pr-review.md.
Read: .factory/reviews/reviewer-draft.md
Write output to: .factory/reviews/pr-review.md" --project "$PROJECT_PATH" --timeout 300 --model haiku
```

```bash
# Artifact verification: finalize_pr
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/pr-review.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: finalize_pr: .factory/reviews/pr-review.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: finalize_pr: .factory/reviews/pr-review.md is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 200 ] && echo "VERIFY FAIL: finalize_pr: .factory/reviews/pr-review.md smaller than 200 bytes" && _vfail=1
[ -f "$_f" ] && ! grep -qE '\#\#\ Root\ Cause\ Analysis|\#\#\ Solution\ Validation' "$_f" && echo "VERIFY FAIL: finalize_pr: .factory/reviews/pr-review.md missing required sentinel (## Root Cause Analysis, ## Solution Validation)" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=finalize_pr" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: finalize_pr artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=finalize_pr" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*
