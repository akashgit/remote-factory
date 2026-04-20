## Strategy — 2026-04-20 (Meta Cycle)

**Composite:** 0.8009
**Mode:** Meta (self-improvement)
**FEEC analysis:** No critical bugs (Fix=none). Three growth dimensions are weak: capability_surface (0.62), experiment_diversity (0.53), research_grounding (0.645). Exploit these gaps. Last 3 experiments were all eval_improvement -- must break the monotony.

**Score profile:**
- Hygiene (near-perfect): tests=1.0, lint=1.0, type_check=0.9, coverage=0.84, guard_patterns=1.0, config_parser=1.0
- Growth (weak): capability_surface=0.62, experiment_diversity=0.53, observability=0.81, research_grounding=0.645, factory_effectiveness=0.70

---

### Hypotheses

#### H1: Add `factory checkpoint` and `factory resume` commands for crash-resilient orchestration

- **What:** Create a new `factory/checkpoint.py` module with `save_checkpoint()` and `load_checkpoint()` functions that serialize CEO state (current mode, active experiment ID, completed agents, pending agents, last eval scores) to `.factory/checkpoint.json`. Add two new CLI commands in `cli.py`: `cmd_checkpoint` (save/show current state) and `cmd_resume` (resume from last checkpoint). Update the CEO agent prompt (`factory/agents/prompts/ceo.md`) to call `factory checkpoint` after each agent completes. Add `CheckpointState` model to `factory/models.py`.
- **Why:** The research report identifies "Checkpointing and Recovery" as a MEDIUM priority orchestration resilience feature. Users have experienced lost progress when the CEO hits context limits or laptop lids close (documented in observations.md). This adds 2 new CLI entry points, a new module with 4+ public functions, and a new Pydantic model -- directly expanding capability surface. The checkpoint/resume pattern is standard in production orchestrators (LangGraph, CrewAI) but missing from the factory.
- **Growth dimension:** capability_surface
- **Expected impact:** capability_surface 0.62 -> 0.67 (+0.05, adds ~14 surface units: 1 module + 2 entry_points + ~8 public functions + model class). experiment_diversity improves by breaking the eval_improvement streak (this is an "infrastructure" category experiment). Composite +0.015-0.025.
- **Priority:** HIGH
- **FEEC:** Explore
- **Files:** `factory/checkpoint.py` (new), `factory/models.py` (add CheckpointState), `factory/cli.py` (add cmd_checkpoint, cmd_resume), `factory/agents/prompts/ceo.md` (checkpoint after each agent), `tests/test_checkpoint.py` (new)

#### H2: Fix research_grounding doc_ratio by restructuring Archivist vault output and scoring

- **What:** The `eval_research_grounding` function in `factory/eval/growth.py` checks for experiment notes in `vault/10-Projects/<name>/Experiments/*.md` (doc_ratio sub-score, 25% weight). The Archivist currently writes notes to `vault/10-Projects/<name>/Exp-NNN-*.md` (flat, no subdirectory), causing doc_ratio=0.00 despite 12 notes existing. Two changes needed: (1) Update `factory/obsidian/templates.py` and `factory/obsidian/notes.py` to write experiment notes into an `Experiments/` subdirectory: `vault/10-Projects/<name>/Experiments/Exp-NNN-*.md`. (2) Update the Archivist prompt (`factory/agents/prompts/archivist.md`) to use the `Experiments/` subdirectory path. Also update the doc_ratio calculation in `growth.py` to additionally check for flat `Exp-*.md` files at the project level as a fallback, so existing notes count retroactively.
- **Why:** research_grounding is at 0.645 with doc_ratio=0.00 contributing zero despite 12 experiment notes already existing in the vault. This is a scoring bug combined with a structural mismatch. The vault has `Exp-031-config-parser-fix.md` etc. at the project root, but the eval checks `Experiments/` subdirectory. Fixing this alignment is a pure Exploit move: the data exists, the measurement is wrong. Sources=26 (capped), utilization=0.70, research_report=yes -- only doc_ratio drags the score down. Fixing it should boost doc_ratio from 0.00 to ~0.41 (12/29).
- **Growth dimension:** research_grounding
- **Expected impact:** research_grounding 0.645 -> 0.75 (+0.10, doc_ratio 0.00 -> 0.41). Composite +0.008-0.012 (research_grounding weight is 0.16 within growth, growth is 50% of composite). experiment_diversity also benefits: this is a "feature" category experiment, breaking the eval_improvement streak.
- **Priority:** HIGH
- **FEEC:** Exploit
- **Files:** `factory/eval/growth.py` (fix doc_ratio fallback in eval_research_grounding), `factory/obsidian/templates.py` (Experiments/ subdirectory), `factory/obsidian/notes.py` (path update), `factory/agents/prompts/archivist.md` (path guidance), `tests/test_eval_growth.py` (test doc_ratio with both layouts)

#### H3: Add `factory diff` command for cross-experiment comparison and delta analysis

- **What:** Create `cmd_diff` in `factory/cli.py` that compares two experiments side-by-side: `factory diff 31 34` shows hypothesis, score_before/after, delta, dimension-level diffs (which dimensions improved/regressed), and a unified diff of their changes. Also add `cmd_explain` that takes an experiment ID and prints a structured analysis: hypothesis categorization (FEEC), dimension impact breakdown, and whether the experiment pattern matches any known cross-project insight. Add corresponding helper functions in a new `factory/analysis.py` module: `compare_experiments()`, `explain_experiment()`, `dimension_diff()`. These are analytical tools that help the CEO and human operators understand what worked and why -- currently there is no way to compare experiments or decompose their impact.
- **Growth dimension:** capability_surface
- **Expected impact:** capability_surface 0.62 -> 0.66 (+0.04, adds ~12 surface units: 1 module + 2 entry_points + ~6 public functions). experiment_diversity benefits from a "feature" category experiment. factory_effectiveness benefits indirectly: better analysis tools lead to better hypothesis quality in future cycles. Composite +0.010-0.020.
- **Priority:** MEDIUM
- **FEEC:** Explore
- **Files:** `factory/analysis.py` (new), `factory/cli.py` (add cmd_diff, cmd_explain), `tests/test_analysis.py` (new)

---

### Execution Order

1. **H2 first** (research_grounding fix) -- lowest risk, highest certainty of impact, fixes a known scoring bug. Quick to implement.
2. **H1 second** (checkpoint/resume) -- highest capability surface impact, adds infrastructure the CEO needs for reliability.
3. **H3 third** (diff/explain) -- new analytical capability, builds on experiment data already available.

### Anti-patterns to Avoid
- Last 3 experiments were all "eval_improvement" -- none of these hypotheses are eval_improvement. Categories: infrastructure (H1), feature (H2, H3).
- Don't propose tests/lint/coverage improvements -- already near-perfect (1.0, 1.0, 0.84).
- Don't add hygiene-only changes. All 3 hypotheses target growth dimensions.
- 100% keep rate across 66 experiments suggests evals may be too easy -- focus on meaningful capability additions, not score-gaming.
- Don't just add docstrings or comments to inflate scores -- each hypothesis must add real functionality.

### Growth Dimension Coverage
- **capability_surface** (0.62): targeted by H1 (+0.05) and H3 (+0.04)
- **experiment_diversity** (0.53): all 3 hypotheses break the eval_improvement streak; categories are infrastructure + feature
- **research_grounding** (0.645): directly targeted by H2 (+0.10)
- **factory_effectiveness** (0.70): indirectly improved by H1 (crash recovery) and H3 (better analysis)
- **observability** (0.81): not targeted (already strong)
