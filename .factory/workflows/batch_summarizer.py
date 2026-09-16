"""Batch document summarizer — project-local workflow.

Iterates over documents provided via ``--data <path>`` and generates a
concise summary for each one.  The DataNode's data source is late-bound:
the ``--data`` CLI flag populates ``source_path`` / ``source_format`` at
runtime.
"""

from __future__ import annotations

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    DataNode,
    Edge,
    Workflow,
)

meta = {
    "name": "batch-summarizer",
    "description": (
        "Batch document summarizer — iterates over documents provided via "
        "--data <path> and generates a concise summary for each one."
    ),
}


def workflow() -> Workflow:
    """Build the batch-summarizer workflow.

    The DataNode's data source is late-bound: the ``--data <path>`` CLI flag
    populates ``source_path`` / ``source_format`` at runtime.  We use
    ``model_construct()`` to build the DataNode without triggering the
    Pydantic source-validation that requires exactly one of task_ref /
    source_path / inline_items to be set.
    """

    # ── DataNode (start_node) ──────────────────────────────────────
    # Late-bound: no source_path, task_ref, or inline_items.
    # The --data CLI flag injects source_path + source_format before execution.
    document_loader = DataNode.model_construct(
        id="document_loader",
        reads=set(),
        writes=set(),
        blocking=True,
        task_ref=None,
        source_path=None,
        source_format=None,
        inline_items=[],
        subgraph_entry="summarizer",
        subgraph_exit="summarizer",
        parallelism=1,
        split="all",
        shuffle=False,
        shuffle_seed=None,
        limit=None,
        max_items=500,
    )

    # ── Summarizer (subgraph: single node) ─────────────────────────
    summarizer = AgentNode(
        id="summarizer",
        role=AgentRole.BUILDER,
        prompt_template=(
            "You are a document summarization expert.\n\n"
            "## Task\n\n"
            "Read the document provided to you and write a concise summary.\n\n"
            "## Requirements\n\n"
            "- 3-5 sentences maximum\n"
            "- Focus on the key points, conclusions, and actionable insights\n"
            "- Preserve important names, numbers, and technical terms\n"
            "- Write the summary to .factory/summaries/{current_item_id}-summary.md\n"
            "- Create the .factory/summaries/ directory if it does not exist\n"
        ),
        reads=set(),
        writes=set(),
        timeout=300,
    )

    nodes: dict[str, AgentNode | DataNode] = {
        "document_loader": document_loader,
        "summarizer": summarizer,
    }

    # No edges:
    # - DataNode → subgraph_entry: FORBIDDEN (DataNode dispatches internally)
    # - Subgraph internal: none needed (single-node subgraph, entry == exit)
    edges: list[Edge] = []

    # Use model_construct() for the Workflow as well — Pydantic's strict
    # union validation on the nodes dict would re-validate the DataNode
    # and trigger _validate_source, defeating the late-bound bypass.
    return Workflow.model_construct(
        name="batch-summarizer",
        nodes=nodes,
        edges=edges,
        start_node="document_loader",
        terminal=False,
        task=None,
        trigger=None,
        knob_values={},
        knob_bounds={},
        knob_expandable={},
        knob_specs={},
        declared_capabilities=frozenset(),
    )
