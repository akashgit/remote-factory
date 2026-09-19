# Builder Review — DataNode always uses executor

## Summary

Restored the unconditional DataNode-always-uses-executor behavior. The CEO subprocess
cannot reliably follow multi-step iteration loops from SKILL.md prose — this is a known
LLM reliability limitation, not a bug in the execution strategy dispatch.

## Changes

### factory/inner_loop.py
- **Line ~427**: Removed `and self.execution_strategy == 'executor'` condition from the
  DataNode check in `_step_with_task()`. DataNode workflows now unconditionally route to
  `_step_with_data_node()` regardless of `execution_strategy`.
- **`_step_with_data_node()` docstring**: Updated to explain *why* DataNode always uses
  WorkflowExecutor — documents the LLM reliability limitation.
- No warning log existed to remove (the code hadn't added one yet).

### tests/test_inner_loop_dispatch.py
- `test_datanode_ceo_skill_uses_subprocess` → renamed to `test_datanode_ceo_skill_uses_executor`,
  now asserts DataNode + ceo-skill routes to `_step_with_data_node` (not `_run_ceo_subprocess`).
- `test_datanode_ceo_tool_uses_subprocess` → renamed to `test_datanode_ceo_tool_uses_executor`,
  now asserts DataNode + ceo-tool routes to `_step_with_data_node`.
- `test_datanode_executor_uses_step_with_data_node` — kept unchanged, still passes.

## Verification

- All 15 tests in `test_inner_loop_dispatch.py` pass
- `ruff check` clean
- `mypy` clean
