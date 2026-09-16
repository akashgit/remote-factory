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


class TestBatchSummarizerWorkflow:
    """Tests for batch-summarizer workflow graph structure."""

    def test_workflow_name(self) -> None:
        wf = workflow()
        assert wf.name == "batch-summarizer"

    def test_node_count(self) -> None:
        """Workflow has exactly 4 nodes."""
        wf = workflow()
        assert len(wf.nodes) == 4

    def test_node_ids(self) -> None:
        """Exactly these 4 node IDs exist."""
        wf = workflow()
        assert set(wf.nodes.keys()) == {
            "scan_files",
            "iterate_docs",
            "summarize",
            "finalize",
        }

    def test_start_node(self) -> None:
        wf = workflow()
        assert wf.start_node == "scan_files"
        assert "scan_files" in wf.nodes

    def test_graph_validates(self) -> None:
        """Graph passes structural validation with zero issues."""
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow has validation issues: {issues}"

    def test_edge_count(self) -> None:
        """Exactly 2 explicit edges."""
        wf = workflow()
        assert len(wf.edges) == 2

    def test_edge_scan_to_iterate(self) -> None:
        """scan_files → iterate_docs edge exists."""
        wf = workflow()
        edges = [
            e for e in wf.edges
            if e.source == "scan_files" and e.target == "iterate_docs"
        ]
        assert len(edges) == 1

    def test_edge_iterate_to_finalize(self) -> None:
        """iterate_docs → finalize edge exists."""
        wf = workflow()
        edges = [
            e for e in wf.edges
            if e.source == "iterate_docs" and e.target == "finalize"
        ]
        assert len(edges) == 1

    def test_no_edge_iterate_to_summarize(self) -> None:
        """No explicit edge from iterate_docs to summarize (prevents double-execution)."""
        wf = workflow()
        edges = [
            e for e in wf.edges
            if e.source == "iterate_docs" and e.target == "summarize"
        ]
        assert len(edges) == 0


class TestBatchSummarizerTerminal:
    """Tests for the terminal flag."""

    def test_workflow_is_terminal(self) -> None:
        wf = workflow()
        assert wf.terminal is True


class TestBatchSummarizerTrigger:
    """Tests for the trigger function."""

    def test_trigger_matches_batch_summarizer_mode(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "batch-summarizer"})

    def test_trigger_rejects_other_modes(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "build"})

    def test_trigger_rejects_missing_mode(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


class TestBatchSummarizerMeta:
    """Tests for the module-level meta dict."""

    def test_meta_has_name(self) -> None:
        assert meta["name"] == "batch-summarizer"

    def test_meta_has_description(self) -> None:
        assert len(meta["description"]) > 10
        assert "summar" in meta["description"].lower()


class TestBatchSummarizerScanFiles:
    """Tests for the scan_files FnNode."""

    def test_scan_files_is_fn_node(self) -> None:
        wf = workflow()
        assert isinstance(wf.nodes["scan_files"], FnNode)

    def test_scan_files_writes_manifest(self) -> None:
        wf = workflow()
        assert ".factory/manifest.jsonl" in wf.nodes["scan_files"].writes

    def test_scan_files_has_extension_whitelist(self) -> None:
        """Command includes extension filters for text files."""
        wf = workflow()
        node = wf.nodes["scan_files"]
        assert isinstance(node, FnNode)
        assert "*.txt" in node.command
        assert "*.md" in node.command
        assert "*.py" in node.command
        assert "*.json" in node.command
        assert "*.yaml" in node.command


class TestBatchSummarizerDataNode:
    """Tests for the iterate_docs DataNode."""

    def test_iterate_docs_is_data_node(self) -> None:
        wf = workflow()
        assert isinstance(wf.nodes["iterate_docs"], DataNode)

    def test_source_format_is_jsonl(self) -> None:
        """Uses JSONL format (NOT directory which only iterates subdirs)."""
        wf = workflow()
        node = wf.nodes["iterate_docs"]
        assert isinstance(node, DataNode)
        assert node.source_format == "jsonl"

    def test_source_path_is_manifest(self) -> None:
        wf = workflow()
        node = wf.nodes["iterate_docs"]
        assert isinstance(node, DataNode)
        assert node.source_path == ".factory/manifest.jsonl"

    def test_subgraph_entry_and_exit(self) -> None:
        wf = workflow()
        node = wf.nodes["iterate_docs"]
        assert isinstance(node, DataNode)
        assert node.subgraph_entry == "summarize"
        assert node.subgraph_exit == "summarize"

    def test_parallelism(self) -> None:
        wf = workflow()
        node = wf.nodes["iterate_docs"]
        assert isinstance(node, DataNode)
        assert node.parallelism == 1

    def test_max_items(self) -> None:
        wf = workflow()
        node = wf.nodes["iterate_docs"]
        assert isinstance(node, DataNode)
        assert node.max_items == 500


class TestBatchSummarizerLLMNode:
    """Tests for the summarize LLMNode."""

    def test_summarize_is_llm_node(self) -> None:
        wf = workflow()
        assert isinstance(wf.nodes["summarize"], LLMNode)

    def test_summarize_model(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert isinstance(node, LLMNode)
        assert node.model == "haiku"

    def test_summarize_has_file_read_tool(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert isinstance(node, LLMNode)
        tool_names = [t.name for t in node.tools]
        assert "file_read" in tool_names

    def test_summarize_has_file_write_tool(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert isinstance(node, LLMNode)
        tool_names = [t.name for t in node.tools]
        assert "file_write" in tool_names

    def test_summarize_reads_current_item(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert ".factory/current_item.json" in node.reads

    def test_summarize_prompt_references_current_item(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert isinstance(node, LLMNode)
        assert "current_item" in node.system_prompt
        assert "current_item" in node.instance_prompt

    def test_summarize_temperature(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert isinstance(node, LLMNode)
        assert node.temperature == 0.0

    def test_summarize_timeout(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert isinstance(node, LLMNode)
        assert node.timeout == 120

    def test_summarize_max_tokens(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert isinstance(node, LLMNode)
        assert node.max_tokens == 4096

    def test_summarize_max_turns(self) -> None:
        wf = workflow()
        node = wf.nodes["summarize"]
        assert isinstance(node, LLMNode)
        assert node.max_turns == 10


class TestBatchSummarizerFinalize:
    """Tests for the finalize FnNode."""

    def test_finalize_is_fn_node(self) -> None:
        wf = workflow()
        assert isinstance(wf.nodes["finalize"], FnNode)

    def test_finalize_reads_manifest(self) -> None:
        wf = workflow()
        assert ".factory/manifest.jsonl" in wf.nodes["finalize"].reads

    def test_finalize_writes_index(self) -> None:
        wf = workflow()
        assert "summaries/INDEX.md" in wf.nodes["finalize"].writes


class TestBatchSummarizerRegistration:
    """Tests for registration in the global workflow registry."""

    def test_registered_in_register_all(self) -> None:
        workflows = register_all()
        assert "batch-summarizer" in workflows

    def test_registered_workflow_valid(self) -> None:
        workflows = register_all()
        wf = workflows["batch-summarizer"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered workflow has issues: {issues}"

    def test_registered_workflow_has_trigger(self) -> None:
        workflows = register_all()
        wf = workflows["batch-summarizer"]
        assert wf.trigger is not None

    def test_registered_workflow_is_terminal(self) -> None:
        workflows = register_all()
        assert workflows["batch-summarizer"].terminal is True
