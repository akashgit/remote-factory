# Batch Summarizer Workflow

Iterates over a directory of text files, summarizes each using an LLM (haiku),
and produces an indexed collection of markdown summaries.

## Graph

```
scan_files (FnNode)
    │
    ▼
iterate_docs (DataNode, source_format='jsonl')
    │                          │
    │  ┌───── subgraph ───────┐│
    │  │                      ││
    │  │  summarize (LLMNode) ││
    │  │                      ││
    │  └──────────────────────┘│
    │                          │
    ▼
finalize (FnNode)
```

**4 nodes, 2 explicit edges, 1 implicit subgraph invocation.**

## Usage

```bash
# Summarize files in the default input directory (.factory/input)
factory workflow run batch-summarizer /path/to/project

# Summarize files from a custom directory
BATCH_INPUT_DIR=docs/ factory workflow run batch-summarizer /path/to/project

# Write summaries to a custom output directory
BATCH_OUTPUT_DIR=out/summaries factory workflow run batch-summarizer /path/to/project
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BATCH_INPUT_DIR` | `.factory/input` | Directory to scan for files |
| `BATCH_OUTPUT_DIR` | `summaries` | Directory for summary output |

## Supported File Types

The scanner includes files with these extensions (max 1 MB each):

`.txt`, `.md`, `.rst`, `.py`, `.js`, `.ts`, `.java`, `.go`, `.rs`,
`.html`, `.xml`, `.json`, `.yaml`, `.yml`, `.toml`, `.cfg`, `.ini`,
`.csv`, `.log`, `.tex`, `.org`

## Output

- `summaries/{id}_summary.md` — one summary per input file
- `summaries/INDEX.md` — index with document table and statistics

## Defaults

| Setting | Value |
|---------|-------|
| Model | `haiku` |
| Parallelism | `1` (sequential) |
| Max items | `500` |
| Max file size | 1 MB |
| Temperature | `0.0` |
| Per-file timeout | `120s` |

## Limitations

- Text files only (no PDF, binary, or image support)
- Top-level directory only (`-maxdepth 1`, no recursive traversal)
- No resume/checkpoint — restarts process all items
- Fixed summary format (no custom templates)
- Sequential processing (parallelism=1)
