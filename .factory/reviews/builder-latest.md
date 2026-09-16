# Builder Review: batch-summarizer workflow

## Summary
Implemented the batch-summarizer contributed workflow per the approved spec at `.factory/strategy/current.md`.

## Files Created
- `factory/workflow/contributed/batch_summarizer/__init__.py` — Re-exports `meta` and `workflow`
- `factory/workflow/contributed/batch_summarizer/workflow.py` — 5-node graph (scan_dir → data_loop[summarize → write_summary] → collect_index) with 3 explicit edges, trigger, FILE_READ_TOOL import, inline FILE_WRITE_TOOL ToolDef
- `factory/workflow/contributed/batch_summarizer/README.md` — Usage documentation
- `factory/workflow/contributed/batch_summarizer/test_workflow.py` — 29 structural tests across 7 test classes

## Files Modified
- `factory/workflow/definitions.py` — Added `"batch-summarizer"` to `_get_builtin_registry()` using lazy import lambda pattern

## Validation Results
- All 29 tests pass (`pytest factory/workflow/contributed/batch_summarizer/test_workflow.py -v`)
- Graph validates via `factory workflow validate --file factory/workflow/contributed/batch_summarizer/workflow.py` → VALID (5 nodes, 3 edges)
- `ruff check` passes with no issues

## Notes
- Added `.factory/current_item.json` and `.factory/batch_summarizer/summaries/` to DataNode's `writes` set to satisfy the graph validator's data dependency check (the DataNode executor writes `current_item.json` implicitly, and the subgraph writes to `summaries/`)
- `factory workflow validate batch-summarizer` (name-based) fails for ALL contributed workflows (including pre-existing ones like salitrap), not specific to this change. The `--file` flag works correctly.
