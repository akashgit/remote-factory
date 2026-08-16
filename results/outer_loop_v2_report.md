# Outer Loop v2 — FeatureBench Evolution Report

**Date:** 2026-08-16 04:19 UTC

## 1. Key Finding: lv1 Instances Have Zero Variance

Both the 4-node pipeline (researcher→builder→health_checker→gate) and the
builder-only seed achieved **100% resolve rate** on all 10 lv1 instances.
This means lv1 tasks are too easy for evolution — no room to improve.

| Seed Type | lv1 Score | Instances Resolved |
|-----------|-----------|-------------------|
| 4-node pipeline | 1.0 | 10/10 |
| Builder-only | 1.0 | 10/10 |

## 2. Calibration — Builder-Only on lv2 (Hard Instances)

- **Seed:** featurebench-builder-only
- **Level:** lv2 (multiple functions per task)
- **Seed score:** 0%
- **Resolved:** 0/10
- **Training set:** 7 instances
- **Holdout set:** 3 instances
- **Total elapsed:** 6998s (116.6 min)

### Per-Instance Results

| Instance | Split | Score | Resolved | Time (s) |
|----------|-------|-------|----------|----------|
| `astropy` | train | 0.00 | FAIL | 259 |
| `fastapi` | train | 0.00 | FAIL | 268 |
| `transformers` | train | 0.00 | FAIL | 692 |
| `pytorch-lightning` | train | 0.00 | FAIL | 1888 |
| `mlflow` | train | 0.00 | FAIL | 160 |
| `seaborn` | train | 0.00 | FAIL | 401 |
| `pandas` | train | 0.00 | FAIL | 394 |
| `xarray` | holdout | 0.00 | FAIL | 548 |
| `sympy` | holdout | 0.00 | FAIL | 1444 |
| `meson` | holdout | 0.00 | FAIL | 943 |


## Evolution

_Not yet run._
