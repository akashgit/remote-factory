# Verification Points: Strategist Agent

These MUST hold regardless of which workflow the agent is in.

## Output
- [ ] Writes output to `.factory/strategy/current.md` (or `playbook-diffs.md` in Meta)
- [ ] Output is auto-captured to `.factory/reviews/strategist-latest.md`

## Hypothesis Quality
- [ ] Every hypothesis is scoped to one PR's worth of work
- [ ] Every hypothesis has a `**Category:**` tag (FIX/EXPLOIT/EXPLORE/COMBINE)
- [ ] Hypotheses follow FEEC priority order: FIX before EXPLOIT before EXPLORE before COMBINE

## Content Constraints
- [ ] Output contains zero calendar-time estimates (no "weeks", "months", "sprints", "quarters")

## Read-Only Discipline
- [ ] Never modifies source code files
- [ ] Does not use `WebSearch` or `WebFetch` (research is the Researcher's job)
- [ ] Does not run eval commands directly

## Improve/Meta Mode
- [ ] At least one hypothesis has an explicit `**Growth dimension:**` tag naming one of the 5 growth dimensions
- [ ] Hygiene-only plans (tests/lint/cleanup with no growth) are never output
- [ ] When `backlog.md` has items, more hypotheses have `**Backlog item:**` tags than `**New:**` tags
- [ ] At most 2 new items beyond the backlog
- [ ] Operational backlog items have `**Type:** operational`, `**Execution step:**`, and `**Expected output:**` fields

## Research Mode
- [ ] Every hypothesis has `**Mutable surface:**` listing only files in `mutable_surfaces`
- [ ] No hypothesis references `fixed_surfaces` files
- [ ] Hypothesis text contains no ground truth leakage (no specific expected values, no negation-as-hint, no fixed surface content)
- [ ] 1-3 hypotheses per cycle (not more)

## Build/Design Mode
- [ ] Phase 1 is always "Project scaffold + eval harness"
- [ ] Architecture decisions cite research findings
- [ ] Deferred section contains only items requiring human intervention, not buildable features

## Stuck Detection
- [ ] After 3+ consecutive reverts in same FEEC category: acknowledges stuck pattern and shifts category

## Forbidden Actions
- Writing or modifying source code
- Using `WebSearch` or `WebFetch` (Researcher's job)
- Running tests, evals, or linters
- Including calendar-time estimates
- Repeating a reverted hypothesis without a substantially different approach
- Proposing changes outside project guards (`factory.md` scope)
- Research mode: proposing changes to `fixed_surfaces`
- Research mode: reading `fixed_surfaces` content to inform hypotheses
- Research mode: encoding expected outputs or using negation-as-hint in hypothesis text

## Failure Modes
| Signal in trace | Indicates |
|---|---|
| `current.md` has no `**Growth dimension:**` tag (Improve/Meta) | All-hygiene plan — CEO will REDIRECT |
| More `**New:**` tags than `**Backlog item:**` tags when backlog non-empty | Backlog ignored — CEO will REDIRECT |
| Operational item with `**Type:** code` instead of `operational`/`mixed` | Code-only for operational item — CEO will REDIRECT |
| Output contains "weeks", "months", "sprints" | Calendar-time estimate — CEO will REDIRECT |
| `**Mutable surface:**` references a `fixed_surfaces` file | Fixed surface violation (Research mode) |
| Hypothesis text contains specific values from test data or negation hints | Ground truth leakage (Research mode) |
| 3+ consecutive reverts in same category, new plan proposes same category | Stuck loop not detected |
| `**What:**` field lacks specific files or changes | Vague hypothesis — Builder will need clarification |
| Build plan Phase 1 is not scaffold + eval | Missing scaffold phase — CEO will REDIRECT |
