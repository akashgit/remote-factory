# Verification Points: QA Agent

These MUST hold regardless of which workflow the agent is in. Check these against the agent's trace.

## Section 1: Health Check
- [ ] Runs `factory eval $PROJECT_PATH` (visible in trace)
- [ ] Reports composite score, per-dimension breakdown, and delta vs `score_before`
- [ ] Gates: if eval fails completely (no valid score), reports REVERT and stops — does NOT proceed to Sections 2/3

## Section 2: Code Review
- [ ] Gets changed files via `git diff --name-only` (NOT `gh pr diff` — that crashes the parser)
- [ ] Reads each file's diff individually (`git diff <baseline>..HEAD -- <file>`)
- [ ] Reads EVERY changed file's diff BEFORE writing any checklist result
- [ ] Evaluates all 7 categories with `file:line` evidence (correctness, security, edge cases, missing tests, style, scope, guardrails)
- [ ] Runs spec fidelity check — reads issue (`gh issue view`) and verifies acceptance criteria coverage
- [ ] Runs plan completion check — reads `.factory/strategy/current.md` and diffs against deliverables
- [ ] When `fixed_surfaces` declared: verifies none appear in `git diff --name-only`
- [ ] Gates: if critical issues found, stops — does NOT proceed to Section 3

## Section 3: Adversarial QA
- [ ] Executes REAL commands (Bash calls visible — not just file reads)
- [ ] Derives test plan from acceptance criteria BEFORE executing
- [ ] Runs smoke test from `factory.md`
- [ ] Tests the feature as a skeptical user (CLI, curl, tmux, or Playwright depending on project type)
- [ ] Every test has evidence: command + output (no evidence = NOT_VERIFIED)
- [ ] Does NOT re-run pytest/lint/mypy (that was Section 1)
- [ ] Cleans up all servers, tmux sessions, and background processes
- [ ] When in doubt, verdict is FAIL (burden of proof is on the Builder)

## Cross-Section
- [ ] Sections execute in strict order: 1 -> 2 -> 3 (with gates between)
- [ ] Final verdict is one of: `CLEAN`, `ISSUES_FOUND: N`, `REVERT`
- [ ] Does NOT modify any source files (read-only throughout)
- [ ] Does NOT make keep/revert decisions — reports findings, CEO decides
- [ ] Does NOT own the iteration loop — CEO controls Builder-QA cycles

## Forbidden Actions
- Modify any source file, `eval/score.py`, or `.factory/` contents
- Run `gh pr diff` (crashes output parser on large PRs)
- Re-run pytest/lint/mypy in Section 3
- Skip Section 3 when Sections 1 and 2 pass
- Report test results without execution evidence (command + output)
- Fill in the 7-category checklist without reading every changed file's diff
- Report high eval score as proof of integration correctness
- Count mock-only tests as evidence of integration correctness
- Leave servers, tmux sessions, or background processes running

## Failure Modes
| Signal in trace | Indicates |
|---|---|
| No `Bash` tool calls in Section 3 | Skipped adversarial testing |
| `gh pr diff` command in trace | Diff crash risk — should use per-file `git diff` |
| Fewer test scenarios than acceptance criteria; CLEAN verdict anyway | False CLEAN verdict |
| All tests mock external services; no real integration tests flagged | Mock-only integration approval |
| `tmux new-session` or `&` without corresponding `kill`/cleanup | Orphaned processes |
| No `Read` of `strategy/current.md`; scope PASS without plan comparison | Plan coverage gap |
