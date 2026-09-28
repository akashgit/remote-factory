## Builder Review: DataNode Error Propagation Fix

**Status:** ✅ Complete — fix implemented, tests passing

### Changes Made

#### 1. `factory/workflow/executor.py` — Error propagation in `_execute_data()`

After `asyncio.gather(*tasks)` and worktree cleanup, added failure propagation logic:

- **All items failed:** Sets `self.result.halted = True`, stores halt_reason describing the failure with a sample error, emits `node.failed` event, stores item results for debugging, and returns without following edges.
- **Partial failure:** Logs a warning but continues (partial success is valid for data processing).
- **No failures:** Normal path — emits `node.completed` and follows edges.

The fix is placed AFTER worktree cleanup (so cleanup always runs) but BEFORE node completion emission and edge following.

#### 2. `tests/test_regression_bugs.py` — New `TestDataNodeSwallowsSubgraphFailures` class

Two tests:
- `test_all_items_failed_halts_datanode`: Constructs a DataNode with a 2-node subgraph (researcher → builder) where `FakeAgent(violate_writes=True)` on researcher causes builder to fail on reads. Verifies top-level `result.success=False`, `result.halted=True`, halt_reason mentions "all items failed", and results are still stored for debugging.
- `test_partial_failure_continues`: Verifies that when only 1 of 2 items fails, the workflow continues successfully.

Both tests use `patch.object(WorkflowExecutor, '_wait_for_reads', fast_wait)` to reduce the 60s read-wait timeout to 0.5s for test speed.

#### 3. `tests/test_data_node.py` — Updated existing test assertion

`test_setup_read_path_mismatch_logs_warning` had 1 item that failed on reads. Previously it asserted `result.success=True` (the old bug where DataNode swallowed failures). Updated to assert `result.success=False`, `result.halted=True` to match the corrected behavior.

### Test Results

- `tests/test_regression_bugs.py`: 12/12 passed ✅
- `tests/test_workflow_smoke.py`: 68/68 passed ✅
- `tests/test_data_node.py`: 81/81 passed ✅
- Full test suite (excluding pre-existing k8s failures): 2170+ passed ✅
