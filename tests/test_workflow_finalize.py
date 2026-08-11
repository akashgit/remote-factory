"""Tests for finalize subgraph extraction and standalone finalize workflow."""

from __future__ import annotations


from factory.workflow.definitions import (
    FinalizeConfig,
    _finalize_subgraph,
    _get_builtin_registry,
    build_workflow,
    improve_workflow,
    register_all,
)
from factory.workflow.primitives import (
    AgentNode,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
)


# ── _finalize_subgraph unit tests ───────────────────────────────


class TestFinalizeSubgraph:
    def test_experiment_mode_nodes(self) -> None:
        nodes, _ = _finalize_subgraph(config=FinalizeConfig(mode="experiment"))
        assert set(nodes.keys()) == {
            "gate_precheck",
            "finalize",
            "archivist",
            "spec_update",
        }
        assert isinstance(nodes["finalize"], FnNode)
        assert isinstance(nodes["archivist"], AgentNode)
        assert nodes["archivist"].blocking is False

    def test_archive_mode_nodes(self) -> None:
        nodes, _ = _finalize_subgraph(config=FinalizeConfig(mode="archive"))
        assert set(nodes.keys()) == {
            "gate_precheck",
            "archivist_build",
            "spec_generate",
        }
        assert isinstance(nodes["archivist_build"], AgentNode)
        assert nodes["archivist_build"].blocking is False

    def test_experiment_mode_edges(self) -> None:
        _, edges = _finalize_subgraph(config=FinalizeConfig(mode="experiment"))
        expected = [
            ("gate_precheck", "finalize", VerdictType.PROCEED),
            ("gate_precheck", "archivist", VerdictType.HALT),
            ("finalize", "archivist", None),
            ("archivist", "spec_update", None),
        ]
        assert sorted((e.source, e.target, e.condition) for e in edges) == sorted(expected)

    def test_archive_mode_edges(self) -> None:
        _, edges = _finalize_subgraph(config=FinalizeConfig(mode="archive"))
        expected = [
            ("gate_precheck", "archivist_build", VerdictType.PROCEED),
            ("gate_precheck", "archivist_build", VerdictType.HALT),
            ("archivist_build", "spec_generate", None),
        ]
        assert sorted((e.source, e.target, e.condition) for e in edges) == sorted(expected)

    def test_gate_precheck_is_fn_hard_gate(self) -> None:
        nodes, _ = _finalize_subgraph(config=FinalizeConfig(mode="experiment"))
        gate = nodes["gate_precheck"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "fn"
        assert "factory precheck" in gate.evaluator_command


# ── Preservation: parent graphs unchanged ───────────────────────


class TestFinalizePreservation:
    def test_build_workflow_has_archive_tail(self) -> None:
        wf = build_workflow()
        assert set(wf.nodes.keys()) >= {
            "gate_precheck",
            "archivist_build",
            "spec_generate",
        }
        assert (
            Edge(source="archivist_build", target="spec_generate") in wf.edges
        )

    def test_improve_workflow_has_experiment_tail(self) -> None:
        wf = improve_workflow()
        assert set(wf.nodes.keys()) >= {
            "gate_precheck",
            "finalize",
            "archivist",
            "spec_update",
        }
        assert Edge(source="finalize", target="archivist") in wf.edges
        assert Edge(source="archivist", target="spec_update") in wf.edges


# ── Standalone workflow ─────────────────────────────────────────


class TestFinalizeStandaloneWorkflow:
    def _get_wf(self):
        from factory.workflow.finalize import workflow

        return workflow()

    def test_valid_graph(self) -> None:
        wf = self._get_wf()
        issues = wf.validate_graph()
        assert issues == [], f"finalize-standalone workflow has issues: {issues}"

    def test_name(self) -> None:
        assert self._get_wf().name == "finalize-standalone"

    def test_start_node(self) -> None:
        assert self._get_wf().start_node == "gate_precheck"

    def test_has_expected_nodes(self) -> None:
        wf = self._get_wf()
        assert set(wf.nodes.keys()) == {
            "gate_precheck",
            "finalize",
            "archivist",
            "spec_update",
        }

    def test_trigger_fires_for_finalize_standalone(self) -> None:
        from factory.models import ProjectState

        wf = self._get_wf()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "finalize-standalone"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})

    def test_registered(self) -> None:
        reg = _get_builtin_registry()
        assert "finalize-standalone" in reg

    def test_register_all_includes_it(self) -> None:
        all_wf = register_all()
        assert "finalize-standalone" in all_wf

    def test_meta_exported(self) -> None:
        from factory.workflow.skill_export import WORKFLOW_META

        assert "finalize-standalone" in WORKFLOW_META
        assert WORKFLOW_META["finalize-standalone"]["description"]

    def test_dry_run_executes_to_completion(self, tmp_path) -> None:
        """Regression: gate_precheck reads must be cleared at the standalone
        boundary, or the executor's _wait_for_reads blocks 60s then halts."""
        import asyncio

        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import DEFAULT_AGENT_POOL

        wf = self._get_wf()
        executor = WorkflowExecutor(
            wf, tmp_path, agent_pool=DEFAULT_AGENT_POOL, dry_run=True
        )
        result = asyncio.run(executor.execute())
        assert result.success
        assert not result.halted
