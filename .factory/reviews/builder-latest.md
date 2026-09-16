# Builder Review — batch-summarizer workflow

## Changes Made

### 1. Relaxed DataNode source validator (`factory/workflow/primitives.py`)
- Changed `_validate_source` from `sum(sources) != 1` to `sum(sources) > 1`
- Allows zero sources for late-bound DataNodes (source injected at runtime via `--data`)
- Kept source_path/source_format validation intact

### 2. Created workflow file (`.factory/workflows/batch_summarizer.py`)
- Project-local workflow discovered automatically by WorkflowRegistry
- DataNode `data` (start_node, no source, late-bound via `--data`)
- AgentNode `summarize` (RESEARCHER role, reads `current_item.json`, writes summaries)
- Zero edges (single-node subgraph, entry == exit)
- DataNode declares `writes={".factory/current_item.json"}` for data-dependency validation

### 3. Tests (`tests/test_batch_summarizer_workflow.py`)
- 14 tests across 7 test classes, all passing
- Covers: construction, graph validation, no double-execution, subgraph exit safety,
  serialization round-trip, registry discovery, and negative edge rejection

## Validation Results

- `factory workflow validate --file .factory/workflows/batch_summarizer.py` → VALID (2 nodes, 0 edges)
- `factory workflow export-skills --project-path .` → batch-summarizer SKILL.md exported
- All 14 new tests pass
- All 32 existing workflow primitives tests pass
- All 165 smoke tests pass
- ruff clean on all modified/created files
