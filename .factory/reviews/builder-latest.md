## Builder Review — Issue #1503

### Summary
Implemented DataNode subgraph injection in `_inject_frozen_nodes()` so that frozen DataNodes bring their complete subgraph (nodes + edges) into designer variants.

### Files Modified
1. **factory/outer_loop/designer.py** (+39 lines)
   - Added import of `_collect_subgraph_nodes` from `factory.workflow.executor`
   - Extended `_inject_frozen_nodes()` to accept `edges` parameter and inject subgraph nodes/edges for frozen DataNodes
   - Updated all 3 call sites (`design_minimal`, `design_thorough`, `design_custom`) to pass edges
   - Fixed `_rewire_data_nodes()` to exclude subgraph nodes from terminal candidate selection (interaction fix)

2. **tests/test_outer_loop/test_designer.py** (+100 lines)
   - Added 5 new tests in `TestInjectFrozenDataNodeSubgraph` class
   - Updated 2 pre-existing validation tests to filter unreachable-node warnings from dead subgraph nodes

### Test Results
- 43/43 tests pass in test_designer.py
- 68/68 tests pass across outer loop test suite
- 165/165 smoke tests pass
- Lint: all checks passed

### Design Decisions
- Edge dedup uses `(source, target, condition)` signature set built before injection loop
- Node dedup checks `if sg_id not in nodes` to handle overlap between frozen_node_ids and subgraph
- Nested DataNodes are NOT recursively expanded (documented limitation)
- `_rewire_data_nodes` terminal candidate fix was necessary: injected subgraph nodes would otherwise corrupt terminal selection, causing non-deterministic rewiring failures
