# Builder Output — Engine Fallback Chain Fix

## Issue
When no split is configured (empty `training_instances` + legacy Task without split param), the TypeError fallback in `engine.py` passes an empty list to SubsetSelector → zero instances → score 0.0.

## Changes

### `factory/outer_loop/engine.py`

**`evolve_generation()` (~line 288-309):**
- Added check: after TypeError catch, if `self._config.training_instances` is non-empty, use SubsetSelector (existing behavior)
- If `self._config.training_instances` is empty, call `task.instances()` with no args to get ALL instances (pre-split behavior)

**`run()` end-of-run (~line 561-568):**
- Same fix for `train_instances` resolution: if config is empty after TypeError, call `task.instances()` for all instances

### `tests/test_outer_loop/test_holdout.py`
- Added `test_legacy_task_empty_config_uses_all_instances`:
  - Creates `LegacyTaskFive` yielding 5 instances without split support
  - Configures SwarmConfig with empty `training_instances` and `holdout_instances`
  - Calls `evolve_generation()`
  - Asserts evaluator received ALL 5 instance IDs

## Test Results
- `pytest tests/test_outer_loop/test_holdout.py -v`: 34 passed ✅
- `pytest tests/test_outer_loop/ -v --timeout=300`: 666 passed ✅

## PR
Commit pushed to `factory/run-54df79cd` → PR #1541
