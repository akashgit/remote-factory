"""batch-summarizer — iterate over documents in a directory and summarize each one.

Minimal DataNode workflow:
  DataNode (start_node, id='data', no source — late-bound via --data <path>)
    └── subgraph: AgentNode (id='summarize', RESEARCHER)

The DataNode source is resolved at runtime from the --data CLI argument.
The executor writes .factory/current_item.json per item; the agent reads it
to discover the document path, then writes a summary to
.factory/summaries/{item_id}_summary.md.

No edges are needed — the subgraph is a single node (entry == exit).
"""

from __future__ import annotations

from typing import Any

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    DataNode,
    Edge,
    Workflow,
)

meta: dict[str, str] = {
    "name": "batch-summarizer",
    "description": (
        "Batch document summarizer — iterates over a directory of documents "
        "and generates a structured summary for each one. Uses a DataNode "
        "with late-bound source (pass --data <directory_path> at runtime). "
        "Each document is summarized by a RESEARCHER agent that reads "
        ".factory/current_item.json for the document path."
    ),
}

_SUMMARIZE_PROMPT = """\
You are summarizing a single document from a batch processing pipeline.

## Instructions

1. Read `.factory/current_item.json` to discover this item's details.
   The file has this structure:
   ```json
   {"id": "...", "path": "...", "metadata": {}, "prompt": ""}
   ```

2. Read the document at the path specified in the `path` field.

3. Generate a structured summary with these sections:
   - **Title**: The document filename or title
   - **Overview**: 1–2 sentence summary of the document's purpose
   - **Key Points**: 3–5 bullet points capturing the main ideas
   - **Notable Details**: Any important specifics, numbers, or conclusions
   - **Word Count**: Approximate word count of the original document

4. Read the item `id` from `.factory/current_item.json` and write your
   summary to `.factory/summaries/{id}_summary.md`.

5. Create the `.factory/summaries/` directory if it does not exist
   (use `mkdir -p`).

Keep the summary concise — aim for 10–20% of the original document length.
Write in clear, neutral language. Do not add opinions or interpretations
beyond what the document states.
"""


def workflow() -> Workflow:
    """Build the batch-summarizer workflow.

    Graph structure:
      data (DataNode, start_node)
        └── summarize (AgentNode, RESEARCHER) — subgraph entry AND exit

    The DataNode has NO source set. The runtime resolves the data source
    from the --data CLI argument, injecting source_path and source_format
    into the node before execution begins.
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── DataNode: iterate over documents ──────────────────────────
    # NO source_path, task_ref, or inline_items — late-bound via --data.
    # The executor patches source_path + source_format at runtime.
    nodes["data"] = DataNode(
        id="data",
        subgraph_entry="summarize",
        subgraph_exit="summarize",
        writes={".factory/current_item.json"},
    )

    # ── AgentNode: summarize each document ────────────────────────
    # Reads .factory/current_item.json (written by executor per item).
    # Writes summary to .factory/summaries/{item_id}_summary.md.
    nodes["summarize"] = AgentNode(
        id="summarize",
        role=AgentRole.RESEARCHER,
        prompt_template=_SUMMARIZE_PROMPT,
        reads={".factory/current_item.json"},
        writes={".factory/summaries/"},
        timeout=600,
        max_iterations=1,
    )

    # ── Edges: NONE ───────────────────────────────────────────────
    # Single-node subgraph: entry == exit == 'summarize'.
    # No explicit edges from DataNode to subgraph (executor handles
    # dispatch internally — explicit edges cause double-execution).
    # No edges within the subgraph (single node, no wiring needed).

    return Workflow(
        name="batch-summarizer",
        nodes=nodes,
        edges=edges,
        start_node="data",
        terminal=True,
    )
