# Builder Review — batch-doc-scorer workflow

**Date:** 2026-10-01
**Issue:** #1547 — feat: batch-document-scorer portable workflow
**Branch:** factory/run-a6f89662

## Summary

Implemented the `batch-doc-scorer` portable workflow as specified in `.factory/strategy/current.md`. The workflow is a 4-node linear pipeline (3 AgentNodes + 1 GateNode) that scores markdown files for grammar and readability.

## Files Created

- `.factory/workflows/batch_doc_scorer.py` — 330-line workflow definition with meta dict, workflow() function, 4 nodes, 5 edges, trigger function
- `tests/test_workflow_batch_doc_scorer.py` — 12 tests covering graph validation, meta completeness, trigger function, node connectivity, start node, edge validity, gate RELOOP, post-checks, data lineage, terminal flag, name consistency, and import constraints

## Validation

- `factory workflow validate --file .factory/workflows/batch_doc_scorer.py` → VALID (4 nodes, 5 edges)
- `factory workflow export-skills` → Generated `workflow-batch-doc-scorer/SKILL.md` (253 lines)
- `ruff check` → All checks passed
- `pytest tests/test_workflow_batch_doc_scorer.py -v` → 12/12 passed

## Architecture

```
collect_files → score_documents → generate_report → gate_report
                                        ↑                |
                                        └── RELOOP ──────┘
                                        └── HALT ────────┘
```

- **collect_files** (RESEARCHER/haiku/120s): Find and validate .md files, write manifest
- **score_documents** (RESEARCHER/sonnet/900s): Score grammar + readability per file
- **generate_report** (RESEARCHER/sonnet/600s): Aggregate statistics and recommendations
- **gate_report** (CEO agent gate, max 2 iterations): Quality check on 5 criteria

## Notes

- Copied code EXACTLY from Section 1 of the spec — no improvisation
- Test 9 (data lineage) needed cycle-safe predecessor traversal due to RELOOP/HALT edges
- Registry auto-discovers the workflow from `.factory/workflows/` when `project_path` is provided
- No modifications to `definitions.py`, `register_all()`, or CLI wiring
