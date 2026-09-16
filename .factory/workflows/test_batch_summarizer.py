"""Structural tests for the batch-summarizer workflow."""

import re

import pytest

from factory.workflow.primitives import (
    DataNode,
    FnNode,
    LLMNode,
    ProjectState,
)

# Import from the workflow module (same directory)
from batch_summarizer import meta, workflow


@pytest.fixture()
def wf():
    """Return the batch-summarizer Workflow instance."""
    return workflow()


# ── Graph structure ──────────────────────────────────────────────────────────


class TestWorkflowStructure:
    def test_name(self, wf):
        assert wf.name == "batch-summarizer"

    def test_node_count(self, wf):
        assert len(wf.nodes) == 5

    def test_start_node(self, wf):
        assert wf.start_node == "discover_docs"

    def test_terminal(self, wf):
        assert wf.terminal is True


# ── Node types ───────────────────────────────────────────────────────────────


class TestNodeTypes:
    def test_discover_docs_is_fnnode(self, wf):
        assert isinstance(wf.nodes["discover_docs"], FnNode)

    def test_iterate_docs_is_datanode(self, wf):
        assert isinstance(wf.nodes["iterate_docs"], DataNode)

    def test_summarize_doc_is_llmnode(self, wf):
        assert isinstance(wf.nodes["summarize_doc"], LLMNode)

    def test_save_summary_is_fnnode(self, wf):
        assert isinstance(wf.nodes["save_summary"], FnNode)

    def test_aggregate_summaries_is_fnnode(self, wf):
        assert isinstance(wf.nodes["aggregate_summaries"], FnNode)


# ── Edges ────────────────────────────────────────────────────────────────────


class TestEdges:
    def test_edge_count(self, wf):
        assert len(wf.edges) == 3

    def test_discover_to_iterate(self, wf):
        pairs = [(e.source, e.target) for e in wf.edges]
        assert ("discover_docs", "iterate_docs") in pairs

    def test_summarize_to_save(self, wf):
        pairs = [(e.source, e.target) for e in wf.edges]
        assert ("summarize_doc", "save_summary") in pairs

    def test_iterate_to_aggregate(self, wf):
        pairs = [(e.source, e.target) for e in wf.edges]
        assert ("iterate_docs", "aggregate_summaries") in pairs

    def test_all_edges_unconditional(self, wf):
        for edge in wf.edges:
            assert edge.condition is None

    def test_no_forbidden_edge_to_subgraph_entry(self, wf):
        """DataNode handles subgraph entry internally — no explicit edge allowed."""
        pairs = [(e.source, e.target) for e in wf.edges]
        assert ("iterate_docs", "summarize_doc") not in pairs


# ── Graph validation ─────────────────────────────────────────────────────────


class TestGraphValidation:
    def test_graph_validates(self, wf):
        issues = wf.validate_graph()
        assert issues == []


# ── Trigger ──────────────────────────────────────────────────────────────────


class TestTrigger:
    def test_accepts_batch_summarizer_mode(self, wf):
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "batch-summarizer"}) is True

    def test_accepts_any_state(self, wf):
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "batch-summarizer"}) is True

    def test_rejects_other_mode(self, wf):
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"}) is False

    def test_rejects_empty_ctx(self, wf):
        assert wf.trigger(ProjectState.HAS_FACTORY, {}) is False


# ── Meta dict ────────────────────────────────────────────────────────────────


class TestMeta:
    def test_meta_name(self):
        assert meta["name"] == "batch-summarizer"

    def test_meta_description(self):
        assert isinstance(meta["description"], str)
        assert len(meta["description"]) > 0


# ── LLMNode configuration ───────────────────────────────────────────────────


class TestLLMNodeConfig:
    def test_reads_current_item_json(self, wf):
        node = wf.nodes["summarize_doc"]
        assert ".factory/current_item.json" in node.reads

    def test_has_file_read_tool(self, wf):
        node = wf.nodes["summarize_doc"]
        tool_names = [t.name for t in node.tools]
        assert "file_read" in tool_names

    def test_instance_prompt_template(self, wf):
        node = wf.nodes["summarize_doc"]
        assert "{instance_context}" in node.instance_prompt

    def test_model(self, wf):
        node = wf.nodes["summarize_doc"]
        assert node.model == "sonnet"

    def test_writes_nonempty(self, wf):
        node = wf.nodes["summarize_doc"]
        assert len(node.writes) > 0


# ── DataNode configuration ──────────────────────────────────────────────────


class TestDataNodeConfig:
    def test_source_format(self, wf):
        node = wf.nodes["iterate_docs"]
        assert node.source_format == "jsonl"

    def test_source_path(self, wf):
        node = wf.nodes["iterate_docs"]
        assert node.source_path == ".factory/batch-summarizer/manifest.jsonl"

    def test_parallelism(self, wf):
        node = wf.nodes["iterate_docs"]
        assert node.parallelism == 1

    def test_subgraph_entry(self, wf):
        node = wf.nodes["iterate_docs"]
        assert node.subgraph_entry == "summarize_doc"
        assert node.subgraph_entry in wf.nodes

    def test_subgraph_exit(self, wf):
        node = wf.nodes["iterate_docs"]
        assert node.subgraph_exit == "save_summary"
        assert node.subgraph_exit in wf.nodes


# ── FnNode command safety ───────────────────────────────────────────────────


class TestFnNodeCommands:
    def test_commands_use_only_project_path(self, wf):
        """All FnNode commands should only use {project_path} template variable."""
        # Match {word} but NOT ${word} (bash variable expansion)
        template_pattern = re.compile(r"(?<!\$)\{(\w+)\}")
        for node in wf.nodes.values():
            if isinstance(node, FnNode) and node.command:
                templates = template_pattern.findall(node.command)
                for t in templates:
                    assert t == "project_path", (
                        f"FnNode '{node.id}' uses unsupported template "
                        f"'{{{t}}}' — only '{{project_path}}' is supported"
                    )
