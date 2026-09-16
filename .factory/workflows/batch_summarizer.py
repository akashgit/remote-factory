"""Batch Summarizer — project-local workflow for the Software Factory.

Iterates over a directory of documents, summarizes each with an LLM,
and produces an aggregated report.

Auto-discovered by WorkflowRegistry.discover() via .factory/workflows/.
"""

from factory.workflow.llm_tools import FILE_READ_TOOL
from factory.workflow.primitives import (
    DataNode,
    Edge,
    FnNode,
    LLMNode,
    ProjectState,
    Workflow,
)

meta = {
    "name": "batch-summarizer",
    "description": (
        "Iterate over a directory of documents, summarize each with an LLM, "
        "and produce an aggregated report."
    ),
}

# ---------------------------------------------------------------------------
# Node definitions
# ---------------------------------------------------------------------------

_discover_docs = FnNode(
    id="discover_docs",
    reads=set(),
    writes={".factory/batch-summarizer/manifest.jsonl"},
    command=(
        'cd {project_path} && '
        'mkdir -p .factory/batch-summarizer && '
        'INPUT_DIR=${BATCH_SUMMARIZER_INPUT_DIR:-.factory/batch-summarizer/input} && '
        'if [ ! -d "$INPUT_DIR" ]; then '
        '  echo "ERROR: Input directory $INPUT_DIR does not exist" >&2; exit 1; '
        'fi && '
        '> .factory/batch-summarizer/manifest.jsonl && '
        'find "$INPUT_DIR" -maxdepth 1 -type f '
        r"  \( -name '*.txt' -o -name '*.md' -o -name '*.rst' -o -name '*.html' \) "
        '  -print0 | sort -z | while IFS= read -r -d \'\' f; do '
        '  FNAME=$(basename "$f") && '
        '  ABSPATH=$(cd "$(dirname "$f")" && pwd)/"$FNAME" && '
        '  printf \'{"id":"%s","path":"%s"}\\n\' "$FNAME" "$ABSPATH" '
        '    >> .factory/batch-summarizer/manifest.jsonl; '
        'done && '
        'ITEM_COUNT=$(wc -l < .factory/batch-summarizer/manifest.jsonl) && '
        'echo "Discovered $ITEM_COUNT documents"'
    ),
)

_iterate_docs = DataNode(
    id="iterate_docs",
    reads={".factory/batch-summarizer/manifest.jsonl"},
    writes={".factory/batch-summarizer/summaries/", ".factory/current_item.json"},
    source_path=".factory/batch-summarizer/manifest.jsonl",
    source_format="jsonl",
    subgraph_entry="summarize_doc",
    subgraph_exit="save_summary",
    parallelism=1,
    max_items=500,
    inline_items=[],
)

_summarize_doc = LLMNode(
    id="summarize_doc",
    reads={".factory/current_item.json"},
    writes={".factory/batch-summarizer/current_summary.md"},
    model="sonnet",
    provider="auto",
    max_tokens=4096,
    max_turns=5,
    temperature=0.0,
    tools=[FILE_READ_TOOL],
    tool_choice="auto",
    timeout=120,
    system_prompt=(
        "You are a precise document summarizer. You produce clear, concise "
        "summaries that capture the key points, arguments, and conclusions "
        "of a document. Your summaries are factual and do not add "
        "interpretation beyond what the document states."
    ),
    instance_prompt=(
        "## Task\n\n"
        "You are summarizing one document from a batch. The document metadata\n"
        "is provided below as JSON from `.factory/current_item.json`.\n\n"
        "{instance_context}\n\n"
        "## Instructions\n\n"
        "1. Parse the JSON above. The document file path is in the "
        "`metadata.path` field.\n"
        "2. Use the `file_read` tool to read the document at that path.\n"
        "3. Produce a summary of the document with the following structure:\n\n"
        "   # Summary: <document filename>\n\n"
        "   ## Key Points\n"
        "   - <bullet points of main ideas>\n\n"
        "   ## Details\n"
        "   <2-3 paragraph summary of the document's content>\n\n"
        "4. Output ONLY the summary in the format above. Do not include any\n"
        "other text, preamble, or explanation."
    ),
)

_save_summary = FnNode(
    id="save_summary",
    reads={".factory/current_item.json", ".factory/batch-summarizer/current_summary.md"},
    writes={".factory/batch-summarizer/summaries/"},
    command=(
        "cd {project_path} && "
        "mkdir -p .factory/batch-summarizer/summaries && "
        'ITEM_ID=$(python3 -c "\n'
        "import json\n"
        "d = json.load(open('.factory/current_item.json'))\n"
        "meta = d.get('metadata', {})\n"
        "name = meta.get('id', d.get('id', 'unknown'))\n"
        "print(name.replace('/', '_').rsplit('.', 1)[0])\n"
        '") && '
        "cp .factory/batch-summarizer/current_summary.md "
        '   .factory/batch-summarizer/summaries/${ITEM_ID}.summary.md && '
        'echo "Saved summary for $ITEM_ID"'
    ),
)

_aggregate_summaries = FnNode(
    id="aggregate_summaries",
    reads={".factory/batch-summarizer/summaries/"},
    writes={".factory/batch-summarizer/report.md"},
    command=(
        "cd {project_path} && "
        "OUTPUT=.factory/batch-summarizer/report.md && "
        "echo '# Batch Summary Report' > \"$OUTPUT\" && "
        "echo '' >> \"$OUTPUT\" && "
        "echo \"Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)\" >> \"$OUTPUT\" && "
        "echo '' >> \"$OUTPUT\" && "
        "SUMMARY_DIR=.factory/batch-summarizer/summaries && "
        'COUNT=$(ls -1 "$SUMMARY_DIR"/*.summary.md 2>/dev/null | wc -l) && '
        "echo \"Documents summarized: $COUNT\" >> \"$OUTPUT\" && "
        "echo '' >> \"$OUTPUT\" && "
        "echo '---' >> \"$OUTPUT\" && "
        "echo '' >> \"$OUTPUT\" && "
        'for f in $(ls -1 "$SUMMARY_DIR"/*.summary.md 2>/dev/null | sort); do '
        "  cat \"$f\" >> \"$OUTPUT\" && "
        "  echo '' >> \"$OUTPUT\" && "
        "  echo '---' >> \"$OUTPUT\" && "
        "  echo '' >> \"$OUTPUT\"; "
        "done && "
        "echo \"Aggregated $COUNT summaries into $OUTPUT\""
    ),
)


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------

def _trigger(_state: ProjectState, ctx: dict) -> bool:  # noqa: ANN401
    """Activate when mode is 'batch-summarizer'."""
    return ctx.get("mode") == "batch-summarizer"


# ---------------------------------------------------------------------------
# Workflow factory
# ---------------------------------------------------------------------------

def workflow() -> Workflow:
    """Return the batch-summarizer workflow graph."""
    nodes = {
        n.id: n
        for n in [
            _discover_docs,
            _iterate_docs,
            _summarize_doc,
            _save_summary,
            _aggregate_summaries,
        ]
    }

    edges = [
        Edge(source="discover_docs", target="iterate_docs"),
        Edge(source="summarize_doc", target="save_summary"),
        Edge(source="iterate_docs", target="aggregate_summaries"),
    ]

    return Workflow(
        name="batch-summarizer",
        nodes=nodes,
        edges=edges,
        start_node="discover_docs",
        terminal=True,
        trigger=_trigger,
    )
