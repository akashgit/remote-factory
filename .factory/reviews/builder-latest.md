# Builder Report

## Issue
Fix false positive in `_validate_datanode_exit` — terminal GateNodes (no RELOOP edges) are valid as `subgraph_exit`.

## Changes
- **factory/workflow/validation.py**: Updated `_validate_datanode_exit` to check for outgoing RELOOP edges before warning about a GateNode used as `subgraph_exit`. Terminal GateNodes without RELOOP edges are now allowed.

## Verification
- `tests/test_outer_loop/test_designer.py`: 38 passed
- `tests/test_data_node.py`: 67 passed
- Total: 105 passed, 0 failed

## Commit
`fix: refine DataNode exit validation to only warn for Loop GateNodes`
