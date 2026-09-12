## Builder Report

- **Branch:** factory/run-e3ddbac6
- **Commit:** b364c09f
- **Status:** ✅ COMPLETE — 38/38 tests pass, lint clean

### Changes

**factory/outer_loop/designer.py** — `_rewire_data_nodes()`:
- **Bug 1 (Critical):** Removed `edges.insert(0, Edge(source=data_id, target=original_start))`. The workflow validator (`_validate_datanode_edges`) rejects explicit edges from a DataNode to its subgraph nodes — the executor handles subgraph execution internally via `DataNode.subgraph_entry`, so explicit edges cause double-execution.
- **Bug 2 (Medium):** Added guard for `data_id == original_start` collision. When the DataNode ID matches the template's start node, follows edges from `original_start` to find the actual first template node (avoiding self-referential `subgraph_entry`). Also removes stale template edges from `original_start` that would become invalid DataNode-to-subgraph edges.

**tests/test_outer_loop/test_designer.py**:
- Updated 3 existing tests to assert edges do NOT exist (previously asserted the buggy behavior)
- Added `test_rewired_workflow_validates_graph` — calls `wf.validate_graph()` on a rewired workflow, asserts no issues
- Added `test_data_node_id_collision_with_start` — creates a seed where DataNode ID == 'researcher' (same as minimal template start), verifies no self-referential cycle and no structural validation issues

### Test Results
- 38/38 tests pass (36 existing + 2 new)
- Lint: clean
- Mypy: 6 pre-existing errors (dict invariance), no new errors
