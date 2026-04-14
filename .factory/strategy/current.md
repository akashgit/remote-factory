## Strategy — 2026-04-13 (Cycle 4 — Hardening & Observability — COMPLETE)

### Results
All 3 hypotheses kept. Composite score: 0.945 → 0.9525.

| # | Exp | Hypothesis | Verdict | Score | PR |
|---|-----|-----------|---------|-------|----|
| H1 | 26 | Fix mypy error + land digest feature | KEEP | 0.945→0.95 | #35 |
| H2 | 27 | Add structured logging to 5 uninstrumented modules | KEEP | 0.95 (maintained) | #37 |
| H3 | 28 | FEEC priority heuristic in strategist | KEEP | 0.95→0.9525 | #39 |

### What Changed
- **type_check:** 0.95→1.0 (fixed mypy redefinition in study.py)
- **coverage:** 80%→81% (41 new tests for strategy.py)
- **observability:** function logging coverage improved from 26% to ~50% (structlog added to store, digest, state, introspect, obsidian/notes)
- **new feature:** `factory digest` command for vault activity summaries
- **new feature:** `factory/strategy.py` FEEC module for principled hypothesis ordering
- **strategist prompt:** updated with FEEC framework and stuck protocol

### Observations
- Current composite: 0.9525 (tests=1.0, lint=1.0, type_check=1.0, coverage=0.81, guards=1.0, config=1.0)
- 357 tests, 81% coverage, lint + mypy clean
- 17 experiments total (all kept, 0 reverts)
- All FEEC categories: H1=FIX, H2=EXPLOIT (observability), H3=EXPLORE (new capability)

### Ideas for Next Cycle (from Research)
1. **Queryable experiment archive** — expose full traces via filesystem (Meta-Harness paper, 0.1x eval budget)
2. **Evaluator hardening** — variance-aware acceptance, multiple eval runs (awesome-autoresearch)
3. **Fix Obsidian CLI nested path bug** — `name=` vs `path=` in `_obsidian_create`
4. **Context packet architecture** — minimal curated context per agent (paperclip)
5. **Token cost tracking** as eval dimension (OpenSpace)

### Anti-patterns to Avoid
- Don't compress diagnostic data too early — preserve raw traces
- Don't trust single eval runs — consider multi-run variance
- Don't allow compound changes — one atomic change per experiment

### Session State
- **Mode:** Improve (Cycle 4 — Complete)
- **Current phase:** Finalized
- **Active experiments:** None
- **Next action:** Cycle 5 — pick from ideas above
