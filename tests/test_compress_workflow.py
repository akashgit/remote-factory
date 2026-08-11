"""Tests for the compress workflow definition."""
from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.definitions import compress_workflow, register_all
from factory.workflow.primitives import VerdictType


class TestCompressWorkflowGraph:
    """Validate compress workflow graph structure."""

    def test_graph_has_expected_nodes(self):
        wf = compress_workflow()
        expected = {
            "study", "fork_research",
            "researcher_techniques", "researcher_priors", "researcher_eval",
            "join_research", "gate_research",
            "strategist", "gate_strategy",
            "begin", "builder", "gate_build",
            "health_checker", "code_reviewer", "gate_review", "adversarial_tester",
            "gate_qa", "gate_doc_freshness", "gate_precheck",
            "finalize", "archivist",
        }
        assert set(wf.nodes.keys()) == expected

    def test_start_node_is_study(self):
        wf = compress_workflow()
        assert wf.start_node == "study"

    def test_workflow_name(self):
        wf = compress_workflow()
        assert wf.name == "compress"

    def test_all_edge_targets_reference_real_nodes(self):
        wf = compress_workflow()
        node_ids = set(wf.nodes.keys())
        for edge in wf.edges:
            assert edge.source in node_ids, f"Edge source {edge.source!r} not in nodes"
            assert edge.target in node_ids, f"Edge target {edge.target!r} not in nodes"

    def test_fork_join_researcher_alignment(self):
        wf = compress_workflow()
        fork = wf.nodes["fork_research"]
        join = wf.nodes["join_research"]
        assert set(fork.targets) == set(join.sources)

    def test_deep_qa_subgraph_present(self):
        wf = compress_workflow()
        assert "health_checker" in wf.nodes
        assert "code_reviewer" in wf.nodes
        assert "gate_review" in wf.nodes
        assert "adversarial_tester" in wf.nodes

    def test_gate_strategy_is_user_type(self):
        wf = compress_workflow()
        gate = wf.nodes["gate_strategy"]
        assert gate.evaluator_type == "user"

    def test_archivist_is_non_blocking(self):
        wf = compress_workflow()
        archivist = wf.nodes["archivist"]
        assert archivist.blocking is False

    def test_reloop_edges_target_builder(self):
        wf = compress_workflow()
        reloop_edges = [e for e in wf.edges if e.condition == VerdictType.RELOOP]
        builder_reloops = [e for e in reloop_edges if e.target == "builder"]
        # gate_build, gate_qa, gate_doc_freshness all reloop to builder
        assert len(builder_reloops) == 3


class TestCompressWorkflowTrigger:
    """Validate trigger function behavior."""

    def test_triggers_on_has_factory_with_compress_mode(self):
        wf = compress_workflow()
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "compress"}) is True

    def test_does_not_trigger_without_compress_mode(self):
        wf = compress_workflow()
        assert wf.trigger(ProjectState.HAS_FACTORY, {}) is False
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"}) is False

    def test_does_not_trigger_on_wrong_state(self):
        wf = compress_workflow()
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "compress"}) is False
        assert wf.trigger(ProjectState.NO_FACTORY, {"mode": "compress"}) is False


class TestCompressWorkflowRegistration:
    """Validate registration in the factory workflow registry."""

    def test_registered_in_register_all(self):
        workflows = register_all()
        assert "compress" in workflows

    def test_registered_workflow_is_valid(self):
        workflows = register_all()
        wf = workflows["compress"]
        assert wf.name == "compress"
        assert wf.start_node == "study"


class TestCompressWorkflowSkillExport:
    """Validate WORKFLOW_META entry for skill export."""

    def test_workflow_meta_has_compress_entry(self):
        from factory.workflow.skill_export import WORKFLOW_META
        assert "compress" in WORKFLOW_META

    def test_workflow_meta_has_description(self):
        from factory.workflow.skill_export import WORKFLOW_META
        meta = WORKFLOW_META["compress"]
        assert "description" in meta
        assert "compress" in meta["description"].lower()

    def test_workflow_meta_has_argument_hint(self):
        from factory.workflow.skill_export import WORKFLOW_META
        meta = WORKFLOW_META["compress"]
        assert "argument_hint" in meta
