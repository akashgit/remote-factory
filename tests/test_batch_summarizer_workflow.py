"""Tests for the batch-summarizer project-local workflow."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    DataNode,
    Edge,
    GateNode,
    Workflow,
)

# ── helpers ─────────────────────────────────────────────────────

# Resolve the workflow file relative to the repo root (tests/ is one level down).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW_FILE = _REPO_ROOT / ".factory" / "workflows" / "batch_summarizer.py"


def _load_module():
    """Import batch_summarizer.py as a module."""
    spec = importlib.util.spec_from_file_location("batch_summarizer", _WORKFLOW_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_workflow() -> Workflow:
    """Import and call workflow() from the batch_summarizer module."""
    module = _load_module()
    return module.workflow()


# ── test cases ──────────────────────────────────────────────────


class TestWorkflowConstruction:
    """Test that the batch-summarizer workflow constructs correctly."""

    def test_meta_dict(self):
        """Module exposes a meta dict with required keys."""
        module = _load_module()
        assert isinstance(module.meta, dict)
        assert module.meta["name"] == "batch-summarizer"
        assert "description" in module.meta
        assert len(module.meta["description"]) > 0

    def test_workflow_returns_workflow(self):
        """workflow() returns a Workflow instance."""
        wf = _build_workflow()
        assert isinstance(wf, Workflow)

    def test_workflow_name_and_flags(self):
        """Workflow has correct name, start_node, and terminal flag."""
        wf = _build_workflow()
        assert wf.name == "batch-summarizer"
        assert wf.start_node == "data"
        assert wf.terminal is True

    def test_node_count(self):
        """Workflow contains exactly 2 nodes."""
        wf = _build_workflow()
        assert len(wf.nodes) == 2, f"Expected 2 nodes, got {len(wf.nodes)}"

    def test_data_node_type_and_fields(self):
        """DataNode 'data' has correct type and subgraph pointers."""
        wf = _build_workflow()
        data_node = wf.nodes["data"]
        assert isinstance(data_node, DataNode)
        assert data_node.subgraph_entry == "summarize"
        assert data_node.subgraph_exit == "summarize"
        # Late-bound: no source set
        assert data_node.source_path is None
        assert data_node.task_ref is None
        assert data_node.inline_items == []

    def test_agent_node_type_and_fields(self):
        """AgentNode 'summarize' has correct role, reads, writes."""
        wf = _build_workflow()
        summarize_node = wf.nodes["summarize"]
        assert isinstance(summarize_node, AgentNode)
        assert summarize_node.role == AgentRole.RESEARCHER
        assert ".factory/current_item.json" in summarize_node.reads
        assert ".factory/summaries/" in summarize_node.writes
        assert summarize_node.timeout == 600
        assert summarize_node.max_iterations == 1

    def test_edges_empty(self):
        """Workflow has zero edges."""
        wf = _build_workflow()
        assert len(wf.edges) == 0, f"Expected 0 edges, got {len(wf.edges)}"


class TestGraphValidation:
    """Test that validate_graph() accepts the workflow."""

    def test_workflow_validates_clean(self):
        """validate_graph() returns no issues."""
        wf = _build_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Validation issues: {issues}"


class TestNoDoubleExecution:
    """Ensure no explicit edges from DataNode to its subgraph nodes."""

    def test_no_edges_from_datanode_to_subgraph(self):
        wf = _build_workflow()
        for edge in wf.edges:
            assert edge.source != "data" or edge.target != "summarize", (
                "Explicit edge from DataNode 'data' to subgraph node 'summarize' "
                "would cause double-execution"
            )


class TestSubgraphExitSafety:
    """Subgraph exit must not be a looping GateNode."""

    def test_subgraph_exit_is_not_loop_gate(self):
        wf = _build_workflow()
        exit_node = wf.nodes[wf.nodes["data"].subgraph_exit]  # type: ignore[union-attr]
        assert not isinstance(exit_node, GateNode), (
            "subgraph_exit should not be a GateNode"
        )


class TestSerializationRoundTrip:
    """Workflow survives to_dict() → from_dict() round trip."""

    def test_roundtrip(self):
        wf = _build_workflow()
        d = wf.to_dict()

        # Verify JSON-serializable
        json_str = json.dumps(d)
        d2 = json.loads(json_str)

        # Reconstruct
        wf2 = Workflow.from_dict(d2)
        assert wf2.name == wf.name
        assert wf2.start_node == wf.start_node
        assert wf2.terminal == wf.terminal
        assert set(wf2.nodes.keys()) == set(wf.nodes.keys())
        assert len(wf2.edges) == len(wf.edges)

    def test_roundtrip_node_types_preserved(self):
        """Node types are preserved through serialization."""
        wf = _build_workflow()
        wf2 = Workflow.from_dict(json.loads(json.dumps(wf.to_dict())))
        assert isinstance(wf2.nodes["data"], DataNode)
        assert isinstance(wf2.nodes["summarize"], AgentNode)


class TestRegistryDiscovery:
    """WorkflowRegistry discovers the workflow from .factory/workflows/."""

    def test_workflow_discovered_by_registry(self, tmp_path):
        from factory.workflow.registry import WorkflowRegistry

        # Create project structure
        wf_dir = tmp_path / ".factory" / "workflows"
        wf_dir.mkdir(parents=True)

        # Copy workflow file
        shutil.copy(_WORKFLOW_FILE, wf_dir / "batch_summarizer.py")

        WorkflowRegistry.reset()
        entries = WorkflowRegistry.discover(project_path=tmp_path)

        assert "batch-summarizer" in entries
        entry = entries["batch-summarizer"]
        assert entry.source == "project"
        assert "batch" in entry.description.lower()


class TestNegativeEdgeRejection:
    """Adding an edge from data→summarize should produce a validation issue."""

    def test_reject_explicit_datanode_edge(self):
        wf = _build_workflow()

        # Manually add a bad edge
        bad_wf = Workflow(
            name=wf.name,
            nodes=wf.nodes,
            edges=[Edge(source="data", target="summarize")],
            start_node=wf.start_node,
            terminal=wf.terminal,
        )
        issues = bad_wf.validate_graph()
        assert any("double-execution" in issue for issue in issues), (
            f"Expected double-execution warning, got: {issues}"
        )
