# Builder Review — PR #1541 Fixes

## Summary

Implemented two fixes on the existing PR #1541 (search/holdout split firewall):

### FIX 1 — Naming: `split='search'` → `split='train'`

Renamed all references from `split="search"` to `split="train"` across 6 files. "train" and "holdout" are standard ML terminology (matching PyTorch train/val/test convention).

**Files changed:**
- `factory/task.py`: `TaskInstance.split` type, `instances()` param, `_assign_splits()` defaults, `_filter_by_split()` param, docstrings
- `factory/outer_loop/engine.py`: `task.instances(split='train')` calls
- `factory/outer_loop/evaluator.py`: `record.split = 'train'`
- `factory/outer_loop/reflector.py`: Assertion message wording
- `factory/outer_loop/filesystem.py`: `run_report.json` key → `train_score`
- `tests/test_outer_loop/test_holdout.py`: All test references updated

### FIX 2 — Backward Compat Fallback

Added `try/except TypeError` in `engine.py` for when existing Task subclasses override `instances()` without the `split` parameter:

- `evolve_generation()`: Falls back to `SubsetSelector` with config lists on `TypeError`
- `run()` holdout section: Falls back to `self._config.holdout_instances` / `self._config.training_instances` on `TypeError`
- Logs `task_instances_no_split` warning when fallback activates

**New tests:**
- `TestLegacyTaskBackwardCompat::test_evolve_generation_with_legacy_task` — LegacyTask (no split param) works via config fallback
- `TestLegacyTaskBackwardCompat::test_run_with_legacy_task_no_crash` — Full `run()` cycle completes without crash

## Test Results

- `tests/test_outer_loop/test_holdout.py`: **33/33 passed**
- `tests/test_outer_loop/` (full suite): **665/665 passed** (595s)
- `ruff check`: All checks passed
