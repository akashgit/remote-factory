"""Tests for the doc-drift workflow and CLI subcommand."""

from __future__ import annotations

from factory.workflow.definitions import doc_drift_workflow, register_all
from factory.workflow.primitives import AgentNode, AgentRole, FnNode, GateNode


# ── Workflow graph validation ─────────────────────────────────────


class TestDocDriftWorkflow:
    def test_graph_validates(self) -> None:
        wf = doc_drift_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Graph validation issues: {issues}"

    def test_workflow_name(self) -> None:
        wf = doc_drift_workflow()
        assert wf.name == "doc-drift"

    def test_start_node(self) -> None:
        wf = doc_drift_workflow()
        assert wf.start_node == "scan_prs"

    def test_node_count(self) -> None:
        wf = doc_drift_workflow()
        assert len(wf.nodes) == 6

    def test_node_types(self) -> None:
        wf = doc_drift_workflow()
        assert isinstance(wf.nodes["scan_prs"], FnNode)
        assert isinstance(wf.nodes["classify"], AgentNode)
        assert isinstance(wf.nodes["gate_drift"], GateNode)
        assert isinstance(wf.nodes["builder"], AgentNode)
        assert isinstance(wf.nodes["gate_review"], GateNode)
        assert isinstance(wf.nodes["archivist"], AgentNode)

    def test_classify_uses_haiku(self) -> None:
        wf = doc_drift_workflow()
        classify = wf.nodes["classify"]
        assert isinstance(classify, AgentNode)
        assert classify.model == "haiku"

    def test_classify_role_is_researcher(self) -> None:
        wf = doc_drift_workflow()
        classify = wf.nodes["classify"]
        assert isinstance(classify, AgentNode)
        assert classify.role == AgentRole.RESEARCHER

    def test_builder_role(self) -> None:
        wf = doc_drift_workflow()
        builder = wf.nodes["builder"]
        assert isinstance(builder, AgentNode)
        assert builder.role == AgentRole.BUILDER

    def test_archivist_non_blocking(self) -> None:
        wf = doc_drift_workflow()
        archivist = wf.nodes["archivist"]
        assert isinstance(archivist, AgentNode)
        assert archivist.blocking is False

    def test_archivist_uses_haiku(self) -> None:
        wf = doc_drift_workflow()
        archivist = wf.nodes["archivist"]
        assert isinstance(archivist, AgentNode)
        assert archivist.model == "haiku"

    def test_gate_drift_is_fn_evaluator(self) -> None:
        wf = doc_drift_workflow()
        gate = wf.nodes["gate_drift"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "fn"

    def test_gate_review_is_agent_evaluator(self) -> None:
        wf = doc_drift_workflow()
        gate = wf.nodes["gate_review"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "agent"
        assert gate.evaluator_role == AgentRole.CEO

    def test_edge_count(self) -> None:
        wf = doc_drift_workflow()
        assert len(wf.edges) == 6

    def test_scan_to_classify_edge(self) -> None:
        wf = doc_drift_workflow()
        edge = wf.edges[0]
        assert edge.source == "scan_prs"
        assert edge.target == "classify"
        assert edge.condition is None

    def test_classify_prompt_references_website(self) -> None:
        wf = doc_drift_workflow()
        classify = wf.nodes["classify"]
        assert isinstance(classify, AgentNode)
        assert "website/" in classify.prompt_template

    def test_builder_writes_website(self) -> None:
        wf = doc_drift_workflow()
        builder = wf.nodes["builder"]
        assert isinstance(builder, AgentNode)
        assert "website/" in builder.writes


# ── register_all integration ──────────────────────────────────────


class TestDocDriftRegistration:
    def test_register_all_includes_doc_drift(self) -> None:
        all_wf = register_all()
        assert "doc-drift" in all_wf

    def test_register_all_count(self) -> None:
        all_wf = register_all()
        assert len(all_wf) == 24


# ── CLI arg parsing ───────────────────────────────────────────────


class TestDocDriftCLI:
    def test_parser_accepts_doc_drift(self) -> None:
        from factory.cli._main import build_parser

        parser = build_parser()
        args = parser.parse_args(["doc-drift", "/tmp/project"])
        assert args.command == "doc-drift"
        assert args.path == "/tmp/project"
        assert args.days == 7
        assert args.dry_run is False

    def test_parser_custom_days(self) -> None:
        from factory.cli._main import build_parser

        parser = build_parser()
        args = parser.parse_args(["doc-drift", "/tmp/project", "--days", "14"])
        assert args.days == 14

    def test_parser_dry_run(self) -> None:
        from factory.cli._main import build_parser

        parser = build_parser()
        args = parser.parse_args(["doc-drift", "/tmp/project", "--dry-run"])
        assert args.dry_run is True

    def test_doc_drift_in_command_groups(self) -> None:
        from factory.cli._main import _COMMAND_GROUPS

        intel_group = next(g for name, g in _COMMAND_GROUPS if name == "Project Intelligence")
        assert "doc-drift" in intel_group


# ── scan_prs FnNode command logic ─────────────────────────────────


class TestScanPrsDuplicateDetection:
    def test_scan_command_contains_duplicate_check(self) -> None:
        wf = doc_drift_workflow()
        scan = wf.nodes["scan_prs"]
        assert isinstance(scan, FnNode)
        assert "doc-drift" in scan.command
        assert "open" in scan.command

    def test_scan_command_filters_by_days(self) -> None:
        wf = doc_drift_workflow()
        scan = wf.nodes["scan_prs"]
        assert isinstance(scan, FnNode)
        assert "{days}" in scan.command


# ── Drift classification ──────────────────────────────────────────


class TestDriftClassification:
    def test_classify_prompt_mentions_doc_worthy_signals(self) -> None:
        wf = doc_drift_workflow()
        classify = wf.nodes["classify"]
        assert isinstance(classify, AgentNode)
        prompt = classify.prompt_template
        assert "CLI commands" in prompt
        assert "breaking changes" in prompt
        assert "config options" in prompt

    def test_classify_prompt_mentions_internal_signals(self) -> None:
        wf = doc_drift_workflow()
        classify = wf.nodes["classify"]
        assert isinstance(classify, AgentNode)
        prompt = classify.prompt_template
        assert "test-only" in prompt
        assert "refactor" in prompt

    def test_builder_prompt_has_hallucination_guard(self) -> None:
        wf = doc_drift_workflow()
        builder = wf.nodes["builder"]
        assert isinstance(builder, AgentNode)
        assert "Do not invent" in builder.prompt_template

    def test_builder_prompt_requires_ai_notice(self) -> None:
        wf = doc_drift_workflow()
        builder = wf.nodes["builder"]
        assert isinstance(builder, AgentNode)
        assert "AI-generated" in builder.prompt_template
