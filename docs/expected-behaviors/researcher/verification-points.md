# Verification Points: Researcher Agent

These MUST hold regardless of which workflow the agent is in.

## Output
- [ ] Writes output to `.factory/strategy/research.md` (or `research-local.md` / `research-<tag>.md`) before exiting
- [ ] Output is auto-captured to `.factory/reviews/researcher-latest.md` (or `researcher-<tag>-latest.md`)
- [ ] Produces a report even if external search fails — includes local findings at minimum

## Web Search Limits
- [ ] Uses `WebSearch` for external research (except Mode 1 Discovery where local analysis may suffice)
- [ ] Does not exceed 8 `WebSearch` calls in standard mode or 5 in targeted mode
- [ ] Does not exceed 5 `WebFetch` calls per invocation

## Content Constraints
- [ ] Output contains zero calendar-time estimates (no "weeks", "months", "sprints", "quarters")
- [ ] Never generates hypotheses or build plans (Strategist's job)

## Read-Only Discipline
- [ ] Never modifies source code files (read-only agent)
- [ ] Does not run eval commands (`python eval/score.py`, `factory eval`)

## Mode-Specific Invariants
- [ ] In Modes 2/3: runs `factory study` or reads local files before any `WebSearch` call
- [ ] In Modes 3/4: reads `.factory/archive/sources/` before `WebSearch` calls
- [ ] In Mode 4: 60%+ of `WebSearch` queries relate to the #1 failure category
- [ ] In Mode 4: does not do general domain research — only failure-targeted search
- [ ] In Mode 4: maps every finding to a mutable surface; flags fixed-surface needs as constraints, not recommendations
- [ ] In Mode 1: writes `.factory/eval_profile.json` and `eval/score.py`; sets `human_reviewed: false`

## Forbidden Actions
- Modifying any source code file
- Running tests, linters, or eval commands
- Generating hypotheses or build plans
- Including calendar-time estimates in output
- Mode 4: general domain research (must be failure-targeted)
- Mode 4: recommending changes to `fixed_surfaces` files

## Failure Modes
| Signal in trace | Indicates |
|---|---|
| Output contains "weeks", "months", "sprints", "quarters" | Calendar-time estimate violation — CEO will REDIRECT |
| `WebSearch` count > 8 (standard) or > 5 (targeted) | Excessive web search — token waste |
| No `factory study` call before first `WebSearch` (Modes 2/3) | Missing local study baseline |
| No `Read` of `.factory/archive/` before `WebSearch` (Modes 3/4) | Archive skip — may duplicate prior research |
| WebSearch queries don't reference failure categories (Mode 4) | General research instead of failure-targeted |
| Output file missing required sections | Incomplete report — CEO will REDIRECT |
| `**Mutable surface:**` references files in `fixed_surfaces` list (Mode 4) | Fixed surface recommendation violation |
