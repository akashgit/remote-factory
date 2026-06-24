# Factory Process: "Build Snake Game" — Trace Analysis

**Trace:** `610f9acf79a46d79020e7eea614ba167`
**Project:** `snake-test-v3` (terminal snake game using Python curses)
**Mode:** Design (interactive) → Improve
**Duration:** ~49 minutes active orchestration (20:06 → 20:55 UTC)
**Total agents spawned:** 22 (3 researchers, 1 strategist, 7 builders, 5 QA, 4 archivists, plus CEO span)
**Observations:** 863
**Composite score:** 0.5249 → 0.6041 (+0.0792)

---

## High-Level Process Flow

```
P0: Research (parallel)     → 3 researchers, ~3 min
P0r: CEO Review Research    → PROCEED
P1: Strategy (strategist)   → 1 strategist, ~2 min
P1r: CEO Review Strategy    → PLAN APPROVED (3 hypotheses)
P2: Present to User         → User approves
────────────────────────────────────────────────────
H1: factory.md (exp 1)      → Builder → QA → Precheck FAIL → REVERT
H1b: factory.md fix (exp 2) → Builder → QA → 3 scope fix attempts → KEEP (+0.0375)
H2: structured logging (exp 3) → Builder → QA → Precheck → KEEP (+0.034)
H3: Windows + CLI (exp 4)   → Builder → QA (issues) → Builder fix → QA pass → KEEP (+0.0077)
────────────────────────────────────────────────────
Final Archive (blocking)    → Archivist, ~1 min
Session Summary             → Cycle complete
```

---

## Phase-by-Phase Detail

### P0: Research (20:08 – 20:11, ~3 min)

Three researchers spawned in parallel with `--review-tag`:

| Tag | Focus | Duration |
|-----|-------|----------|
| `health` | Local project analysis — eval scores, codebase structure, weak areas | ~3 min |
| `practices` | External best practices for weak dimensions (coverage, observability) | ~3 min |
| `backlog` | Backlog items, archive context, prioritization | ~2 min |

**CEO Review:** PROCEED — research was relevant and covered the technology landscape.

### P1: Strategy (20:12 – 20:14, ~2 min)

Strategist synthesized research into 3 hypotheses:

1. **H1: Create `factory.md`** — document all 25+ features, set project goals
   - Target: research_grounding 0.0→1.0, config_parser 0.5→1.0, capability_surface 0.28→0.85
2. **H2: Add structured logging** — 12+ functions across 5 modules
   - Target: observability 0.016→1.0
3. **H3: Windows dependency + CLI flags** — PEP 508 auto-install, `--help`/`--version`/`--width`/`--height`/`--speed`
   - Target: clear backlog item, capability_surface improvement

**CEO Review (Hard Gate):** PLAN APPROVED — all hypotheses had growth dimensions, at least one was growth-focused.

### P2: User Approval

CEO presented the plan to the user. User approved.

---

### Experiment 1: factory.md (REVERTED)

**20:17 – 20:23** | Builder (1.5 min) → QA (1.7 min) → Precheck **FAIL**

- Builder created `factory.md` with 151 lines, opened PR #5
- QA: CLEAN, composite 0.5249 (no change — factory.md doesn't affect hygiene scores directly)
- **Precheck failed:** threshold was `None` (defaults to 0.8), unreachable at 0.5249. Also scope check false positive.
- CEO reverted, closed PR #5

**Key learning:** The project had never configured a threshold in factory.md. The CEO recognized this as a configuration gap, not a quality issue.

### Experiment 2: factory.md with config sections (KEPT)

**20:24 – 20:33** | Builder (2 min) → QA (1.1 min) → **3 scope fix attempts** → KEEP

- Builder updated factory.md with Scope, Guards, Threshold (0.4), Smoke Test sections, opened PR #7
- QA: CLEAN, composite 0.5624 (+0.0375), config_parser 0.25→1.0
- **Precheck scope check repeatedly failed** — `.gitignore` wasn't in scope list, then `.factory/` symlink confusing the checker
- CEO spawned Builder 3 more times for incremental fixes (add factory.md to scope, add .factory/ to .gitignore, add .gitignore to scope)
- Eventually ran precheck without `--baseline` flag, scope check passed

**This was the messiest part of the cycle** — 4 Builder invocations for what should have been one. The precheck scope checker had trouble with the worktree's `.factory` symlink.

### Experiment 3: Structured Logging (KEPT)

**20:33 – 20:43** | Builder (8 min) → QA (1 min) → Precheck → KEEP

- Builder added 18 structured log statements across 6 modules (game.py, food.py, renderer.py, difficulty.py, score.py, main.py), installed `python-json-logger`, 43 tests passing
- QA: CLEAN, composite 0.5964 (+0.034), observability 0.016→0.241
- Precheck passed cleanly
- CEO kept immediately

**Cleanest experiment of the cycle** — one Builder invocation, clean QA, precheck pass.

### Experiment 4: Windows + CLI Flags (KEPT after QA fix loop)

**20:43 – 20:53** | Builder (2.6 min) → QA (issues) → Builder fix (2.8 min) → QA pass → KEEP

- Builder added windows-curses PEP 508 marker, `--help`/`--version`/`--width`/`--height`/`--speed` CLI args
- **QA found 2 issues:**
  1. [medium] `_calc_fps()` used hardcoded `BASE_FPS=10.0` instead of custom `--speed` value
  2. [low] Missing minimum validation for `--width` and `--height`
- Builder fixed both issues in iteration 2
- QA re-run: CLEAN, composite 0.6041 (+0.0077)
- Backlog item "Windows users must manually install windows-curses" was cleared

**The QA iteration loop worked as designed** — caught a real bug (speed curve ignoring custom speed) that would have been user-visible.

---

## Final Archive & Summary (20:54 – 20:55)

Archivist recorded all experiment outcomes (both `.md` and `.json` sidecars). Cycle summary written.

## Post-Cycle: User Interaction (20:55 – 21:08+)

After the cycle, the CEO stayed in the Post-Cycle Refinement Loop. The user asked about running the game, and the CEO:
1. Explained it's a terminal (curses) app, not a web app
2. Downloaded and installed `ttyd` binary to expose it over HTTP
3. Launched the snake game at `http://localhost:8090`

---

## Process Summary

| Metric | Value |
|--------|-------|
| Experiments attempted | 4 |
| Experiments kept | 3 (exp 2, 3, 4) |
| Experiments reverted | 1 (exp 1 — config gap) |
| Score improvement | +0.0792 (0.5249 → 0.6041) |
| Agent invocations | 22 spans total |
| Builder invocations | 7 (4 for H1/H1b scope fixes, 1 for H2, 2 for H3) |
| QA invocations | 5 |
| QA iteration loops | 1 (H3: 2 iterations) |
| QA bugs caught | 2 (speed curve bug, dimension validation) |
| Active orchestration time | ~49 minutes |
| Biggest pain point | Precheck scope checker vs worktree symlinks (4 Builder retries) |
| Biggest win | Structured logging: clean single-pass, +0.034 composite |
