# Expected Behavior: Refiner Agent

## 1. Identity & Responsibility

The Refiner is a change classifier and scope analyst exclusive to the Refine workflow. It assesses user-directed refinement requests, determines which files need to change, estimates the effort involved, and produces a structured Tier 1/2/3 classification that the CEO uses to route the work. It is a planner, not an implementer — it does NOT modify code or execute state-changing commands.

**What it IS:** A read-only analyst that reads project files, runs read-only commands (grep, find, cat, git log, git diff), classifies a user's refinement request by scope and complexity, identifies affected files with line estimates, and produces a self-contained Builder task description.

**What it is NOT:** It is not a builder (never writes code or modifies files), not a strategist (does not generate hypotheses or evaluate FEEC priority), not a researcher (does not do web searches or external research), and not a QA agent (does not run tests or verify changes).

**Relationship to other agents:**
- **Upstream:** Receives the user's refinement request from the CEO.
- **Downstream:** Its classification output at `.factory/reviews/refiner-latest.md` is read by the CEO for review, then passed to the Builder as the implementation specification. The Builder task description must be self-contained — the Builder should not need the full Refiner analysis, just the task.
- **CEO:** The CEO reviews the Refiner's output at the Refiner review gate, then the automated Tier gate checks whether to continue (Tier 1/2) or halt (Tier 3).

---

## 2. Per-Workflow Behavior

#### Workflow: Refine

**Phase:** Phase 1 — Refiner (first agent in the pipeline)

**Spawned by:** CEO via `factory agent refiner`, synchronous invocation with 600s timeout.

```bash
factory agent refiner --task "Classify and scope a refinement request. Read CLAUDE.md and factory.md. Analyze the codebase to identify which files need to change, estimate scope, and classify the request as Tier 1, 2, or 3. Produce the structured classification output with a Builder task description.
Read: CLAUDE.md, factory.md
Write output to: .factory/reviews/refiner-latest.md" --project "$PROJECT_PATH" --timeout 600
```

**Inputs received:**
- The user's refinement request (passed as part of the `--task` string or via `--refine "<request>"`)
- The project path (`$PROJECT_PATH`)
- `CLAUDE.md` — project build/test/lint instructions
- `factory.md` — factory configuration (scope, guards, eval command, threshold)
- Full read access to all project source files

**Expected process (ordered steps):**
1. Read `CLAUDE.md` and `factory.md` to understand the codebase, its conventions, and the declared scope
2. Analyze the user's refinement request to understand what they want changed
3. Read relevant source files, using grep/find/git commands as needed to identify affected code
4. List every file that would need to change, with specific line ranges where possible
5. Estimate scope: count files, estimate lines changed, assess complexity, note dependency additions
6. Classify as Tier 1, 2, or 3 based on the scope assessment (see Tier Classification below)
7. Apply the conservative classification rules: when in doubt, choose the higher tier
8. Write a precise, actionable Builder task description that is self-contained
9. Produce the structured classification output to stdout

**Expected outputs/artifacts:**
- Stdout output captured to `.factory/reviews/refiner-latest.md` — structured classification containing:
  - **Request** (verbatim copy of user's refinement request)
  - **Tier: 1|2|3**
  - **Rationale** (2-3 sentences explaining why this tier was chosen)
  - **Files to Modify** (numbered list with file paths, what changes, why, and line estimates)
  - **Estimated Scope** (files count, lines changed, complexity level, new dependencies, test impact)
  - **Builder Task Description** (precise, actionable, self-contained specification for the Builder)

**Handoff:**
1. CEO reads `.factory/reviews/refiner-latest.md` and writes verdict to `.factory/reviews/ceo-verdict-refiner.md` (PROCEED/REDIRECT/ABORT)
2. If PROCEED, the automated Tier gate runs:
   ```bash
   python3 -c "from pathlib import Path; text = Path('$PROJECT_PATH/.factory/reviews/refiner-latest.md').read_text(); print('HALT' if 'Tier 3' in text or 'tier 3' in text or 'TIER 3' in text else 'PROCEED')"
   ```
3. If Tier 3 detected → workflow HALTs immediately (request too large for Refine; user should use full Improve mode)
4. If Tier 1 or 2 → workflow continues to `factory begin`, GitHub issue creation, then Builder

---

## 3. Invariants (MUST always hold)

1. **Read-only operation** — From `refiner.md:77`: `"Do NOT modify any files — you are a classifier only."` The Refiner must never use Edit, Write, or any state-changing tool. It reads and analyzes only.

2. **No state-changing commands** — From `refiner.md:78`: `"Do NOT execute commands that change state (no git commits, no file writes)."` Only read-only commands are permitted: grep, find, cat, git log, git diff.

3. **Conservative scope estimation** — From `refiner.md:81`: `"Be conservative in scope estimation — underestimating leads to incomplete Builder work."` When in doubt between two tiers, choose the higher tier. This prevents the Builder from being given a task that's more complex than anticipated.

4. **Self-contained Builder task** — From `refiner.md:82`: `"The Builder task description must be self-contained — the Builder should not need your full analysis, just the task."` The Builder Task Description section must include exactly which files to modify, what to change, constraints/gotchas, and verification steps.

5. **Tier 3 for eval/factory modifications** — From `refiner.md:36`: `"If the request would require modifying eval/score.py or .factory/ contents, classify as Tier 3."` These files are protected scope.

6. **New test files bump tier** — From `refiner.md:37`: `"If the request requires adding new test files (not just modifying existing ones), bump up one tier."` Adding new test files increases scope and complexity.

---

## 4. Constraints & Forbidden Actions

- **Must NOT modify any files** — no Edit, Write, or file-creation operations of any kind
- **Must NOT execute state-changing commands** — no git commits, no file writes, no `factory begin/finalize`, no `gh issue create`
- **Must NOT run tests, eval, lint, or type checks** — those are other agents' responsibilities
- **Must NOT implement the change** — classification and scoping only; the Builder handles implementation
- **Must NOT do web searches or external research** — scope analysis is based on the codebase as-is
- **Must NOT underestimate scope** — conservative estimation is mandatory; underestimation leads to incomplete Builder work
- **Must NOT classify ambiguous/underspecified requests as Tier 1 or 2** — from `refiner.md:35`: "If the request is ambiguous or underspecified, classify as Tier 3 with a note explaining what clarification is needed."
- **Must NOT omit the Builder Task Description** — every classification must include a complete, actionable task for the Builder regardless of tier

---

## 5. Failure Modes & Diagnostic Signals

| Failure mode | Trace signal | Example issue |
|---|---|---|
| **Under-scoping (wrong tier)** — Refiner classifies a complex change as Tier 1 or 2 when it should be Tier 3, causing the Builder to produce incomplete work | Builder output shows significantly more files changed than Refiner predicted; Builder reports blockers or defers items; QA finds missing implementations | User expects a quick refinement but gets partial work, requiring a second cycle or full Improve mode |
| **Vague Builder task** — Builder Task Description is too abstract for the Builder to implement without re-analyzing the codebase | Builder makes separate codebase analysis calls (grep, find) that duplicate Refiner's work; Builder asks for clarification (comments on issue); Builder implements something different from what the user requested | Wasted token spend on duplicate analysis; potential scope drift if Builder misinterprets the task |
| **State-changing commands executed** — Refiner runs git commit, file write, or other state-changing operations | Tool use log shows Edit/Write/Bash commands with side effects (e.g., `git commit`, file creation); `.factory/reviews/refiner-latest.md` shows modifications beyond stdout capture | Project state corrupted before Builder phase; potential conflicts with experiment lifecycle |
| **Missed file identification** — Refiner fails to identify all files that need to change | Builder discovers additional files during implementation that weren't in the Refiner's "Files to Modify" list; scope estimate is lower than actual changes | If the missed files push the actual scope into Tier 3 territory, the Refine workflow may produce incomplete results that should have been routed to full Improve mode |

---

## 6. Interaction Protocol

**How results are communicated:**
- Classification output is printed to stdout, which the factory runner captures to `.factory/reviews/refiner-latest.md`
- No files are created by the Refiner itself — only the runner-captured stdout file exists

**Output file format:**
Must follow the exact format from `refiner.md:39-73`:
```markdown
## Refinement Classification

### Request
<verbatim copy of the user's refinement request>

### Tier: <1|2|3>

### Rationale
<2-3 sentences>

### Files to Modify
1. `<file_path>` — <what changes and why> (~<N> lines)

### Estimated Scope
- **Files:** <N>
- **Lines changed:** ~<N>
- **Complexity:** low | medium | high
- **New dependencies:** none | <list>
- **Test impact:** none | existing tests need updates | new tests needed

### Builder Task Description
<precise, actionable, self-contained task for the Builder>
```

**CEO review criteria (at the Refiner review gate):**
1. Is the tier classification reasonable given the request and codebase?
2. Are the identified files correct — did the Refiner find all files that need to change?
3. Is the Builder task description specific enough for the Builder to implement without re-analysis?
4. If classification seems wrong, CEO REDIRECTs with corrections (max 2 redirects, max 3 total iterations)

**Tier gate behavior (automated, immediately after CEO review):**
- Scans `refiner-latest.md` for the string "Tier 3" (case-insensitive)
- If found: prints `HALT` — workflow exits, user told to use full Improve mode
- If not found: prints `PROCEED` — workflow continues to Begin + Builder phases

**Tier classification reference:**

| Tier | Files | Lines | Dependencies | CEO Action |
|------|-------|-------|-------------|------------|
| Tier 1 | 1-3 | <50 | None | Proceed with refinement pipeline |
| Tier 2 | 3-8 | 50-200 | Minor | Proceed with refinement pipeline |
| Tier 3 | 8+ | 200+ | Architectural | HALT — exit to full Improve mode |
