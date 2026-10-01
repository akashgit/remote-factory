"""Tests for the batch-doc-scorer portable workflow.

Tests validate graph structure, meta dict, trigger function, node
connectivity, data lineage, and import constraints — all without
executing the workflow against real files.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

# ── Module loading ───────────────────────────────────────────────
# The workflow lives under .factory/workflows/ which isn't a regular
# Python package. Load it via importlib.util.spec_from_file_location.

_WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / ".factory" / "workflows" / "batch_doc_scorer.py"
)


@pytest.fixture()
def mod():
    """Load batch_doc_scorer module from .factory/workflows/."""
    spec = importlib.util.spec_from_file_location("batch_doc_scorer", _WORKFLOW_PATH)
    assert spec is not None, f"Could not create module spec from {_WORKFLOW_PATH}"
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Test 1: Graph validation ────────────────────────────────────


def test_batch_doc_scorer_graph_validates(mod: Any) -> None:
    """Workflow graph has no structural issues."""
    wf = mod.workflow()
    issues = wf.validate_graph()
    assert issues == [], f"Graph validation issues: {issues}"


# ── Test 2: Meta dict completeness ──────────────────────────────


def test_meta_has_required_keys(mod: Any) -> None:
    """Meta dict has name and description keys."""
    assert "name" in mod.meta
    assert "description" in mod.meta
    assert mod.meta["name"] == "batch-doc-scorer"
    assert len(mod.meta["description"]) > 20


# ── Test 3: Trigger function ────────────────────────────────────


def test_trigger_matches_batch_doc_scorer_mode(mod: Any) -> None:
    """Trigger function matches on correct mode."""
    from factory.workflow.primitives import ProjectState

    wf = mod.workflow()
    trigger = wf.trigger

    # Should match
    assert trigger(ProjectState.HAS_FACTORY, {"mode": "batch-doc-scorer"}) is True
    assert trigger(ProjectState.NO_FACTORY, {"mode": "batch-doc-scorer"}) is True

    # Should NOT match
    assert trigger(ProjectState.HAS_FACTORY, {"mode": "pr-review"}) is False
    assert trigger(ProjectState.HAS_FACTORY, {}) is False
    assert trigger(ProjectState.HAS_FACTORY, {"mode": "improve"}) is False


# ── Test 4: Node connectivity ───────────────────────────────────


def test_all_nodes_reachable(mod: Any) -> None:
    """All nodes are reachable from start_node via BFS."""
    wf = mod.workflow()

    visited: set[str] = set()
    queue = [wf.start_node]
    while queue:
        node_id = queue.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)
        for edge in wf.edges:
            if edge.source == node_id:
                queue.append(edge.target)

    assert visited == set(wf.nodes.keys()), (
        f"Unreachable nodes: {set(wf.nodes.keys()) - visited}"
    )


# ── Test 5: Start node exists ───────────────────────────────────


def test_start_node_exists(mod: Any) -> None:
    """start_node references an existing node."""
    wf = mod.workflow()
    assert wf.start_node in wf.nodes, (
        f"start_node '{wf.start_node}' not in nodes: {list(wf.nodes.keys())}"
    )


# ── Test 6: Edge endpoints valid ────────────────────────────────


def test_edge_endpoints_valid(mod: Any) -> None:
    """All edge sources and targets reference existing nodes."""
    wf = mod.workflow()
    node_ids = set(wf.nodes.keys())
    for edge in wf.edges:
        assert edge.source in node_ids, f"Edge source '{edge.source}' not in nodes"
        assert edge.target in node_ids, f"Edge target '{edge.target}' not in nodes"


# ── Test 7: Gate RELOOP edge ────────────────────────────────────


def test_gate_reloop_targets_generate_report(mod: Any) -> None:
    """RELOOP edge from gate_report targets generate_report."""
    from factory.workflow.primitives import VerdictType

    wf = mod.workflow()

    reloop_edges = [
        e for e in wf.edges
        if e.source == "gate_report" and e.condition == VerdictType.RELOOP
    ]
    assert len(reloop_edges) == 1
    assert reloop_edges[0].target == "generate_report"


# ── Test 8: Post-checks on all AgentNodes ───────────────────────


def test_all_agent_nodes_have_post_checks(mod: Any) -> None:
    """Every AgentNode has at least one post_check with required fields."""
    from factory.workflow.primitives import AgentNode

    wf = mod.workflow()

    for node_id, node in wf.nodes.items():
        if isinstance(node, AgentNode):
            assert len(node.post_checks) > 0, (
                f"AgentNode '{node_id}' has no post_checks"
            )
            for check in node.post_checks:
                assert check.must_exist is True
                assert check.min_size > 0
                assert len(check.must_contain) > 0


# ── Test 9: Data lineage ────────────────────────────────────────


def test_data_lineage(mod: Any) -> None:
    """Every file a node reads is written by a predecessor."""

    wf = mod.workflow()

    # BFS to find all predecessors for each node (with cycle detection)
    def predecessors_of(target_id: str, visited: set[str] | None = None) -> set[str]:
        if visited is None:
            visited = set()
        preds: set[str] = set()
        for edge in wf.edges:
            if edge.target == target_id and edge.source not in visited:
                preds.add(edge.source)
                visited.add(edge.source)
                preds |= predecessors_of(edge.source, visited)
        return preds

    # Collect all files written by all nodes
    all_writes: dict[str, set[str]] = {}
    for nid, node in wf.nodes.items():
        all_writes[nid] = node.writes

    # Check: each node's reads are written by a predecessor
    for nid, node in wf.nodes.items():
        if not node.reads:
            continue
        pred_ids = predecessors_of(nid)
        pred_writes: set[str] = set()
        for pid in pred_ids:
            pred_writes |= all_writes.get(pid, set())

        for read_path in node.reads:
            assert read_path in pred_writes, (
                f"Node '{nid}' reads '{read_path}' but no predecessor writes it. "
                f"Predecessors write: {pred_writes}"
            )


# ── Test 10: Workflow is terminal ────────────────────────────────


def test_workflow_is_terminal(mod: Any) -> None:
    """Workflow is terminal."""
    wf = mod.workflow()
    assert wf.terminal is True


# ── Test 11: Workflow name matches meta name ─────────────────────


def test_workflow_name_matches_meta(mod: Any) -> None:
    """Workflow name matches meta dict name."""
    wf = mod.workflow()
    assert wf.name == mod.meta["name"] == "batch-doc-scorer"


# ── Test 12: Only allowed imports ────────────────────────────────


def test_only_allowed_imports() -> None:
    """Workflow file only imports from allowed modules."""
    source = _WORKFLOW_PATH.read_text()
    tree = ast.parse(source)

    allowed_prefixes = {"factory.workflow.primitives", "__future__", "typing"}
    stdlib_modules = set(sys.stdlib_module_names)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module not in allowed_prefixes and module.split(".")[0] not in stdlib_modules:
                raise AssertionError(
                    f"Forbidden import: 'from {module} import ...' — "
                    f"only factory.workflow.primitives and stdlib allowed"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in stdlib_modules:
                    raise AssertionError(
                        f"Forbidden import: 'import {alias.name}' — "
                        f"only factory.workflow.primitives and stdlib allowed"
                    )
