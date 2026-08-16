# Outer Loop v2 — FeatureBench Evolution Report

**Date:** 2026-08-16
**Branch:** `factory/run-17122260`

## 1. Key Finding: lv1 Instances Have Zero Variance

Both the 4-node pipeline (researcher→builder→health_checker→gate) and the
builder-only seed achieved **100% resolve rate** on all 10 lv1 instances.
The builder alone — with no prior codebase study — solves every lv1 task.

| Seed Type | lv1 Score | Instances | Time |
|-----------|-----------|-----------|------|
| 4-node pipeline | 100% (10/10) | 10 | 53 min |
| Builder-only | 100% (10/10) | 10 | 66 min |

**Implication:** lv1 tasks are too easy for workflow evolution. Even the
simplest possible workflow (one builder node) achieves a perfect score,
leaving zero variance for evolution to improve upon.

## 2. Calibration — Builder-Only on lv2 (Hard Instances)

lv2 instances require implementing multiple functions per task. They are
genuinely hard — the builder-only seed scored **0%** (0/10 resolved).

| Instance | Score | Resolved | Time (s) |
|----------|-------|----------|----------|
| `astropy` lv2 | 0.00 | FAIL | 259 |
| `fastapi` lv2 | 0.00 | FAIL | 268 |
| `transformers` lv2 | 0.00 | FAIL | 692 |
| `pytorch-lightning` lv2 | 0.00 | FAIL | 1888 |
| `mlflow` lv2 | 0.00 | FAIL | 160 |
| `seaborn` lv2 | 0.00 | FAIL | 401 |
| `pandas` lv2 | 0.00 | FAIL | 394 |
| `xarray` lv2 | 0.00 | FAIL | 548 |
| `sympy` lv2 | 0.00 | FAIL | 1444 |
| `meson` lv2 | 0.00 | FAIL | 943 |

**Total time:** 6998s (117 min)

## 3. Mixed Calibration (lv1 + lv2)

To get meaningful variance for evolution, we mixed easy (lv1) and hard (lv2)
instances. The builder-only seed passes lv1 but fails lv2, giving ~57% seed score.

**Training (7 instances):**
- 4× lv1 (fastapi, sympy, packaging, mlflow) — all PASS
- 3× lv2 (fastapi, mlflow, seaborn) — all FAIL
- **Seed score: 4/7 = 0.571**

**Holdout (3 instances):**
- 2× lv1 (pytest, matplotlib) — both PASS
- 1× lv2 (pandas) — FAIL
- **Expected holdout: 2/3 = 0.667**

## 4. Evolution — Initial Results (In Progress)

Evolution running with:
- **Seed:** builder-only (1 node, 0.571 training score)
- **Population:** 3 (seed + 2 designer variants)
- **Designer variants:** minimal (3 nodes), thorough (10 nodes)
- **Budget:** 20 evaluations
- **Generations:** 2

### Generation 0 — Partial Results

| Workflow | Nodes | Score | lv1 Pass | lv2 Pass |
|----------|-------|-------|----------|----------|
| Builder-only (seed) | 1 | 0.571 | 4/4 | 0/3 |
| Designer minimal (R+B+HC) | 3 | 0.571 | 4/4 | 0/3 |
| Designer thorough | 10 | (evaluating) | — | — |

**Key observation:** The 3-node designer variant (researcher→builder→health_checker)
scored the same as the builder-only seed. Adding a researcher node does NOT help
solve lv2 tasks. The difficulty of lv2 is in the implementation complexity, not
in understanding the codebase.

## 5. What the Mutations Can Discover

The mixed calibration gives evolution three axes to explore:

1. **PROMPT_MUTATE** — Improve builder instructions to better handle lv2 tasks
   (more specific guidance on multi-function implementation)
2. **NODE_INSERT** — Add specialized nodes (researcher, verifier, planner)
3. **PARALLELIZE** — Run research and building in parallel

Early evidence (designer minimal = builder-only on score) suggests that
**prompt quality matters more than pipeline structure** for these tasks.
The builder's instructions, not the workflow graph, are the bottleneck.

## 6. Difficulty Spectrum

| Level | Builder-Only | Description |
|-------|-------------|-------------|
| lv1 | 100% | Single function to implement — trivially easy |
| lv2 | 0% | Multiple functions — too hard without better prompting |

The ideal calibration set would have instances in the 30-70% difficulty
range. Options for future work:
- Find lv1.5 instances (if they exist)
- Use lv2 instances with better seed prompts (closer to 30-50%)
- Use a stronger seed (e.g., with chain-of-thought planning instructions)

## 7. Cost and Time

| Phase | Time | Instances |
|-------|------|-----------|
| lv1 calibration (4-node) | 53 min | 10 |
| lv1 calibration (builder-only) | 66 min | 10 |
| lv2 calibration (builder-only) | 117 min | 10 |
| Mixed evolution gen 0 | ~120+ min | 7×3 workflows |

Per-instance evaluation cost: ~5-30 min depending on:
- Number of workflow nodes (1 node = 5 min, 10 nodes = 30+ min)
- Instance complexity (sympy/pytorch-lightning tend to timeout)
- Agent retry overhead (timeout→retry with 2× timeout)

## 8. Summary

1. **Builder-only seed created** — `create_seed_workflow(minimal=True)` returns
   a single-node workflow with no researcher/health_checker/gate

2. **lv1 is a ceiling, not a floor** — both simple and complex workflows
   achieve 100% on lv1. No evolutionary signal.

3. **lv2 is a floor** — 0% pass rate even with researcher nodes. The hard
   part is implementation quality, not codebase understanding.

4. **Mixed calibration works** — 4 lv1 + 3 lv2 gives 0.571 seed score with
   room for evolution to discover prompt improvements

5. **Prompt > Structure** — designer's 3-node variant (with researcher)
   scores identical to builder-only. The builder prompt is the bottleneck.

6. **Evolution is running** — generation 0 in progress with 20-eval budget
   across 2 generations. Results will be written to `evolution_results.json`
   when complete.
