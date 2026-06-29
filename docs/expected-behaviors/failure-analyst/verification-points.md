# Verification Points: Failure Analyst

These MUST hold regardless of which workflow the agent is in. Check these against the agent's trace.

## Classification Quality
- [ ] Classifies every failed instance with: Status, Stage, Failure description, Root cause, Category (`UPPERCASE_SNAKE_CASE`), and Suggested fix
- [ ] Every classification names the specific pipeline stage that failed (e.g., localization, planning, execution, validation)
- [ ] Every classification provides a specific root cause — "the agent failed" is never acceptable
- [ ] Describes failures behaviorally ("agent only searched top-level directories"), never encodes expected outputs ("agent should have edited utils.py line 42")
- [ ] Uses consistent `UPPERCASE_SNAKE_CASE` category names across cycles — reuses existing taxonomy names before creating new ones

## Methodology
- [ ] Parses JSON/JSONL/log files programmatically (e.g., `python3 -c`, `jq`) — never skims text heuristically
- [ ] Accepts pipeline outputs as authoritative — never disputes whether a FAIL is valid, only explains WHY

## Distribution & Analysis
- [ ] Computes failure distribution with category counts and percentages
- [ ] Identifies the dominant failure mode and gives it the most attention
- [ ] When prior cycle data exists, compares: improvements, regressions, new failure modes — and accounts for problem set changes (new instances are not regressions)

## Scope Constraints
- [ ] All suggested interventions reference only files within the declared `mutable_surfaces` set

## Output Requirements
- [ ] Writes full analysis to `failure_analysis.md` in the run directory
- [ ] Prints summary to stdout containing at minimum: Summary, Failure Distribution, and Recommended Interventions

## Forbidden Actions
- Modify any source code files
- Run evals, tests, or commands that change project state
- Suggest changes to `fixed_surfaces` or `eval/score.py`
- Encode expected outputs, correct answers, or ground-truth content in the analysis
- Use negation to hint at answers (e.g., "incorrectly chose X instead of Y" leaks Y)
- Read `fixed_surfaces` files to inform analysis
- Generate formal hypotheses (that is the Strategist's job)
- Attribute failures on new problem-set instances to regression

## Failure Modes
| Signal in trace | Indicates |
|---|---|
| Per-instance entries lack `Stage` or `Root cause`, or use vague language ("test failed") | Vague classification — Strategist will produce generic hypotheses |
| Analysis contains file paths/line numbers matching ground truth | Ground truth leakage — invalidates experiment integrity |
| Intervention references files not in `mutable_surfaces` | Mutable surface violation — Builder will be blocked by scope validation |
| Same failure mode has different category names across cycles | Taxonomy inconsistency — trend analysis becomes meaningless |
| JSON files read via `Read` tool without structured extraction commands | Skimming instead of parsing — failure counts may be inaccurate |
| No `failure_analysis.md` written or stdout missing required sections | Incomplete exit — downstream agents have no input |
