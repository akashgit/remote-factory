"""Batch document summarizer workflow — iterates over files and produces summaries.

4-node pipeline: scan_files (FnNode) → iterate_docs (DataNode) → finalize (FnNode)
with a single-node subgraph: summarize (LLMNode) dispatched per-item by DataNode.

Uses the FnNode→JSONL→DataNode pattern for per-file iteration.
"""

from __future__ import annotations

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

# FILE_WRITE_TOOL is NOT exported by llm_tools.py — define locally.
FILE_WRITE_TOOL = ToolDef(
    name="file_write",
    description="Write content to a file. Creates parent directories if needed.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to write to"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    },
    executor="file_write",
)

meta = {
    "name": "batch-summarizer",
    "description": (
        "Batch document summarizer — iterates over a directory of files, "
        "summarizes each using LLM (haiku), and produces an indexed collection "
        "of markdown summaries. scan_files → iterate_docs (DataNode) → finalize."
    ),
}


def workflow() -> Workflow:
    """Build the batch-summarizer workflow."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: scan_files (FnNode) ───────────────────────────────
    nodes["scan_files"] = FnNode(
        id="scan_files",
        command=(
            'cd {project_path} && '
            'INPUT_DIR="${BATCH_INPUT_DIR:-.factory/input}" && '
            'OUTPUT_DIR="${BATCH_OUTPUT_DIR:-summaries}" && '
            'mkdir -p "$OUTPUT_DIR" .factory && '
            '> .factory/manifest.jsonl && '
            'if [ ! -d "$INPUT_DIR" ]; then '
            '  echo \'{"error": "Input directory not found: \'"$INPUT_DIR"\'"}\'  '
            '> .factory/manifest.jsonl; '
            '  exit 0; '
            'fi && '
            'find "$INPUT_DIR" -maxdepth 1 -type f '
            '  \\( -name \'*.txt\' -o -name \'*.md\' -o -name \'*.rst\' '
            '     -o -name \'*.py\' -o -name \'*.js\' -o -name \'*.ts\' '
            '     -o -name \'*.java\' -o -name \'*.go\' -o -name \'*.rs\' '
            '     -o -name \'*.html\' -o -name \'*.xml\' -o -name \'*.json\' '
            '     -o -name \'*.yaml\' -o -name \'*.yml\' -o -name \'*.toml\' '
            '     -o -name \'*.cfg\' -o -name \'*.ini\' -o -name \'*.csv\' '
            '     -o -name \'*.log\' -o -name \'*.tex\' -o -name \'*.org\' \\) '
            '  -readable '
            '  -print0 | sort -z | while IFS= read -r -d \'\' f; do '
            '  BASENAME=$(basename "$f") && '
            '  ITEM_ID=$(echo "$BASENAME" | sed \'s/[^a-zA-Z0-9._-]/_/g\') && '
            '  SIZE=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null '
            '|| echo 0) && '
            '  if [ "$SIZE" -gt 0 ] 2>/dev/null && '
            '[ "$SIZE" -le 1048576 ] 2>/dev/null; then '
            '    printf \'{"id":"%s","path":"%s","filename":"%s","size":%s}\\n\' '
            '      "$ITEM_ID" "$f" "$BASENAME" "$SIZE" >> .factory/manifest.jsonl; '
            '  fi; '
            'done && '
            'ITEM_COUNT=$(wc -l < .factory/manifest.jsonl | tr -d \' \') && '
            'echo "Scanned $ITEM_COUNT files into manifest"'
        ),
        writes={".factory/manifest.jsonl"},
    )

    # ── Node 2: iterate_docs (DataNode) ───────────────────────────
    nodes["iterate_docs"] = DataNode(
        id="iterate_docs",
        source_path=".factory/manifest.jsonl",
        source_format="jsonl",
        subgraph_entry="summarize",
        subgraph_exit="summarize",
        parallelism=1,
        max_items=500,
        # DataNode executor writes current_item.json implicitly per iteration
        writes={".factory/current_item.json"},
    )

    # ── Node 3: summarize (LLMNode) — subgraph of iterate_docs ───
    nodes["summarize"] = LLMNode(
        id="summarize",
        model="haiku",
        provider="auto",
        tools=[FILE_READ_TOOL, FILE_WRITE_TOOL],
        max_turns=10,
        max_tokens=4096,
        temperature=0.0,
        timeout=120,
        reads={".factory/current_item.json"},
        system_prompt=(
            "You are a document summarizer. You produce clear, concise summaries "
            "that capture the key points, structure, and purpose of a document.\n\n"
            "## Rules\n"
            "1. Read .factory/current_item.json to discover which file to summarize.\n"
            "   The JSON contains an 'id' field and a 'metadata' object with 'path' "
            "and 'filename' keys.\n"
            "2. Read the document at the path specified in metadata.path.\n"
            "3. Write a markdown summary to summaries/{id}_summary.md where {id} is "
            "from current_item.json.\n"
            "4. The summary MUST include:\n"
            "   - A title line: '# Summary: {filename}'\n"
            "   - A 1-2 sentence overview\n"
            "   - Key points as a bullet list\n"
            "   - Document type/purpose classification (e.g., 'Configuration', "
            "'Source code', 'Documentation')\n"
            "   - Approximate word count of the original\n"
            "5. Keep summaries concise — aim for 100-300 words.\n"
            "6. If the file cannot be read or is empty, write a summary noting this.\n"
            "7. Do NOT modify the original document."
        ),
        instance_prompt=(
            "Summarize the document described in .factory/current_item.json.\n\n"
            "Current item metadata:\n"
            "{instance_context}\n\n"
            "Steps:\n"
            "1. Read .factory/current_item.json to get the file path\n"
            "2. Read the actual document file\n"
            "3. Write the summary to the summaries/ directory"
        ),
    )

    # ── Node 4: finalize (FnNode) ─────────────────────────────────
    nodes["finalize"] = FnNode(
        id="finalize",
        command=(
            'cd {project_path} && '
            'OUTPUT_DIR="${BATCH_OUTPUT_DIR:-summaries}" && '
            'echo \'# Batch Summary Index\' > "$OUTPUT_DIR/INDEX.md" && '
            'echo \'\' >> "$OUTPUT_DIR/INDEX.md" && '
            'echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)" '
            '>> "$OUTPUT_DIR/INDEX.md" && '
            'echo \'\' >> "$OUTPUT_DIR/INDEX.md" && '
            'TOTAL=0 && SUCCESS=0 && '
            'if [ -f .factory/manifest.jsonl ]; then '
            '  TOTAL=$(wc -l < .factory/manifest.jsonl | tr -d \' \'); '
            'fi && '
            'echo \'## Documents\' >> "$OUTPUT_DIR/INDEX.md" && '
            'echo \'\' >> "$OUTPUT_DIR/INDEX.md" && '
            'echo \'| # | File | Summary | Size |\' >> "$OUTPUT_DIR/INDEX.md" && '
            'echo \'|---|------|---------|------|\' >> "$OUTPUT_DIR/INDEX.md" && '
            'IDX=1 && '
            'for f in "$OUTPUT_DIR"/*_summary.md; do '
            '  if [ -f "$f" ]; then '
            '    FNAME=$(basename "$f" _summary.md) && '
            '    FSIZE=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null '
            '|| echo \'?\') && '
            '    echo "| $IDX | $FNAME | [summary]($(basename $f)) | ${FSIZE}B |" '
            '>> "$OUTPUT_DIR/INDEX.md" && '
            '    IDX=$((IDX + 1)) && '
            '    SUCCESS=$((SUCCESS + 1)); '
            '  fi; '
            'done && '
            'echo \'\' >> "$OUTPUT_DIR/INDEX.md" && '
            'echo "## Statistics" >> "$OUTPUT_DIR/INDEX.md" && '
            'echo \'\' >> "$OUTPUT_DIR/INDEX.md" && '
            'echo "- **Total documents scanned:** $TOTAL" '
            '>> "$OUTPUT_DIR/INDEX.md" && '
            'echo "- **Summaries generated:** $SUCCESS" '
            '>> "$OUTPUT_DIR/INDEX.md" && '
            'if [ "$TOTAL" -gt 0 ] 2>/dev/null; then '
            '  PCT=$((SUCCESS * 100 / TOTAL)) && '
            '  echo "- **Success rate:** ${PCT}%" >> "$OUTPUT_DIR/INDEX.md"; '
            'fi && '
            'echo "Finalized: $SUCCESS summaries indexed in $OUTPUT_DIR/INDEX.md"'
        ),
        reads={".factory/manifest.jsonl"},
        writes={"summaries/INDEX.md"},
    )

    # ── Edges ─────────────────────────────────────────────────────
    # Only 2 explicit edges. NO edge from iterate_docs → summarize
    # (DataNode handles subgraph dispatch internally).
    edges = [
        Edge(source="scan_files", target="iterate_docs"),
        Edge(source="iterate_docs", target="finalize"),
    ]

    # ── Trigger ───────────────────────────────────────────────────
    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "batch-summarizer"

    return Workflow(
        name="batch-summarizer",
        nodes=nodes,
        edges=edges,
        start_node="scan_files",
        trigger=trigger,
        terminal=True,
    )
