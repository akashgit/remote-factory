# batch-summarizer — Batch Document Summarization Workflow

A contributed workflow that iterates over a directory of documents and
summarizes each one using an LLM. Produces per-file Markdown summaries and an
aggregated summary index.

## Usage

```bash
factory workflow run batch-summarizer /path/to/project
```

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `BATCH_SUMMARIZER_SOURCE` | `{project_path}/docs` | Directory containing documents to summarize |
| `BATCH_SUMMARIZER_RECURSIVE` | `false` | Set to `true` to scan subdirectories recursively |

## Graph Topology

```
scan_dir → data_loop[summarize → write_summary] → collect_index
```

- **scan_dir** (FnNode) — Scans the source directory, generates a JSONL manifest
- **data_loop** (DataNode) — Iterates over the manifest, running the subgraph per document
- **summarize** (LLMNode) — Reads a document via `file_read`, writes a summary via `file_write`
- **write_summary** (FnNode) — Fallback summary persistence (belt-and-suspenders)
- **collect_index** (FnNode) — Aggregates all summaries into `SUMMARY_INDEX.md`

## Output

```
{project_path}/.factory/batch_summarizer/
├── manifest.jsonl                    # JSONL listing of scanned files
├── summaries/
│   ├── README.md.summary.md          # Per-file summaries
│   ├── CONTRIBUTING.md.summary.md
│   └── ...
└── SUMMARY_INDEX.md                  # Aggregated index of all summaries
```

## Supported File Extensions

`.md`, `.txt`, `.rst`, `.html`, `.py`, `.js`, `.ts`, `.json`, `.yaml`, `.yml`,
`.toml`, `.cfg`, `.ini`, `.csv`, `.log`, `.xml`, `.sh`, `.bash`, `.rb`, `.go`,
`.rs`, `.java`, `.c`, `.cpp`, `.h`, `.hpp`
