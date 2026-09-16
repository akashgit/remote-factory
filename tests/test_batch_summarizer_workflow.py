"""Tests for the batch-summarizer project-local workflow."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from factory.workflow.primitives import AgentNode, AgentRole, DataNode


def _load_workflow():
    """Load the project-local workflow module."""
    wf_path = (
        Path(__file__).resolve().parents[1]
        / ".factory"
        / "workflows"
        / "batch_summarizer.py"
    )
    spec = importlib.util.spec_from_file_location("batch_summarizer", wf_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    sys.modules.pop(spec.name, None)
    return mod


# ── Test 1: Graph validation passes ──────────────────────────────


def test_graph_validates():
    """workflow().validate_graph() returns no issues."""
    mod = _load_workflow()
    wf = mod.workflow()
    issues = wf.validate_graph()
    assert issues == [], f"Graph validation issues: {issues}"


# ── Test 2: start_node is DataNode ────────────────────────────────


def test_start_node_is_data_node():
    """start_node must be the DataNode 'document_loader'."""
    mod = _load_workflow()
    wf = mod.workflow()
    assert wf.start_node == "document_loader"
    start = wf.nodes[wf.start_node]
    assert isinstance(start, DataNode)


# ── Test 3: Subgraph entry/exit point to summarizer ───────────────


def test_subgraph_entry_exit():
    """DataNode's subgraph_entry and subgraph_exit both point to 'summarizer'."""
    mod = _load_workflow()
    wf = mod.workflow()
    data_node = wf.nodes["document_loader"]
    assert data_node.subgraph_entry == "summarizer"
    assert data_node.subgraph_exit == "summarizer"
    # Verify the target node exists
    assert "summarizer" in wf.nodes


# ── Test 4: DataNode has no source fields set (late-bound) ────────


def test_data_source_is_late_bound():
    """DataNode must not have source_path, task_ref, or inline_items set."""
    mod = _load_workflow()
    wf = mod.workflow()
    data_node = wf.nodes["document_loader"]
    assert data_node.source_path is None
    assert data_node.task_ref is None
    assert data_node.inline_items == [] or not data_node.inline_items


# ── Test 5: No edges from DataNode to subgraph ────────────────────


def test_no_edges_from_datanode_to_subgraph():
    """There must be no explicit edges from document_loader to summarizer."""
    mod = _load_workflow()
    wf = mod.workflow()
    bad_edges = [
        e
        for e in wf.edges
        if e.source == "document_loader" and e.target == "summarizer"
    ]
    assert bad_edges == [], (
        "DataNode must not have explicit edges to its subgraph — "
        "it dispatches internally"
    )


# ── Test 6: Meta dict is well-formed ──────────────────────────────


def test_meta_dict():
    """meta dict must have 'name' and 'description' keys."""
    mod = _load_workflow()
    assert isinstance(mod.meta, dict)
    assert mod.meta["name"] == "batch-summarizer"
    assert "description" in mod.meta
    assert len(mod.meta["description"]) > 10


# ── Test 7: Summarizer node has correct role ───────────────────────


def test_summarizer_is_builder_agent():
    """Summarizer node must be an AgentNode with BUILDER role."""
    mod = _load_workflow()
    wf = mod.workflow()
    summarizer = wf.nodes["summarizer"]
    assert isinstance(summarizer, AgentNode)
    assert summarizer.role == AgentRole.BUILDER


# ── Test 8: SKILL.md generation does not crash ─────────────────────


def test_skill_md_generation():
    """workflow_to_skill_md() should produce valid output without errors."""
    from factory.workflow.skill_export import workflow_to_skill_md

    mod = _load_workflow()
    wf = mod.workflow()
    skill_md = workflow_to_skill_md(wf)
    assert isinstance(skill_md, str)
    assert len(skill_md) > 0
    assert "document_loader" in skill_md or "batch-summarizer" in skill_md


# ── Test 9: Workflow name matches meta name ────────────────────────


def test_workflow_name_matches_meta():
    """Workflow.name must match meta['name']."""
    mod = _load_workflow()
    wf = mod.workflow()
    assert wf.name == mod.meta["name"]


# ── Test 10: Node count is exactly 2 ──────────────────────────────


def test_node_count():
    """Minimal workflow has exactly 2 nodes: document_loader + summarizer."""
    mod = _load_workflow()
    wf = mod.workflow()
    assert len(wf.nodes) == 2
    assert set(wf.nodes.keys()) == {"document_loader", "summarizer"}
