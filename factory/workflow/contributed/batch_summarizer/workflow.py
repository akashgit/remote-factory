"""batch-summarizer workflow — iterate over documents and summarize each via LLM.

5-node pipeline: scan_dir → data_loop[summarize → write_summary] → collect_index

Scans a source directory for document files, generates a JSONL manifest,
then uses a DataNode to iterate over each file, invoking an LLMNode subgraph
that reads and summarizes each document. Produces per-file Markdown summaries
and an aggregated SUMMARY_INDEX.md.

Invoke via: factory workflow run batch-summarizer /path/to/project
Configure source directory: BATCH_SUMMARIZER_SOURCE env var (default: {project_path}/docs)
Configure recursive scanning: BATCH_SUMMARIZER_RECURSIVE=true env var
"""

from typing import Any

from factory.workflow.llm_tools import FILE_READ_TOOL
from factory.workflow.primitives import (
    DataNode,
    Edge,
    FnNode,
    LLMNode,
    ProjectState,
    ToolDef,
    Workflow,
)

meta = {
    "name": "batch-summarizer",
    "description": (
        "Batch document summarization workflow — iterates over a directory of "
        "documents and produces per-file Markdown summaries plus an aggregated "
        "SUMMARY_INDEX.md using an LLM-powered DataNode subgraph."
    ),
}

FILE_WRITE_TOOL = ToolDef(
    name="file_write",
    description="Write content to a file. Creates parent directories if needed.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The file path to write to",
            },
            "content": {
                "type": "string",
                "description": "The content to write",
            },
        },
        "required": ["path", "content"],
    },
    executor="file_write",
)

_SYSTEM_PROMPT = (
    "You are a document summarizer. Your job is to read a single source document,\n"
    "extract its key information, and write a structured Markdown summary.\n"
    "\n"
    "Rules:\n"
    "1. Faithfulness — Every claim in your summary MUST be directly supported by\n"
    "   the source document. Never infer, speculate, or add information not present.\n"
    "2. Completeness — Capture all major points. Do not silently drop sections.\n"
    "3. Conciseness — Target 150–300 words for the summary body. Shorter documents\n"
    "   may produce shorter summaries (minimum 50 words). Longer documents should\n"
    "   stay under 300 words.\n"
    "4. Neutrality — Use third-person, neutral language. Do not editorialize.\n"
    "5. Structure — Always use the output format specified in the user message.\n"
    "6. Tool discipline — You have exactly two tools: file_read and file_write.\n"
    "   Call file_read first to retrieve the document, then file_write to emit the\n"
    "   summary. Do not call any other tool. Do not skip either step.\n"
    "7. Anti-hallucination — If the document is empty, unreadable, or not\n"
    "   text-based, write a summary that states this fact instead of fabricating\n"
    '   content. Use the exact sentinel: "⚠️ UNABLE TO SUMMARIZE: <reason>".\n'
    "8. One document per invocation — Summarize only the document identified in the\n"
    "   user message. Do not read or reference other files."
)

_INSTANCE_PROMPT = """\
## Document to Summarize

{instance_context}

## Instructions

Follow these steps exactly:

1. **Read the document.** Call `file_read` with the `abs_path` field from the
   JSON above.
   - If `file_read` returns an error or empty content, skip to step 3 and
     write an error summary.
2. **Analyze the content.** Identify:
   - The document's main topic or purpose
   - Key arguments, findings, data points, or decisions
   - Any conclusions or action items
3. **Write the summary.** Call `file_write` with:
   - `path`: `.factory/batch_summarizer/summaries/<id>.summary.md`
     (where `<id>` is the `id` field from the JSON above)
   - `content`: A Markdown summary following the Output Format below

## Output Format

```markdown
# Summary: <document filename>

**TL;DR:** <1–2 sentence executive summary>

## Key Points
- <bullet 1>
- <bullet 2>
- ...

## Key Data
<Only if the document contains quantitative data, dates, figures, or named
entities worth preserving. Omit this section entirely otherwise.>

| Item | Value |
|---|---|
| ... | ... |

## Source
- **Path:** <original file path>
- **Word count (source):** <approximate word count of the original>
```"""

_SCAN_DIR_COMMAND = (
    "mkdir -p {project_path}/.factory/batch_summarizer/summaries && "
    'SOURCE_DIR="${BATCH_SUMMARIZER_SOURCE:-{project_path}/docs}" && '
    'RECURSIVE="${BATCH_SUMMARIZER_RECURSIVE:-false}" && '
    "if [ ! -d \"$SOURCE_DIR\" ]; then "
    'echo "ERROR: Source directory not found: $SOURCE_DIR" >&2; exit 1; '
    "fi && "
    'cd "$SOURCE_DIR" && '
    "if [ \"$RECURSIVE\" = \"true\" ]; then "
    "FIND_CMD='find . -type f'; "
    "else "
    "FIND_CMD='find . -maxdepth 1 -type f'; "
    "fi && "
    "eval $FIND_CMD | "
    r"grep -iE '\.(md|txt|rst|html|py|js|ts|json|yaml|yml|toml|cfg|ini|csv|log|xml|sh|bash|rb|go|rs|java|c|cpp|h|hpp)$' | "
    "sort | "
    "while read -r f; do "
    'REL=$(echo "$f" | sed \'s|^\\./||\'); '
    'ABS="$SOURCE_DIR/$REL"; '
    'FNAME=$(basename "$REL"); '
    "printf '{\"id\":\"%s\",\"metadata\":{\"filename\":\"%s\",\"relpath\":\"%s\",\"abs_path\":\"%s\"}}\\n' "
    '"$FNAME" "$FNAME" "$REL" "$ABS"; '
    "done > {project_path}/.factory/batch_summarizer/manifest.jsonl && "
    "NFILES=$(wc -l < {project_path}/.factory/batch_summarizer/manifest.jsonl) && "
    'echo "Scanned $NFILES files from $SOURCE_DIR"'
)

_WRITE_SUMMARY_COMMAND = (
    "cd {project_path} && "
    "ITEM_JSON=$(cat .factory/current_item.json 2>/dev/null) && "
    "FILENAME=$(echo \"$ITEM_JSON\" | python3 -c "
    "\"import sys,json; print(json.load(sys.stdin)['metadata']['filename'])\") && "
    "SUMMARY=$(cat .factory/reviews/builder-latest.md 2>/dev/null || echo 'No summary generated') && "
    'if [ ! -f ".factory/batch_summarizer/summaries/${FILENAME}.summary.md" ]; then '
    "printf '# Summary: %s\\n\\n%s\\n' \"$FILENAME\" \"$SUMMARY\" "
    "> .factory/batch_summarizer/summaries/\"${FILENAME}.summary.md\"; "
    "fi && "
    'echo "Summary complete for $FILENAME"'
)

_COLLECT_INDEX_COMMAND = (
    "cd {project_path} && "
    "echo '# Document Summary Index' > .factory/batch_summarizer/SUMMARY_INDEX.md && "
    "echo '' >> .factory/batch_summarizer/SUMMARY_INDEX.md && "
    "echo 'Generated by batch-summarizer workflow.' >> .factory/batch_summarizer/SUMMARY_INDEX.md && "
    "echo '' >> .factory/batch_summarizer/SUMMARY_INDEX.md && "
    "TOTAL=0 && "
    "for f in .factory/batch_summarizer/summaries/*.summary.md; do "
    '[ -f "$f" ] || continue; '
    "echo '---' >> .factory/batch_summarizer/SUMMARY_INDEX.md && "
    'cat "$f" >> .factory/batch_summarizer/SUMMARY_INDEX.md && '
    "echo '' >> .factory/batch_summarizer/SUMMARY_INDEX.md && "
    "TOTAL=$((TOTAL + 1)); "
    "done && "
    'echo "Collected $TOTAL summaries into SUMMARY_INDEX.md"'
)


def workflow() -> Workflow:
    """Build the batch-summarizer workflow with 5 nodes and 3 edges."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: scan_dir (FnNode) ─────────────────────────────────
    nodes["scan_dir"] = FnNode(
        id="scan_dir",
        command=_SCAN_DIR_COMMAND,
        writes={".factory/batch_summarizer/manifest.jsonl"},
    )

    # ── Node 2: data_loop (DataNode) ──────────────────────────────
    nodes["data_loop"] = DataNode(
        id="data_loop",
        source_path=".factory/batch_summarizer/manifest.jsonl",
        source_format="jsonl",
        subgraph_entry="summarize",
        subgraph_exit="write_summary",
        parallelism=1,
        max_items=500,
        writes={
            ".factory/current_item.json",
            ".factory/batch_summarizer/summaries/",
        },
    )

    # ── Node 3: summarize (LLMNode) — subgraph entry ─────────────
    nodes["summarize"] = LLMNode(
        id="summarize",
        system_prompt=_SYSTEM_PROMPT,
        instance_prompt=_INSTANCE_PROMPT,
        model="sonnet",
        provider="auto",
        max_turns=5,
        max_tokens=4096,
        temperature=0.0,
        tool_choice="auto",
        tools=[FILE_READ_TOOL, FILE_WRITE_TOOL],
        timeout=120,
        reads={".factory/current_item.json"},
        writes={".factory/reviews/builder-latest.md"},
    )

    # ── Node 4: write_summary (FnNode) — subgraph exit ───────────
    nodes["write_summary"] = FnNode(
        id="write_summary",
        command=_WRITE_SUMMARY_COMMAND,
        reads={".factory/current_item.json", ".factory/reviews/builder-latest.md"},
        writes={".factory/batch_summarizer/summaries/"},
    )

    # ── Node 5: collect_index (FnNode) ────────────────────────────
    nodes["collect_index"] = FnNode(
        id="collect_index",
        command=_COLLECT_INDEX_COMMAND,
        reads={".factory/batch_summarizer/summaries/"},
        writes={".factory/batch_summarizer/SUMMARY_INDEX.md"},
    )

    # ── Edges (3 explicit) ────────────────────────────────────────
    edges = [
        Edge(source="scan_dir", target="data_loop"),
        Edge(source="summarize", target="write_summary"),
        Edge(source="data_loop", target="collect_index"),
    ]

    # ── Trigger ───────────────────────────────────────────────────
    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "batch-summarizer"

    return Workflow(
        name="batch-summarizer",
        nodes=nodes,
        edges=edges,
        start_node="scan_dir",
        terminal=True,
        trigger=trigger,
    )
