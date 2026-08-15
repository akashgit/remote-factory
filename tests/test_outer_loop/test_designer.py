"""Tests for DesignerAgent — design mode and mutation mode."""

from __future__ import annotations

from factory.outer_loop.designer import DesignerAgent
from factory.outer_loop.models import MutationType


class TestDesignMinimal:
    def test_produces_3_to_4_nodes(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_minimal("test benchmark")
        assert 3 <= len(wf.nodes) <= 4

    def test_valid_workflow(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_minimal("test benchmark")
        issues = wf.validate_graph()
        assert issues == [], f"Validation issues: {issues}"

    def test_has_builder(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_minimal("test benchmark")
        roles = {
            node.role.value
            for node in wf.nodes.values()
            if hasattr(node, "role")
        }
        assert "builder" in roles

    def test_has_gate(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_minimal("test benchmark")
        gate_nodes = [
            n for n in wf.nodes.values()
            if type(n).__name__ == "GateNode"
        ]
        assert len(gate_nodes) >= 1

    def test_name_includes_benchmark(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_minimal("feature_bench")
        assert "minimal" in wf.name
        assert "feature_bench" in wf.name

    def test_serialization_roundtrip(self) -> None:
        from factory.workflow.primitives import Workflow

        designer = DesignerAgent()
        wf = designer.design_minimal("test benchmark")
        data = wf.to_dict()
        restored = Workflow.from_dict(data)
        assert len(restored.nodes) == len(wf.nodes)
        assert restored.start_node == wf.start_node


class TestDesignThorough:
    def test_produces_8_to_10_nodes(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_thorough("test benchmark")
        assert 8 <= len(wf.nodes) <= 10

    def test_valid_workflow(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_thorough("test benchmark")
        issues = wf.validate_graph()
        assert issues == [], f"Validation issues: {issues}"

    def test_has_parallel_builders(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_thorough("test benchmark")
        fork_nodes = [
            n for n in wf.nodes.values()
            if type(n).__name__ == "ForkNode"
        ]
        assert len(fork_nodes) >= 1

    def test_has_code_reviewer(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_thorough("test benchmark")
        roles = {
            node.role.value
            for node in wf.nodes.values()
            if hasattr(node, "role")
        }
        assert "code_reviewer" in roles

    def test_has_adversarial_tester(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_thorough("test benchmark")
        roles = {
            node.role.value
            for node in wf.nodes.values()
            if hasattr(node, "role")
        }
        assert "adversarial_tester" in roles

    def test_has_study_node(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_thorough("test benchmark")
        assert "study" in wf.nodes

    def test_serialization_roundtrip(self) -> None:
        from factory.workflow.primitives import Workflow

        designer = DesignerAgent()
        wf = designer.design_thorough("test benchmark")
        data = wf.to_dict()
        restored = Workflow.from_dict(data)
        assert len(restored.nodes) == len(wf.nodes)
        assert restored.start_node == wf.start_node


class TestDesignCustom:
    def test_respects_max_nodes(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_custom("bench", {"max_nodes": 5})
        assert len(wf.nodes) <= 5

    def test_valid_workflow(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_custom("bench", {"max_nodes": 6})
        issues = wf.validate_graph()
        assert issues == [], f"Validation issues: {issues}"

    def test_includes_required_roles(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_custom(
            "bench", {"max_nodes": 8, "require_roles": ["health_checker"]}
        )
        roles = {
            node.role.value
            for node in wf.nodes.values()
            if hasattr(node, "role")
        }
        assert "health_checker" in roles


class TestPropose:
    def test_returns_mutation_records(self, simple_workflow) -> None:  # type: ignore[no-untyped-def]
        designer = DesignerAgent()
        proposals = designer.propose(
            simple_workflow,
            telemetry={"node_stats": {}, "dominant_failure": ""},
            archive_stats={"diversity": 0.5},
            benchmark_spec="test",
        )
        assert len(proposals) >= 1
        assert len(proposals) <= 3

    def test_high_failure_rate_proposes_removal(self, simple_workflow) -> None:  # type: ignore[no-untyped-def]
        designer = DesignerAgent()
        proposals = designer.propose(
            simple_workflow,
            telemetry={
                "node_stats": {"researcher": {"failure_rate": 0.8}},
                "dominant_failure": "",
            },
            archive_stats={"diversity": 0.5},
            benchmark_spec="test",
        )
        remove_proposals = [
            p for p in proposals if p.operator == MutationType.NODE_REMOVE
        ]
        assert len(remove_proposals) >= 1
        assert remove_proposals[0].target_node == "researcher"

    def test_timeout_failure_proposes_param_mutate(self, simple_workflow) -> None:  # type: ignore[no-untyped-def]
        designer = DesignerAgent()
        proposals = designer.propose(
            simple_workflow,
            telemetry={
                "node_stats": {},
                "dominant_failure": "timeout",
            },
            archive_stats={"diversity": 0.5},
            benchmark_spec="test",
        )
        timeout_proposals = [
            p for p in proposals if p.operator == MutationType.PARAM_MUTATE
        ]
        assert len(timeout_proposals) >= 1

    def test_low_diversity_proposes_insertion(self, simple_workflow) -> None:  # type: ignore[no-untyped-def]
        designer = DesignerAgent()
        proposals = designer.propose(
            simple_workflow,
            telemetry={"node_stats": {}, "dominant_failure": ""},
            archive_stats={"diversity": 0.1},
            benchmark_spec="test",
        )
        insert_proposals = [
            p for p in proposals if p.operator == MutationType.NODE_INSERT
        ]
        assert len(insert_proposals) >= 1

    def test_no_signal_still_returns_proposal(self, simple_workflow) -> None:  # type: ignore[no-untyped-def]
        designer = DesignerAgent()
        proposals = designer.propose(
            simple_workflow,
            telemetry={},
            archive_stats={},
            benchmark_spec="test",
        )
        assert len(proposals) >= 1

    def test_max_3_proposals(self, simple_workflow) -> None:  # type: ignore[no-untyped-def]
        designer = DesignerAgent()
        proposals = designer.propose(
            simple_workflow,
            telemetry={
                "node_stats": {
                    "researcher": {"failure_rate": 0.9},
                    "strategist": {"failure_rate": 0.9},
                    "builder": {"failure_rate": 0.9},
                    "gate_qa": {"failure_rate": 0.9},
                },
                "dominant_failure": "timeout",
            },
            archive_stats={"diversity": 0.1},
            benchmark_spec="test",
        )
        assert len(proposals) <= 3
