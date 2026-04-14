## Strategy — 2026-04-13 (Cycle 5 — Fix & Harden — COMPLETE)

### Results
Both hypotheses kept. Composite score: 0.9325 → 0.9575.

| # | Exp | Hypothesis | Verdict | Score | PR |
|---|-----|-----------|---------|-------|----|
| H1 | 29 | Fix 5 mypy errors + land cross-project insights module | KEEP | 0.9325→0.955 | #40 |
| H2 | 30 | Add structlog logging to insights.py and profile.py | KEEP | 0.955→0.9575 | #41 |

### What Changed
- **type_check:** 0.75→1.0 (fixed variable shadowing in insights.py, object→str cast in study.py)
- **coverage:** 0.82→0.83 (new tests for insights and prompts modules)
- **observability:** structlog added to insights.py (4 log points) and discovery/profile.py (1 log point)
- **new feature:** cross-project insights module (category stats, pattern discovery, winning/losing strategies)
- **new feature:** insights CLI command, --projects-dir flag for study
- **agent prompts:** updated archivist, researcher, strategist with improved instructions

### Observations
- Current composite: 0.9575 (tests=1.0, lint=1.0, type_check=1.0, coverage=0.83, guards=1.0, config=1.0)
- 430 tests, 83% coverage, lint + mypy clean
- 19 experiments total (all kept, 0 reverts), 100% keep rate
- Cross-project: 3 projects, 73 experiments, 97% overall keep rate
- Observability: function coverage improved, 2 more modules instrumented

### Ideas for Next Cycle (from Research)
1. **Queryable experiment archive** — expose full traces via filesystem (Meta-Harness paper)
2. **Evaluator hardening** — variance-aware acceptance, multiple eval runs
3. **Context packet architecture** — minimal curated context per agent (paperclip)
4. **Token cost tracking** as eval dimension
5. **Coverage push** — target 85%+ (current 83%)

### Anti-patterns to Avoid
- Don't reuse loop variable names across different types (caused p shadowing bug)
- Don't add excessive logging to pure data models (models.py, templates.py)
- Don't compress diagnostic data too early — preserve raw traces

### Session State
- **Mode:** Improve (Cycle 5 — Complete)
- **Current phase:** Finalized
- **Active experiments:** None
- **Next action:** Cycle 6 — pick from ideas above
