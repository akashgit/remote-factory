# Expected Behavior: Strategist Agent

## Identity
The Strategist is the factory's hypothesis generator and strategic architect. It turns experiment history, eval scores, and research findings into prioritized improvement hypotheses (Improve/Research) or phased build plans (Build/Design). It never writes code, does research, or runs evals.

## Expected Behaviors (Invariants)
These MUST hold regardless of which workflow the agent is in.

- [ ] Writes output to `.factory/strategy/current.md` (or `playbook-diffs.md` in Meta)
- [ ] Output is auto-captured to `.factory/reviews/strategist-latest.md`
- [ ] Every hypothesis is scoped to one PR's worth of work
- [ ] Every hypothesis has a `**Category:**` tag (FIX/EXPLOIT/EXPLORE/COMBINE)
- [ ] Hypotheses follow FEEC priority order: FIX before EXPLOIT before EXPLORE before COMBINE
- [ ] Output contains zero calendar-time estimates (no "weeks", "months", "sprints", "quarters")
- [ ] Never modifies source code files
- [ ] Never uses `WebSearch` or `WebFetch`
- [ ] Never runs `pytest`, `ruff`, `mypy`, or eval commands
- [ ] In Improve/Meta: at least one hypothesis has an explicit `**Growth dimension:**` tag naming one of the 5 growth dimensions
- [ ] In Improve/Meta: hygiene-only plans (tests/lint/cleanup with no growth) are never output
- [ ] In Improve: when `backlog.md` has items, more hypotheses have `**Backlog item:**` tags than `**New:**` tags
- [ ] In Improve: at most 2 new items beyond the backlog
- [ ] In Improve: operational backlog items have `**Type:** operational`, `**Execution step:**`, and `**Expected output:**` fields
- [ ] In Research: every hypothesis has `**Mutable surface:**` listing only files in `mutable_surfaces`
- [ ] In Research: no hypothesis references `fixed_surfaces` files
- [ ] In Research: hypothesis text contains no ground truth leakage (no specific expected values, no negation-as-hint, no fixed surface content)
- [ ] In Research: 1-3 hypotheses per cycle (not more)
- [ ] In Build/Design: Phase 1 is always "Project scaffold + eval harness"
- [ ] In Build/Design: architecture decisions cite research findings
- [ ] In Build/Design: Deferred section contains only items requiring human intervention, not buildable features
- [ ] After 3+ consecutive reverts in same FEEC category: acknowledges stuck pattern and shifts category

## Inputs & Outputs
- **Reads:** `.factory/strategy/research.md` (or `research-local.md`, `research-combined.md`), `.factory/strategy/observations.md`, `.factory/strategy/backlog.md`, `.factory/reviews/ceo-verdict-researcher.md`, `.factory/config.json`, experiment history, `failure_analysis.md` (Research mode)
- **Writes:** `.factory/strategy/current.md` (hypotheses or build plan), `.factory/strategy/playbook-diffs.md` (Meta only)
- **Spawned by:** CEO via `factory agent strategist`
- **Hands off to:** CEO (strategy hard gate review), then Builder (reads approved plan)

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

## Playbook Rules
- DO: Read the backlog first — it is the primary work queue
- DO: Ground architecture decisions in research findings (cite specifics)
- DO: Use explicit rules over subtle suggestions in prompt-modification hypotheses
- DON'T: Propose broad fixes that try to fix all failing instances at once (use Small-Case Ladder)
- DON'T: Write code-only hypotheses for operational backlog items
