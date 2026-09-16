"""Tests for the batch-summarizer contributed workflow."""

from __future__ import annotations

from factory.workflow.contributed.batch_summarizer import meta, workflow
from factory.workflow.definitions import register_all
from factory.workflow.primitives import (
    DataNode,
    FnNode,
    LLMNode,
    ProjectState,
)


class TestBatchSummarizerSmoke:
    """Mandatory baseline: meta, graph validation, registration."""

    def test_meta_name(self) -> None:
        assert meta["name"] == "batch-summarizer"

    def test_meta_description_mentions_summarization(self) -> None:
        assert "summar" in meta["description"].lower()

    def test_graph_validates(self) -> None:
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow has validation issues: {issues}"

    def test_registered_in_register_all(self) -> None:
        workflows = register_all()
        assert "batch-summarizer" in workflows

    def test_registered_workflow_validates(self) -> None:
        workflows = register_all()
        wf = workflows["batch-summarizer"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered workflow has issues: {issues}"


class TestBatchSummarizerWorkflow:
    """Graph structure: nodes, types, start node, name."""

    def test_workflow_name(self) -> None:
        wf = workflow()
        assert wf.name == "batch-summarizer"

    def test_start_node(self) -> None:
        wf = workflow()
        assert wf.start_node == "scan_dir"
        assert "scan_dir" in wf.nodes

    def test_node_count(self) -> None:
        wf = workflow()
        assert len(wf.nodes) == 5

    def test_node_ids(self) -> None:
        wf = workflow()
        assert set(wf.nodes.keys()) == {
            "scan_dir", "data_loop", "summarize", "write_summary", "collect_index",
        }

    def test_node_types(self) -> None:
        wf = workflow()
        assert isinstance(wf.nodes["scan_dir"], FnNode)
        assert isinstance(wf.nodes["data_loop"], DataNode)
        assert isinstance(wf.nodes["summarize"], LLMNode)
        assert isinstance(wf.nodes["write_summary"], FnNode)
        assert isinstance(wf.nodes["collect_index"], FnNode)

    def test_data_node_source_format(self) -> None:
        wf = workflow()
        dn = wf.nodes["data_loop"]
        assert isinstance(dn, DataNode)
        assert dn.source_format == "jsonl"

    def test_data_node_subgraph_refs_valid(self) -> None:
        wf = workflow()
        dn = wf.nodes["data_loop"]
        assert isinstance(dn, DataNode)
        assert dn.subgraph_entry in wf.nodes
        assert dn.subgraph_exit in wf.nodes

    def test_no_explicit_edges_into_subgraph_from_data_node(self) -> None:
        """DataNode manages subgraph entry/exit implicitly — no explicit edges."""
        wf = workflow()
        data_to_subgraph = [
            e for e in wf.edges
            if e.source == "data_loop" and e.target in ("summarize", "write_summary")
        ]
        assert data_to_subgraph == []


class TestBatchSummarizerLLMNode:
    """LLM config: tools, prompts, reads."""

    def test_llm_has_file_read_tool(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert isinstance(node, LLMNode)
        tool_names = [t.name for t in node.tools]
        assert "file_read" in tool_names

    def test_llm_has_file_write_tool(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert isinstance(node, LLMNode)
        tool_names = [t.name for t in node.tools]
        assert "file_write" in tool_names

    def test_system_prompt_mentions_summarization(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert isinstance(node, LLMNode)
        assert "summar" in node.system_prompt.lower()


class TestBatchSummarizerTrigger:
    """Mode activation: matches batch-summarizer, rejects others."""

    def test_trigger_matches_batch_summarizer(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "batch-summarizer"})

    def test_trigger_fires_for_all_project_states(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        for state in ProjectState:
            assert wf.trigger(state, {"mode": "batch-summarizer"})

    def test_trigger_rejects_other_modes(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "build"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "design"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


class TestBatchSummarizerEdges:
    """Edge wiring: 3 explicit edges."""

    def test_scan_dir_to_data_loop_edge(self) -> None:
        wf = workflow()
        matching = [
            e for e in wf.edges
            if e.source == "scan_dir" and e.target == "data_loop"
        ]
        assert len(matching) == 1
        assert matching[0].condition is None

    def test_summarize_to_write_summary_edge(self) -> None:
        wf = workflow()
        matching = [
            e for e in wf.edges
            if e.source == "summarize" and e.target == "write_summary"
        ]
        assert len(matching) == 1
        assert matching[0].condition is None

    def test_data_loop_to_collect_index_edge(self) -> None:
        wf = workflow()
        matching = [
            e for e in wf.edges
            if e.source == "data_loop" and e.target == "collect_index"
        ]
        assert len(matching) == 1
        assert matching[0].condition is None


class TestBatchSummarizerTerminal:
    """Terminal flag on workflow."""

    def test_workflow_is_terminal(self) -> None:
        wf = workflow()
        assert wf.terminal is True

    def test_registered_workflow_is_terminal(self) -> None:
        workflows = register_all()
        assert workflows["batch-summarizer"].terminal is True


class TestBatchSummarizerDataNode:
    """DataNode configuration details."""

    def test_source_path(self) -> None:
        wf = workflow()
        dn = wf.nodes["data_loop"]
        assert isinstance(dn, DataNode)
        assert dn.source_path == ".factory/batch_summarizer/manifest.jsonl"

    def test_subgraph_entry_and_exit(self) -> None:
        wf = workflow()
        dn = wf.nodes["data_loop"]
        assert isinstance(dn, DataNode)
        assert dn.subgraph_entry == "summarize"
        assert dn.subgraph_exit == "write_summary"

    def test_parallelism(self) -> None:
        wf = workflow()
        dn = wf.nodes["data_loop"]
        assert isinstance(dn, DataNode)
        assert dn.parallelism == 1

    def test_instance_prompt_has_placeholder(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert isinstance(node, LLMNode)
        assert "{instance_context}" in node.instance_prompt

    def test_llm_reads_current_item(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert isinstance(node, LLMNode)
        assert ".factory/current_item.json" in node.reads
