# Builder Review — DataNode train/val split bypass fix

## Issue
PR #1541: DataNode workflows bypass the train/val split firewall.

## Root Cause
`InnerLoop._step_with_data_node()` ignores `_subset_selector` and `WorkflowExecutor._execute_data()` calls `task.instances()` with no split parameter, bypassing the train/val firewall set by `SwarmEngine.evolve_generation()`.

## Changes Made

### 1. `factory/workflow/executor.py`
- Added `allowed_instance_ids: set[str] | None = None` keyword parameter to `WorkflowExecutor.__init__()`
- Stored as `self._allowed_instance_ids`
- In `_execute_data()`, added filtering of `task_instances` by `allowed_instance_ids` BEFORE the existing `node.split` filter — this ensures the SwarmEngine's authoritative instance list takes precedence

### 2. `factory/inner_loop.py`
- In `_step_with_data_node()` (executor path, ~line 649), extract allowed instance IDs from `_subset_selector` when present
- Pass `allowed_instance_ids` to `WorkflowExecutor` constructor

### 3. `tests/test_outer_loop/test_holdout.py`
- Added `TestDataNodeRespectsSubsetSelector` test class with 5 tests:
  - `test_allowed_instance_ids_passed_to_executor`: WorkflowExecutor accepts the parameter
  - `test_allowed_instance_ids_default_none`: Default is None (backward compat)
  - `test_executor_filters_task_instances_by_allowed_ids`: DataNode inline items are filtered
  - `test_executor_no_filter_when_allowed_ids_none`: No filter when None
  - `test_inner_loop_passes_subset_selector_to_executor`: InnerLoop correctly extracts and passes IDs

## Backward Compatibility
- `allowed_instance_ids` defaults to `None` — all existing callers are unaffected
- Verified 5 callers in `factory/` and 50+ callers in `tests/` — none pass this parameter

## Test Results
- `pytest tests/test_outer_loop/test_holdout.py -v`: 39/39 passed
- `ruff check` on changed files: All checks passed
