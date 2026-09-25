# Builder Review: Search/Holdout Split Firewall (#1540)

## Summary

Implemented all 9 components of the search/holdout split firewall as described in the design doc, with the user-approved modification: no automatic 80/20 seeded random split. Splits are opt-in only.

## Changes Made

### C1 — Task.instances(split=) API (`factory/task.py`)
- Added `split` field to `TaskInstance` (`Literal["search", "holdout"] | None`)
- Added `holdout_ids` field to `InstancesConfig`
- Added `split` parameter to `instances()` method (default="all")
- Added `_raw_instances()`, `_assign_splits()`, `_filter_by_split()` helper methods
- No-split fallback: all instances become `split="search"` when no explicit split configured

### C2 — Individual.holdout_score (`factory/outer_loop/models.py`)
- Added `holdout_score: float | None = None` to `Individual`
- Added `total_candidates_evaluated: int = 0` to `OuterLoopResult`

### C3 — SwarmEngine holdout firewall (`factory/outer_loop/engine.py`)
- `evolve_generation()` uses `task.instances(split="search")` when Task is available
- Deleted per-generation holdout evaluation block (was lines 416-428)
- End-of-run holdout evaluation calls `evaluate()` WITHOUT `individual_id`
- Falls back to SwarmConfig `holdout_instances` when Task has no splits
- Passes pre-computed `training_score` to `OverfitDetector.audit()`

### C4 — CycleRecordCache instance-aware keying (`factory/outer_loop/evaluator.py`)
- Added `_cache_key()` static method: `hash(workflow) + ":" + hash(sorted(instances))`
- Updated `get()` and `put()` to accept optional `instances` parameter
- Tags CycleRecords with `split="search"` in `_evaluate_via_inner_loop()`

### C5 — Reflector firewall assertion (`factory/outer_loop/reflector.py`)
- Added runtime assertion at top of `reflect()`: raises `RuntimeError` if any CycleRecord has `split == "holdout"`

### C6 — CycleRecord.split field (`factory/cycle_analyzer.py`)
- Added `split: str | None = None` field to `CycleRecord`

### C7 — Run report (`factory/outer_loop/filesystem.py`)
- `save_best()` now writes `run_report.json` with: `search_score`, `holdout_score`, `overfit_flag`, `total_candidates_evaluated`, `generations_completed`, `convergence_reason`, `total_cost_usd`

### C8 — OverfitDetector training_score param (`factory/outer_loop/overfit.py`)
- Added `training_score: float | None = None` parameter to `audit()`
- When provided, skips training evaluation

### C9 — No-split fallback
- Implemented in C1: when no explicit split declared, all instances are search
- No automatic 80/20 seeded random split (per user approval)

## Test Coverage

31 tests in `tests/test_outer_loop/test_holdout.py` covering:
- AC1: 5 tests for Task.instances(split=) API
- AC2/AC3: 1 test for engine search-only evolution
- AC4: 3 tests for reflector firewall
- AC5: 2 tests for OverfitDetector training_score
- AC6: 2 tests for CycleRecord.split field
- AC7: 1 test for run_report.json
- AC8: 4 tests for instance-aware cache keys
- AC9/AC10: 7 tests for split configuration and fallback
- AC11: 3 tests for Individual.holdout_score lifecycle
- AC12: 1 test for verify() unchanged

## Regression Status

- 137 tests pass (holdout + models + overfit + task)
- 165 smoke tests pass
- 2 previously-failing e2e/engine tests fixed and passing
- Lint clean (ruff check passes)
