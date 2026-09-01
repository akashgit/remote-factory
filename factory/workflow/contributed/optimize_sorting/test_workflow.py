"""Tests for the optimize-sorting contributed workflow."""

from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.contributed.optimize_sorting import meta, workflow
from factory.workflow.definitions import register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
)


# ── Graph Validation ──────────────────────────────────────────────


class TestGraphValidation:
    """Graph structural validation tests."""

    def test_validate_graph_returns_empty(self) -> None:
        """validate_graph() returns [] — no structural issues."""
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow has validation issues: {issues}"

    def test_workflow_name(self) -> None:
        wf = workflow()
        assert wf.name == "optimize-sorting"

    def test_workflow_is_terminal(self) -> None:
        wf = workflow()
        assert wf.terminal is True

    def test_start_node(self) -> None:
        wf = workflow()
        assert wf.start_node == "lock_baseline"


# ── Node Existence ────────────────────────────────────────────────


class TestNodeExistence:
    """All 25 nodes must be present."""

    SHARED_NODES = {
        "lock_baseline",
        "select_tier",
        "gate_is_tier1",
        "gate_is_tier2",
        "gate_is_tier3",
    }

    TIER1_NODES = {
        "researcher_discover_params",
        "strategist_t1",
        "builder_config_change",
        "gate_no_code_changes",
        "run_benchmark_t1",
        "gate_accuracy_t1",
        "confirm_benchmark_t1",
        "archive_result_t1",
    }

    TIER2_NODES = {
        "researcher_profile_pipeline",
        "strategist_t2",
        "builder_optimize_hotpath",
        "run_benchmark_t2",
        "gate_accuracy_t2",
        "confirm_benchmark_t2",
        "archive_result_t2",
    }

    TIER3_NODES = {
        "researcher_explore_alternatives",
        "strategist_t3",
        "builder_implement_alternative",
        "run_benchmark_t3",
        "gate_accuracy_t3",
        "gate_per_unit_accuracy",
        "confirm_benchmark_t3",
        "archive_result_t3",
    }

    ALL_NODES = SHARED_NODES | TIER1_NODES | TIER2_NODES | TIER3_NODES

    def test_total_node_count(self) -> None:
        wf = workflow()
        assert len(wf.nodes) == 28

    def test_all_nodes_present(self) -> None:
        wf = workflow()
        assert set(wf.nodes.keys()) == self.ALL_NODES

    def test_shared_node_count(self) -> None:
        assert len(self.SHARED_NODES) == 5

    def test_tier1_node_count(self) -> None:
        assert len(self.TIER1_NODES) == 8

    def test_tier2_node_count(self) -> None:
        assert len(self.TIER2_NODES) == 7

    def test_tier3_node_count(self) -> None:
        assert len(self.TIER3_NODES) == 8


# ── Node Types ────────────────────────────────────────────────────


class TestNodeTypes:
    """Verify correct node types for each node."""

    def test_lock_baseline_is_fn(self) -> None:
        wf = workflow()
        assert isinstance(wf.nodes["lock_baseline"], FnNode)

    def test_select_tier_is_fn(self) -> None:
        wf = workflow()
        assert isinstance(wf.nodes["select_tier"], FnNode)

    def test_tier_gates_are_gate_nodes(self) -> None:
        wf = workflow()
        for name in ("gate_is_tier1", "gate_is_tier2", "gate_is_tier3"):
            node = wf.nodes[name]
            assert isinstance(node, GateNode), f"{name} should be GateNode"
            assert node.evaluator_type == "fn"
            assert node.evaluator_command is not None

    def test_config_gate_is_gate_node(self) -> None:
        wf = workflow()
        node = wf.nodes["gate_no_code_changes"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"

    def test_accuracy_gates_are_gate_nodes(self) -> None:
        wf = workflow()
        for name in ("gate_accuracy_t1", "gate_accuracy_t2", "gate_accuracy_t3"):
            node = wf.nodes[name]
            assert isinstance(node, GateNode), f"{name} should be GateNode"
            assert node.evaluator_type == "fn"

    def test_per_unit_gate_is_gate_node(self) -> None:
        wf = workflow()
        node = wf.nodes["gate_per_unit_accuracy"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"

    def test_benchmark_nodes_are_fn(self) -> None:
        wf = workflow()
        for name in ("run_benchmark_t1", "run_benchmark_t2", "run_benchmark_t3"):
            assert isinstance(wf.nodes[name], FnNode), f"{name} should be FnNode"

    def test_confirm_benchmark_nodes_are_fn(self) -> None:
        wf = workflow()
        for name in (
            "confirm_benchmark_t1",
            "confirm_benchmark_t2",
            "confirm_benchmark_t3",
        ):
            assert isinstance(wf.nodes[name], FnNode), f"{name} should be FnNode"

    def test_researcher_nodes_are_agent(self) -> None:
        wf = workflow()
        for name in (
            "researcher_discover_params",
            "researcher_profile_pipeline",
            "researcher_explore_alternatives",
        ):
            node = wf.nodes[name]
            assert isinstance(node, AgentNode), f"{name} should be AgentNode"
            assert node.role == AgentRole.RESEARCHER

    def test_strategist_nodes_are_agent(self) -> None:
        wf = workflow()
        for name in ("strategist_t1", "strategist_t2", "strategist_t3"):
            node = wf.nodes[name]
            assert isinstance(node, AgentNode), f"{name} should be AgentNode"
            assert node.role == AgentRole.STRATEGIST

    def test_builder_nodes_are_agent(self) -> None:
        wf = workflow()
        for name in (
            "builder_config_change",
            "builder_optimize_hotpath",
            "builder_implement_alternative",
        ):
            node = wf.nodes[name]
            assert isinstance(node, AgentNode), f"{name} should be AgentNode"
            assert node.role == AgentRole.BUILDER
            assert node.model == "opus"
            assert node.timeout == 3600

    def test_archive_nodes_are_archivist(self) -> None:
        wf = workflow()
        for name in ("archive_result_t1", "archive_result_t2", "archive_result_t3"):
            node = wf.nodes[name]
            assert isinstance(node, AgentNode), f"{name} should be AgentNode"
            assert node.role == AgentRole.ARCHIVIST


# ── Edge Topology ─────────────────────────────────────────────────


class TestEdgeTopology:
    """All 33 edges with correct conditions."""

    def test_edge_count(self) -> None:
        wf = workflow()
        assert len(wf.edges) == 36

    def _find_edges(
        self,
        source: str,
        target: str,
        condition: VerdictType | None = None,
    ) -> list[Edge]:
        wf = workflow()
        return [
            e
            for e in wf.edges
            if e.source == source
            and e.target == target
            and e.condition == condition
        ]

    # Baseline + Tier Selection
    def test_edge_lock_to_select(self) -> None:
        assert len(self._find_edges("lock_baseline", "select_tier")) == 1

    def test_edge_select_to_gate1(self) -> None:
        assert len(self._find_edges("select_tier", "gate_is_tier1")) == 1

    # Tier routing chain
    def test_edge_gate1_proceed(self) -> None:
        assert len(self._find_edges(
            "gate_is_tier1", "researcher_discover_params", VerdictType.PROCEED
        )) == 1

    def test_edge_gate1_halt(self) -> None:
        assert len(self._find_edges(
            "gate_is_tier1", "gate_is_tier2", VerdictType.HALT
        )) == 1

    def test_edge_gate2_proceed(self) -> None:
        assert len(self._find_edges(
            "gate_is_tier2", "researcher_profile_pipeline", VerdictType.PROCEED
        )) == 1

    def test_edge_gate2_halt(self) -> None:
        assert len(self._find_edges(
            "gate_is_tier2", "gate_is_tier3", VerdictType.HALT
        )) == 1

    def test_edge_gate3_proceed(self) -> None:
        assert len(self._find_edges(
            "gate_is_tier3", "researcher_explore_alternatives", VerdictType.PROCEED
        )) == 1

    # Tier 1 subgraph
    def test_edge_t1_researcher_to_strategist(self) -> None:
        assert len(self._find_edges(
            "researcher_discover_params", "strategist_t1"
        )) == 1

    def test_edge_t1_strategist_to_builder(self) -> None:
        assert len(self._find_edges("strategist_t1", "builder_config_change")) == 1

    def test_edge_t1_builder_to_config_gate(self) -> None:
        assert len(self._find_edges(
            "builder_config_change", "gate_no_code_changes"
        )) == 1

    def test_edge_t1_config_gate_proceed(self) -> None:
        assert len(self._find_edges(
            "gate_no_code_changes", "run_benchmark_t1", VerdictType.PROCEED
        )) == 1

    def test_edge_t1_config_gate_reloop(self) -> None:
        assert len(self._find_edges(
            "gate_no_code_changes", "builder_config_change", VerdictType.RELOOP
        )) == 1

    def test_edge_t1_benchmark_to_accuracy(self) -> None:
        assert len(self._find_edges("run_benchmark_t1", "gate_accuracy_t1")) == 1

    def test_edge_t1_accuracy_proceed(self) -> None:
        assert len(self._find_edges(
            "gate_accuracy_t1", "confirm_benchmark_t1", VerdictType.PROCEED
        )) == 1

    def test_edge_t1_confirm_to_archive(self) -> None:
        assert len(self._find_edges("confirm_benchmark_t1", "archive_result_t1")) == 1

    def test_edge_t1_accuracy_reloop(self) -> None:
        assert len(self._find_edges(
            "gate_accuracy_t1", "builder_config_change", VerdictType.RELOOP
        )) == 1

    def test_edge_t1_accuracy_halt(self) -> None:
        assert len(self._find_edges(
            "gate_accuracy_t1", "archive_result_t1", VerdictType.HALT
        )) == 1

    # Tier 2 subgraph
    def test_edge_t2_researcher_to_strategist(self) -> None:
        assert len(self._find_edges(
            "researcher_profile_pipeline", "strategist_t2"
        )) == 1

    def test_edge_t2_strategist_to_builder(self) -> None:
        assert len(self._find_edges(
            "strategist_t2", "builder_optimize_hotpath"
        )) == 1

    def test_edge_t2_builder_to_benchmark(self) -> None:
        assert len(self._find_edges(
            "builder_optimize_hotpath", "run_benchmark_t2"
        )) == 1

    def test_edge_t2_benchmark_to_accuracy(self) -> None:
        assert len(self._find_edges("run_benchmark_t2", "gate_accuracy_t2")) == 1

    def test_edge_t2_accuracy_proceed(self) -> None:
        assert len(self._find_edges(
            "gate_accuracy_t2", "confirm_benchmark_t2", VerdictType.PROCEED
        )) == 1

    def test_edge_t2_confirm_to_archive(self) -> None:
        assert len(self._find_edges("confirm_benchmark_t2", "archive_result_t2")) == 1

    def test_edge_t2_accuracy_reloop(self) -> None:
        assert len(self._find_edges(
            "gate_accuracy_t2", "builder_optimize_hotpath", VerdictType.RELOOP
        )) == 1

    def test_edge_t2_accuracy_halt(self) -> None:
        assert len(self._find_edges(
            "gate_accuracy_t2", "archive_result_t2", VerdictType.HALT
        )) == 1

    # Tier 3 subgraph
    def test_edge_t3_researcher_to_strategist(self) -> None:
        assert len(self._find_edges(
            "researcher_explore_alternatives", "strategist_t3"
        )) == 1

    def test_edge_t3_strategist_to_builder(self) -> None:
        assert len(self._find_edges(
            "strategist_t3", "builder_implement_alternative"
        )) == 1

    def test_edge_t3_builder_to_benchmark(self) -> None:
        assert len(self._find_edges(
            "builder_implement_alternative", "run_benchmark_t3"
        )) == 1

    def test_edge_t3_benchmark_to_accuracy(self) -> None:
        assert len(self._find_edges("run_benchmark_t3", "gate_accuracy_t3")) == 1

    def test_edge_t3_accuracy_proceed(self) -> None:
        assert len(self._find_edges(
            "gate_accuracy_t3", "gate_per_unit_accuracy", VerdictType.PROCEED
        )) == 1

    def test_edge_t3_accuracy_reloop(self) -> None:
        assert len(self._find_edges(
            "gate_accuracy_t3", "builder_implement_alternative", VerdictType.RELOOP
        )) == 1

    def test_edge_t3_accuracy_halt(self) -> None:
        assert len(self._find_edges(
            "gate_accuracy_t3", "archive_result_t3", VerdictType.HALT
        )) == 1

    def test_edge_t3_per_unit_proceed(self) -> None:
        assert len(self._find_edges(
            "gate_per_unit_accuracy", "confirm_benchmark_t3", VerdictType.PROCEED
        )) == 1

    def test_edge_t3_confirm_to_archive(self) -> None:
        assert len(self._find_edges("confirm_benchmark_t3", "archive_result_t3")) == 1

    def test_edge_t3_per_unit_reloop(self) -> None:
        assert len(self._find_edges(
            "gate_per_unit_accuracy", "builder_implement_alternative", VerdictType.RELOOP
        )) == 1

    def test_edge_t3_per_unit_halt(self) -> None:
        assert len(self._find_edges(
            "gate_per_unit_accuracy", "archive_result_t3", VerdictType.HALT
        )) == 1


# ── Tier Routing Mutual Exclusivity ───────────────────────────────


class TestTierRouting:
    """Tier routing gates ensure mutual exclusivity."""

    def test_gate_is_tier1_has_exactly_two_outgoing(self) -> None:
        wf = workflow()
        outgoing = [e for e in wf.edges if e.source == "gate_is_tier1"]
        assert len(outgoing) == 2
        conditions = {e.condition for e in outgoing}
        assert conditions == {VerdictType.PROCEED, VerdictType.HALT}

    def test_gate_is_tier2_has_exactly_two_outgoing(self) -> None:
        wf = workflow()
        outgoing = [e for e in wf.edges if e.source == "gate_is_tier2"]
        assert len(outgoing) == 2
        conditions = {e.condition for e in outgoing}
        assert conditions == {VerdictType.PROCEED, VerdictType.HALT}

    def test_gate_is_tier3_has_exactly_one_outgoing(self) -> None:
        """gate_is_tier3 HALT terminates workflow — only PROCEED edge exists."""
        wf = workflow()
        outgoing = [e for e in wf.edges if e.source == "gate_is_tier3"]
        assert len(outgoing) == 1
        assert outgoing[0].condition == VerdictType.PROCEED

    def test_tier_gates_form_chain(self) -> None:
        """gate_is_tier1 HALT → gate_is_tier2, gate_is_tier2 HALT → gate_is_tier3."""
        wf = workflow()
        halt_1 = [
            e for e in wf.edges
            if e.source == "gate_is_tier1" and e.condition == VerdictType.HALT
        ]
        assert len(halt_1) == 1
        assert halt_1[0].target == "gate_is_tier2"

        halt_2 = [
            e for e in wf.edges
            if e.source == "gate_is_tier2" and e.condition == VerdictType.HALT
        ]
        assert len(halt_2) == 1
        assert halt_2[0].target == "gate_is_tier3"


# ── Data Dependency Validation ────────────────────────────────────


class TestDataDependencies:
    """Reads sets are satisfied by predecessor writes sets."""

    def test_select_tier_reads_baseline(self) -> None:
        wf = workflow()
        node = wf.nodes["select_tier"]
        assert ".factory/sorting/baseline.json" in node.reads
        # lock_baseline writes it
        writer = wf.nodes["lock_baseline"]
        assert ".factory/sorting/baseline.json" in writer.writes

    def test_tier_gates_read_selection(self) -> None:
        wf = workflow()
        for name in ("gate_is_tier1", "gate_is_tier2", "gate_is_tier3"):
            node = wf.nodes[name]
            assert ".factory/sorting/tier-selection.json" in node.reads

    def test_accuracy_gates_read_benchmark_and_baseline(self) -> None:
        wf = workflow()
        for name in ("gate_accuracy_t1", "gate_accuracy_t2", "gate_accuracy_t3"):
            node = wf.nodes[name]
            assert ".factory/sorting/benchmark-result.json" in node.reads
            assert ".factory/sorting/baseline.json" in node.reads

    def test_archive_nodes_read_benchmark_and_baseline(self) -> None:
        wf = workflow()
        for name in ("archive_result_t1", "archive_result_t2", "archive_result_t3"):
            node = wf.nodes[name]
            assert ".factory/sorting/benchmark-result.json" in node.reads
            assert ".factory/sorting/baseline.json" in node.reads

    def test_confirm_benchmark_nodes_read_and_write_benchmark_result(self) -> None:
        wf = workflow()
        for name in (
            "confirm_benchmark_t1",
            "confirm_benchmark_t2",
            "confirm_benchmark_t3",
        ):
            node = wf.nodes[name]
            assert ".factory/sorting/benchmark-result.json" in node.reads
            assert ".factory/sorting/benchmark-result.json" in node.writes

    def test_per_unit_gate_reads_benchmark_and_baseline(self) -> None:
        wf = workflow()
        node = wf.nodes["gate_per_unit_accuracy"]
        assert ".factory/sorting/benchmark-result.json" in node.reads
        assert ".factory/sorting/baseline.json" in node.reads


# ── Trigger Function ──────────────────────────────────────────────


class TestTrigger:
    """Trigger function tests."""

    def test_trigger_matches_optimize_sorting(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "optimize-sorting"})

    def test_trigger_rejects_other_modes(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "design"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})

    def test_trigger_works_with_any_state(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "optimize-sorting"})
        assert wf.trigger(ProjectState.NO_FACTORY, {"mode": "optimize-sorting"})


# ── Meta Dict ─────────────────────────────────────────────────────


class TestMeta:
    """Module-level meta dict tests."""

    def test_meta_has_name(self) -> None:
        assert meta["name"] == "optimize-sorting"

    def test_meta_has_description(self) -> None:
        desc = meta["description"]
        assert isinstance(desc, str)
        assert len(desc) > 0
        assert "sorting" in desc.lower()

    def test_meta_description_mentions_tiers(self) -> None:
        desc = meta["description"]
        assert "tier 1" in desc.lower() or "tier1" in desc.lower()


# ── Registration ──────────────────────────────────────────────────


class TestRegistration:
    """Workflow is properly registered in register_all()."""

    def test_registered_in_register_all(self) -> None:
        workflows = register_all()
        assert "optimize-sorting" in workflows

    def test_registered_workflow_validates(self) -> None:
        workflows = register_all()
        wf = workflows["optimize-sorting"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered workflow has issues: {issues}"

    def test_registered_workflow_is_terminal(self) -> None:
        workflows = register_all()
        assert workflows["optimize-sorting"].terminal is True


# ── Prompt Content ────────────────────────────────────────────────


class TestPromptContent:
    """Prompt templates contain required domain context."""

    def test_researcher_prompts_mention_neuropixels(self) -> None:
        wf = workflow()
        for name in (
            "researcher_discover_params",
            "researcher_profile_pipeline",
            "researcher_explore_alternatives",
        ):
            node = wf.nodes[name]
            assert isinstance(node, AgentNode)
            assert "neuropixels" in node.prompt_template.lower()

    def test_builder_prompts_mention_neuropixels(self) -> None:
        wf = workflow()
        for name in (
            "builder_config_change",
            "builder_optimize_hotpath",
            "builder_implement_alternative",
        ):
            node = wf.nodes[name]
            assert isinstance(node, AgentNode)
            assert "neuropixels" in node.prompt_template.lower()

    def test_prompts_mention_gpu(self) -> None:
        wf = workflow()
        for name in (
            "researcher_discover_params",
            "researcher_profile_pipeline",
            "researcher_explore_alternatives",
        ):
            node = wf.nodes[name]
            assert isinstance(node, AgentNode)
            assert "gpu" in node.prompt_template.lower()

    def test_prompts_mention_speed_target(self) -> None:
        wf = workflow()
        for name in (
            "researcher_discover_params",
            "strategist_t1",
            "builder_config_change",
        ):
            node = wf.nodes[name]
            assert isinstance(node, AgentNode)
            assert "speed" in node.prompt_template.lower()
            assert "accuracy" in node.prompt_template.lower()

    def test_builder_t1_mentions_config_only(self) -> None:
        wf = workflow()
        node = wf.nodes["builder_config_change"]
        assert isinstance(node, AgentNode)
        assert ".yaml" in node.prompt_template
        assert ".json" in node.prompt_template
        assert "ZERO source code" in node.prompt_template

    def test_builder_t3_mentions_per_unit(self) -> None:
        wf = workflow()
        node = wf.nodes["builder_implement_alternative"]
        assert isinstance(node, AgentNode)
        assert "per-unit" in node.prompt_template.lower() or "per_unit" in node.prompt_template.lower()

    def test_all_agent_nodes_have_prompts(self) -> None:
        """All 12 AgentNodes (10 unique templates, archivists share 1) have non-empty prompts."""
        wf = workflow()
        agent_nodes = [
            n for n in wf.nodes.values() if isinstance(n, AgentNode)
        ]
        assert len(agent_nodes) == 12
        for node in agent_nodes:
            assert node.prompt_template, f"{node.id} has empty prompt_template"


# ── RELOOP Edges ──────────────────────────────────────────────────


class TestReloopEdges:
    """All RELOOP cycles contain a GateNode with conditional edge."""

    def test_all_reloop_edges_originate_from_gates(self) -> None:
        wf = workflow()
        reloop_edges = [e for e in wf.edges if e.condition == VerdictType.RELOOP]
        assert len(reloop_edges) == 5  # 2 in T1, 1 in T2, 2 in T3
        for edge in reloop_edges:
            assert isinstance(wf.nodes[edge.source], GateNode), (
                f"RELOOP edge from {edge.source} but it's not a GateNode"
            )

    def test_reloop_count(self) -> None:
        wf = workflow()
        reloop_edges = [e for e in wf.edges if e.condition == VerdictType.RELOOP]
        assert len(reloop_edges) == 5
