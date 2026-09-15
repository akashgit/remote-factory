"""Mechanical DataNode wrapping for Builder-produced subgraph workflows.

Takes a plain workflow (the per-item subgraph) and wraps it in a DataNode,
making the DataNode the new start_node. The wrapping is purely structural —
no LLM involvement, no source resolution. The data source is either set
via task_ref (when --task is provided) or left late-bound (when --data-node
is used alone).
"""

from __future__ import annotations

import structlog

from factory.workflow.primitives import DataNode, Workflow

log = structlog.get_logger()


def wrap_with_data_node(
    workflow: Workflow,
    *,
    task_ref: str | None = None,
    data_node_id: str = "data",
    parallelism: int = 1,
) -> Workflow:
    """Wrap a Builder-produced subgraph workflow with a DataNode.

    Args:
        workflow: The Builder's output — a plain workflow with no DataNode.
                  Must have exactly one terminal node (no outgoing edges).
        task_ref: Optional task reference string (module:Class or name).
                  When provided, set as DataNode.task_ref.
                  When None, the DataNode is late-bound (no source — resolved at runtime).
        data_node_id: ID for the created DataNode. Defaults to "data".
                      If this collides with an existing node ID, "_data" is tried.
        parallelism: DataNode parallelism setting. Defaults to 1.

    Returns:
        A new Workflow with the DataNode as start_node and the original
        workflow's nodes as the subgraph.

    Raises:
        ValueError: If the workflow has zero nodes, multiple terminal nodes,
                    or the data_node_id collides after fallback attempts.
    """
    if not workflow.nodes:
        raise ValueError("Cannot wrap an empty workflow with a DataNode")

    # Step 1: Identify the subgraph entry point (the original start_node)
    subgraph_entry = workflow.start_node
    if subgraph_entry not in workflow.nodes:
        raise ValueError(
            f"start_node {subgraph_entry!r} not found in workflow nodes"
        )

    # Step 2: Find the terminal node (node with no outgoing edges)
    sources = {e.source for e in workflow.edges}
    all_ids = set(workflow.nodes.keys())
    terminal_candidates = all_ids - sources

    if len(terminal_candidates) == 0:
        raise ValueError(
            "No terminal node found (every node has outgoing edges). "
            "The subgraph must have exactly one terminal node."
        )
    if len(terminal_candidates) > 1:
        # If the entry node is among candidates (single-node workflow), it's the terminal
        if len(workflow.nodes) == 1:
            terminal_candidates = {subgraph_entry}
        else:
            raise ValueError(
                f"Multiple terminal nodes found: {terminal_candidates}. "
                "The subgraph must have exactly one terminal node."
            )
    subgraph_exit = next(iter(terminal_candidates))

    # Step 3: Handle ID collision
    chosen_id = data_node_id
    if chosen_id in workflow.nodes:
        chosen_id = f"_{data_node_id}"
        if chosen_id in workflow.nodes:
            raise ValueError(
                f"DataNode ID collision: both {data_node_id!r} and {chosen_id!r} "
                f"already exist in the workflow nodes."
            )
        log.info(
            "datanode_wrapping.id_collision_resolved",
            original=data_node_id,
            resolved=chosen_id,
        )

    # Step 4: Create the DataNode
    data_node_kwargs: dict = {
        "id": chosen_id,
        "subgraph_entry": subgraph_entry,
        "subgraph_exit": subgraph_exit,
        "parallelism": parallelism,
    }
    if task_ref is not None:
        data_node_kwargs["task_ref"] = task_ref
    # else: late-bound — no source set. Validator now allows sum(sources)==0.

    data_node = DataNode(**data_node_kwargs)

    # Step 5: Assemble the wrapped workflow
    new_nodes = dict(workflow.nodes)
    new_nodes[chosen_id] = data_node

    # Keep ALL original edges (they're subgraph-internal).
    # Do NOT add edges from DataNode → subgraph_entry (forbidden by validation).
    new_edges = list(workflow.edges)

    wrapped = Workflow(
        name=workflow.name,
        nodes=new_nodes,
        edges=new_edges,
        start_node=chosen_id,
        task=task_ref,  # Set workflow.task for outer-loop compatibility
    )

    log.info(
        "datanode_wrapping.complete",
        data_node_id=chosen_id,
        subgraph_entry=subgraph_entry,
        subgraph_exit=subgraph_exit,
        task_ref=task_ref or "(late-bound)",
        node_count=len(new_nodes),
    )

    return wrapped
