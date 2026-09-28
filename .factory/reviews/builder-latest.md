# Builder Review — Empty Subset Selector Guard

## Summary
Fixed a bug where an empty `FixedSubsetSelector` (from unconfigured `SwarmConfig.training_instances`) would filter out ALL instances, causing score 0.0. Added a guard in both `_step_with_task()` and `_step_with_data_node()` to skip filtering when the selector returns an empty list.

## Changes
- **factory/inner_loop.py**: Added empty-list guard in `_step_with_task()` (line ~471-479) and `_step_with_data_node()` (line ~655-664). If the subset selector returns an empty list, filtering is skipped (all instances allowed).
- **tests/test_outer_loop/test_holdout.py**: Added `test_empty_subset_selector_allows_all_instances` verifying that an empty `FixedSubsetSelector` results in `allowed_instance_ids=None` (no filtering).

## Test Results
- `pytest tests/test_outer_loop/test_holdout.py -v`: 40/40 passed
- `pytest tests/test_outer_loop/ -v --timeout=300`: 672/672 passed
- `ruff check`: All checks passed

## Status
✅ Complete — PR ready
