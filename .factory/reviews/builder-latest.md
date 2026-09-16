# Builder Review — batch-summarizer workflow

## Summary

Implemented the `batch-summarizer` project-local workflow as specified in `.factory/strategy/current.md`.

## Files Created

| File | Purpose |
|------|---------|
| `.factory/workflows/batch_summarizer.py` | Project-local workflow definition |
| `tests/test_batch_summarizer_workflow.py` | 10 test cases covering graph validation, structure, and SKILL.md export |
| `skills/workflow-batch-summarizer/SKILL.md` | Auto-generated skill file for CEO agent consumption |

## Implementation Details

- **DataNode (`document_loader`)**: Created via `DataNode.model_construct()` to bypass Pydantic's `_validate_source` check — the data source is late-bound via `--data <path>` CLI flag at runtime
- **Workflow**: Also created via `Workflow.model_construct()` because Pydantic's strict union validation on the `nodes` dict re-validates the DataNode through the discriminated union, re-triggering `_validate_source`
- **AgentNode (`summarizer`)**: BUILDER role with prompt template for document summarization, 300s timeout
- **Zero edges**: DataNode dispatches to its subgraph internally; single-node subgraph needs no internal edges
- **Meta dict**: `name="batch-summarizer"`, description matches spec

## Validation

- `factory workflow validate batch-summarizer --file .factory/workflows/batch_summarizer.py` → VALID (2 nodes, 0 edges)
- `factory workflow export-skills --output-dir skills --project-path .` → generated `skills/workflow-batch-summarizer/SKILL.md`
- `pytest tests/test_batch_summarizer_workflow.py -v` → 10/10 passed
- `ruff check` → clean

## Test Coverage

1. `test_graph_validates` — validate_graph() returns no issues
2. `test_start_node_is_data_node` — start_node is DataNode 'document_loader'
3. `test_subgraph_entry_exit` — subgraph_entry/exit both point to 'summarizer'
4. `test_data_source_is_late_bound` — no source_path, task_ref, or inline_items
5. `test_no_edges_from_datanode_to_subgraph` — no explicit edges from DataNode to subgraph
6. `test_meta_dict` — meta dict well-formed with name and description
7. `test_summarizer_is_builder_agent` — AgentNode with BUILDER role
8. `test_skill_md_generation` — workflow_to_skill_md() produces valid output
9. `test_workflow_name_matches_meta` — Workflow.name matches meta['name']
10. `test_node_count` — exactly 2 nodes: document_loader + summarizer
