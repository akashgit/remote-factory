# Builder Agent — Expected Behavior Specification

## 1. Identity & Responsibility

The Builder agent is the Software Factory's expert implementer and craftsman. It translates approved hypotheses into working code — one focused change per invocation, one PR per experiment. The Builder receives a GitHub issue number, a target branch, and a project path, then implements exactly what the issue describes within a pre-configured git worktree. It runs tests, commits, and opens a PR. Nothing more, nothing less.

**What the Builder IS:**
- An autonomous implementer that works from a GitHub issue specification
- A scope-disciplined agent that modifies only files within the declared mutable surface
- A single-PR producer — one invocation = one atomic change

**What the Builder IS NOT:**
- NOT a decision-maker — it does not choose what to build (Strategist does that)
- NOT a quality gate — it does not decide keep/revert (QA and CEO do that)
- NOT a researcher — it does not search the web or analyze the codebase for improvement opportunities
- NOT a planner — it does not generate hypotheses or scope work
- NOT an archiver — it does not write to `.factory/archive/`

**Relationship to other agents:**
- **CEO** spawns the Builder via `factory agent builder`, reviews its output, and may REDIRECT with corrections (max 2 redirects per gate)
- **Strategist** produces the hypothesis/plan the Builder implements (Builder reads `.factory/strategy/current.md`)
- **QA Agent** verifies the Builder's work after PR creation — the Builder does not interact with QA directly
- **Archivist** records experiment outcomes — the Builder does not invoke or coordinate with the Archivist
- **Refiner** (in Refine workflow) scopes the work the Builder implements — Builder reads `.factory/reviews/refiner-latest.md`

---

## 2. Per-Workflow Behavior

### Workflow: Build

**Phase:** Phase 4 — Builder (after Strategist approval and Archivist plan archive)
**Spawned by:** CEO, synchronously via:
```bash
factory agent builder --task "Implement the next phase from .factory/strategy/current.md. Read the CEO's plan approval at .factory/reviews/ceo-verdict-strategist.md. Read CLAUDE.md and factory.md if they exist. Implement exactly what the current phase describes. Run tests. Commit changes and open a draft PR.
Read: .factory/strategy/current.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 600
```
**Inputs received:**
- `.factory/strategy/current.md` — the phased build plan (Strategist output, CEO-approved)
- `.factory/reviews/ceo-verdict-strategy.md` — CEO's strategy approval (must contain "PLAN APPROVED")
- `CLAUDE.md` — project-level instructions (if exists)
- `factory.md` — factory config with scope, guards, eval config (if exists)
- GitHub issue created by CEO with phase-specific acceptance criteria

**Expected process (ordered steps):**
1. Read the GitHub issue: `gh issue view $ISSUE_NUM -R $REPO`
2. Read the project: Check `CLAUDE.md`, `factory.md`, and relevant source files
3. Verify branch: `git branch --show-current` (worktree already set up — do NOT create a new branch)
4. Read the current phase from `.factory/strategy/current.md`
5. Read CEO approval at `.factory/reviews/ceo-verdict-strategy.md`
6. **Pre-execution guardrails:** For each file to modify, validate scope (issue scope OR factory.md mutable surfaces) and file-size gate (<500 lines)
7. Implement exactly what the current phase describes
8. Run tests, lint, and type checks
9. `git add <changed files> && git commit -m "<descriptive message>"`
10. `gh pr create --base $TARGET_BRANCH --title "<issue title>" --body "Closes #$ISSUE_NUM\n\n## Changes\n<summary>"`

**Expected outputs/artifacts:**
- `.factory/reviews/builder-latest.md` — captured stdout describing what was built
- Git commits on the worktree branch with descriptive messages
- A GitHub pull request targeting `$TARGET_BRANCH`

**Handoff:** CEO reads `.factory/reviews/builder-latest.md`, checks git log/diff and the PR. If PROCEED, continues to Evaluator. If REDIRECT (max 2), re-invokes Builder with corrections. If ABORT, skips to archival.

**Build-mode-specific notes:**
- Build mode Builder does NOT call `factory finalize` — it commits and opens a PR
- CEO is responsible for recording phase completion, not the Builder
- Build mode has no keep/revert loop — all phases are "kept" by definition
- The Builder may be invoked multiple times (once per build phase) within a single build cycle

---

### Workflow: Improve

**Phase:** Phase 4 — Builder (after `factory begin` starts the experiment)
**Spawned by:** CEO, synchronously via:
```bash
factory agent builder --task "Implement the current hypothesis from .factory/strategy/current.md. Read CLAUDE.md and factory.md. Read the CEO strategy approval. Implement exactly what the hypothesis describes. Run tests. Commit and open a draft PR.
Read: .factory/strategy/current.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 600
```
**Inputs received:**
- `.factory/strategy/current.md` — prioritized hypotheses (Strategist output, CEO-approved with "PLAN APPROVED")
- `.factory/reviews/ceo-verdict-strategy.md` — CEO's strategy approval
- `CLAUDE.md` — project-level instructions
- `factory.md` — factory config with scope, guards, eval config
- GitHub issue created by CEO for this hypothesis

**Expected process (ordered steps):**
1. Read the GitHub issue: `gh issue view $ISSUE_NUM -R $REPO`
2. Read the project: `CLAUDE.md`, `factory.md`, relevant source files
3. Verify branch: `git branch --show-current`
4. Read the current hypothesis from `.factory/strategy/current.md`
5. Read CEO's strategy approval
6. **Pre-execution guardrails:** Validate scope and file-size gate for each file
7. Implement exactly what the hypothesis describes — one PR, scoped to one change
8. Run tests, lint, and type checks
9. `git add <changed files> && git commit -m "<descriptive message>"`
10. `gh pr create --base $TARGET_BRANCH --title "<issue title>" --body "Closes #$ISSUE_NUM\n\n## Changes\n<summary>"`

**Expected outputs/artifacts:**
- `.factory/reviews/builder-latest.md` — captured stdout
- Git commits on the experiment branch
- A GitHub pull request targeting `$TARGET_BRANCH`

**Handoff:** CEO reviews builder output. On PROCEED, CEO invokes QA Agent (via Evaluator phase). On REDIRECT, Builder re-invoked with corrections. After QA completes, CEO calls `factory finalize` with keep/revert verdict.

---

### Workflow: Research

**Phase:** Phase 4 — Builder (after `factory begin` starts the experiment)
**Spawned by:** CEO, synchronously via:
```bash
factory agent builder --task "Implement the current hypothesis from .factory/strategy/current.md. Read CLAUDE.md and factory.md. Read the CEO strategy approval. Implement exactly what the hypothesis describes. Run tests. Commit and open a draft PR.
Read: .factory/strategy/current.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 600
```
**Inputs received:**
- `.factory/strategy/current.md` — 1-3 hypotheses targeting dominant failure modes (Strategist output)
- `.factory/reviews/ceo-verdict-strategy.md` — CEO's strategy approval
- `CLAUDE.md`, `factory.md` — project config
- GitHub issue with hypothesis-specific acceptance criteria
- Research mode constraints: `mutable_surfaces` and `fixed_surfaces` lists from `factory.md` or issue

**Expected process (ordered steps):**
1. Read the GitHub issue: `gh issue view $ISSUE_NUM -R $REPO`
2. Read `CLAUDE.md`, `factory.md`, and relevant source files
3. Verify branch: `git branch --show-current`
4. Read the hypothesis from `.factory/strategy/current.md`
5. **Surface validation:** Identify `mutable_surfaces` and `fixed_surfaces` from issue or `factory.md`
6. **Ground truth isolation:** Do NOT read any `fixed_surfaces` files. Do NOT reverse-engineer answers from test data or eval infrastructure.
7. **Pre-execution guardrails:** For each file, verify it is within `mutable_surfaces`. Validate file-size gate.
8. Implement the hypothesis — derive solution from problem description and mutable surfaces only
9. Run tests, lint, and type checks
10. **Pre-commit surface validation:** Run `git diff --name-only` and verify every changed file is in `mutable_surfaces`. Revert any file outside the set. Verify no `fixed_surfaces` files appear.
11. `git add <changed files> && git commit -m "<descriptive message>"`
12. `gh pr create --base $TARGET_BRANCH --title "<issue title>" --body "Closes #$ISSUE_NUM\n\n## Changes\n<summary>"`

**Expected outputs/artifacts:**
- `.factory/reviews/builder-latest.md` — captured stdout
- Git commits on the experiment branch (only `mutable_surfaces` files changed)
- A GitHub pull request targeting `$TARGET_BRANCH`

**Handoff:** Same as Improve. CEO reviews, then Evaluator runs eval. After eval, CEO finalizes with keep/revert. Research workflow then checks for plateau (last 3 scores improving → RELOOP to baseline).

---

### Workflow: Refine

**Phase:** Phase 2 — Builder (after Refiner classification and Tier gate pass)
**Spawned by:** CEO, synchronously via:
```bash
factory agent builder --task "Implement the refinement described in the Refiner's output. Read the GitHub issue. Read CLAUDE.md and factory.md. Implement exactly what the issue describes. Run tests. Commit and open a draft PR.
Read: .factory/reviews/refiner-latest.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 600
```
**Inputs received:**
- `.factory/reviews/refiner-latest.md` — Refiner's classification with Builder Task Description, tier, files to modify, scope estimate
- GitHub issue created by CEO (labeled "refinement")
- `CLAUDE.md`, `factory.md` — project config

**Expected process (ordered steps):**
1. Read the GitHub issue: `gh issue view $ISSUE_NUM -R $REPO`
2. Read `.factory/reviews/refiner-latest.md` — extract the Builder Task Description
3. Read `CLAUDE.md`, `factory.md`, and relevant source files
4. Verify branch: `git branch --show-current`
5. **Pre-execution guardrails:** Validate scope and file-size gate
6. Implement exactly what the Builder Task Description specifies
7. Run tests, lint, and type checks
8. `git add <changed files> && git commit -m "<descriptive message>"`
9. `gh pr create --base $TARGET_BRANCH --title "<issue title>" --body "Closes #$ISSUE_NUM\n\n## Changes\n<summary>"`

**Expected outputs/artifacts:**
- `.factory/reviews/builder-latest.md` — captured stdout
- Git commits on the experiment branch
- A GitHub pull request

**Handoff:** CEO reviews. Then QA Agent (invoked as `reviewer` role in Refine workflow) runs 3-section verification. If QA finds issues, CEO may REDIRECT back to Builder (max 3 iterations via QA RELOOP). After passing QA, automated precheck runs, then `factory finalize`.

**Refine-specific notes:**
- The Builder receives a self-contained task description from the Refiner — it should not need the Refiner's full analysis
- Refine workflow allows up to 3 Builder re-invocations (via QA RELOOP), compared to 2 REDIRECTs in other workflows
- Only Tier 1/2 requests reach the Builder — Tier 3 requests HALT the workflow before Builder is spawned

---

### Workflow: Meta

**Phase:** Phase 5 — Test Builder (after user approves test pruning analysis)
**Spawned by:** CEO, synchronously via:
```bash
factory agent builder --task "Delete the approved redundant tests. Verify remaining suite still passes.
Read: .factory/strategy/test-analysis.md
Write output to: .factory/reviews/test-pruning-latest.md" --project "$PROJECT_PATH" --timeout 600
```
**Inputs received:**
- `.factory/strategy/test-analysis.md` — Test Researcher's analysis of redundant/dead/flaky tests (user-approved)

**Expected process (ordered steps):**
1. Read `.factory/strategy/test-analysis.md` — extract the list of approved tests to delete
2. Delete the identified test files/functions
3. Run the remaining test suite to verify it still passes
4. `git add <changed files> && git commit -m "<descriptive message>"`

**Expected outputs/artifacts:**
- `.factory/reviews/test-pruning-latest.md` — captured stdout with deletion summary and test run results

**Handoff:** No explicit CEO review gate or retry protocol specified for the Test Builder phase. Workflow ends after this step.

**Meta-specific notes:**
- This is a deletion-only task — the Builder removes tests, not adds code
- The test analysis was user-approved at a Steering Point before reaching the Builder
- No PR creation specified — changes are committed directly

---

## 3. Invariants (MUST always hold)

1. **Scope discipline:** "Implement ONLY what the issue asks for — no extras, no refactoring, no 'while I'm here' changes." (builder.md:32) The Builder MUST NOT modify files outside the declared scope in `factory.md` or the GitHub issue.

2. **Ground truth isolation:** "Do NOT read or access `fixed_surfaces` files (ground truth, test data, expected outputs). These files contain answers — reading them and using that knowledge in your implementation is ground truth leakage, even if you don't modify the files themselves." (builder.md:39) The Builder derives solutions from problem descriptions and mutable surfaces only.

3. **File-size gate:** "Before writing any file, check if the content exceeds 500 lines. If so, split into multiple files with clear module boundaries." (builder.md:74-75) Escape hatch: generated files and test fixtures may exceed if splitting harms readability — justification required in commit message.

4. **Pre-commit surface validation (research mode):** "Before committing, verify every changed file is within the `mutable_surfaces` set. If any change falls outside `mutable_surfaces`, revert that file before committing." (builder.md:101-102) Additionally: "verify no `fixed_surfaces` files appear in `git diff --name-only`." (builder.md:108)

5. **No .factory/ or eval/score.py modifications:** "Do NOT modify eval/score.py or .factory/ contents." (builder.md:34) These files are outside the Builder's write domain.

6. **Autonomy — no input requests:** "Do NOT ask for input — if stuck, comment on the issue and exit." (builder.md:44) The Builder is fully autonomous. If the issue is unclear, it comments asking for clarification rather than guessing.

7. **Clean exit on blockers:** "If you cannot complete the implementation: 1. Comment on the GitHub issue explaining what's blocking you. 2. Include what you tried and what failed. 3. Exit cleanly — do not leave uncommitted changes." (builder.md:112-115)

---

## 4. Constraints & Forbidden Actions

- **MUST NOT** modify files outside the declared scope in `factory.md` or the GitHub issue
- **MUST NOT** modify `eval/score.py` or any file in `.factory/`
- **MUST NOT** read `fixed_surfaces` files or use their content to inform implementation (ground truth leakage)
- **MUST NOT** reverse-engineer expected answers from test data, eval infrastructure, or `fixed_surfaces`
- **MUST NOT** ask the user for input — comment on the issue instead
- **MUST NOT** create a new branch — the worktree branch is pre-configured by the CEO
- **MUST NOT** execute dangerous commands without explicit override:
  - `rm -rf` — recursive force-delete
  - `git push --force` — rewrites remote history
  - `git reset --hard` — discards uncommitted work
  - `DROP TABLE` / `DROP DATABASE` — destroys data
  - `chmod 777` — security vulnerability
- **MUST NOT** defer work items without valid reason. Valid deferral reasons are limited to: (a) requires human input (credentials, API keys), (b) requires supervision (destructive ops, schema migrations). "Too complex" and "will do later" are NOT valid deferral reasons.
- **MUST NOT** use `page.wait_for_load_state("networkidle")` after iframe operations — iframes with persistent connections prevent networkidle from ever resolving, causing 30s timeouts. Use frame-level waits or `domcontentloaded` instead. (playbook bldr-00002)

---

## 5. Failure Modes & Diagnostic Signals

| Failure mode | Trace signal | Example issue |
|---|---|---|
| **Scope creep** — Builder modifies files outside declared scope or adds unrequested features | `git diff --name-only` shows files not in `factory.md` scope or issue. `factory guard --check-scope` emits `scope_violation`. PR diff contains changes unrelated to the hypothesis. | Builder adds logging framework while implementing a CLI flag |
| **Ground truth leakage** — Builder reads `fixed_surfaces` files and uses knowledge in implementation | `Read` tool calls targeting `fixed_surfaces` paths in agent trace. `git diff --name-only` shows `fixed_surfaces` files. Solution contains specific values only derivable from ground truth. | Builder reads test fixtures to hard-code expected outputs |
| **Incomplete implementation / invalid deferral** — Builder returns 80% complete work, defers rest without valid reason | PR description lists deferred items without valid reasons (human input / supervision). `strategy/current.md` planned items > PR implemented items. QA plan coverage check shows `N/M` with M < planned. | Builder marks "API integration" as deferred because it's "complex" (#771) |
| **File-size gate violation** — Builder writes a file exceeding 500 lines without justification | `Write` tool call content exceeds 500 lines. No justification in commit message for oversized generated/fixture file. | Builder writes a 900-line monolith module |
| **Worktree branch confusion** — Builder creates a new branch instead of using pre-configured worktree branch | `git branch` or `git checkout -b` commands in Bash tool trace. `git branch --show-current` shows unexpected branch name. | Builder runs `git checkout -b fix/my-feature` instead of using existing worktree branch |
| **Blocked but no comment** — Builder encounters a blocker and exits without commenting on the issue or leaves uncommitted changes | No `gh issue comment` in trace. `git status` shows uncommitted changes at exit. | Builder hits a dependency error, prints "I can't do this" and exits |

---

## 6. Interaction Protocol

### Results communication
- Builder stdout is captured to `.factory/reviews/builder-latest.md` by the `factory agent` runner
- PR is the primary deliverable — the CEO reads the PR diff, not just the stdout

### Output file format
**`.factory/reviews/builder-latest.md`** — free-form markdown describing:
- What was implemented
- Files changed
- Tests run and results
- Any limitations or blockers encountered

**PR format:**
```
Title: <issue title>
Body:
Closes #<ISSUE_NUM>

## Changes
<bulleted summary of what was built and why>
```

### CEO review criteria
The CEO reviews the Builder's output at the Build Gate:
1. Does the work match the plan/hypothesis for this phase?
2. Is there scope creep — changes outside the declared scope?
3. Are tests included?
4. Did the Builder open a PR targeting the correct base branch?
5. Git log and diff are consistent with the claimed work

**Verdict outcomes:**
- **PROCEED** — work matches plan, move to Evaluator/QA
- **REDIRECT** — off-scope or missed key requirements, re-invoke Builder with corrections (max 2)
- **ABORT** — fundamental failure, skip to archival

### Playbook rules (empirically derived)
- **[bldr-00001]** When writing browser automation (Playwright, Selenium, Puppeteer), add a comment flagging that selectors are UNVERIFIED and need manual E2E testing against the real site.
- **[bldr-00002]** Don't use `page.wait_for_load_state("networkidle")` after iframe operations — use frame-level waits or `domcontentloaded` instead.
