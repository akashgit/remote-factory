"""Tests for DesignerAgent — design mode and mutation mode."""

from __future__ import annotations

from factory.outer_loop.designer import (
    DesignerAgent,
    _propagate_prompts_from_seed,
    _validate_and_fix,
)
from factory.outer_loop.models import MutationType
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    DataItem,
    DataNode,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)


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


class TestFrozenNodePreservation:
    """Tests for frozen node injection in designer methods."""

    @staticmethod
    def _seed_with_positions() -> Workflow:
        """Create a seed workflow containing a FnNode with id='positions'."""
        return Workflow(
            name="seed",
            nodes={
                "positions": FnNode(
                    id="positions",
                    command="load_positions",
                    writes={".factory/positions.json"},
                ),
                "researcher": AgentNode(
                    id="researcher",
                    role=AgentRole.RESEARCHER,
                ),
            },
            edges=[Edge(source="positions", target="researcher")],
            start_node="positions",
        )

    def test_design_minimal_preserves_frozen_nodes(self) -> None:
        designer = DesignerAgent()
        seed = self._seed_with_positions()
        result = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        assert "positions" in result.nodes
        assert result.nodes["positions"].command == "load_positions"  # type: ignore[union-attr]

    def test_design_thorough_preserves_frozen_nodes(self) -> None:
        designer = DesignerAgent()
        seed = self._seed_with_positions()
        result = designer.design_thorough(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        assert "positions" in result.nodes
        assert result.nodes["positions"].command == "load_positions"  # type: ignore[union-attr]

    def test_design_custom_preserves_frozen_nodes(self) -> None:
        designer = DesignerAgent()
        seed = self._seed_with_positions()
        result = designer.design_custom(
            "bench",
            {"max_nodes": 6},
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        assert "positions" in result.nodes
        assert result.nodes["positions"].command == "load_positions"  # type: ignore[union-attr]

    def test_frozen_node_overwrites_template_on_collision(self) -> None:
        """When a frozen node ID collides with a template node, frozen wins."""
        seed = Workflow(
            name="seed",
            nodes={
                "researcher": AgentNode(
                    id="researcher",
                    role=AgentRole.RESEARCHER,
                    timeout=999,
                ),
            },
            edges=[],
            start_node="researcher",
        )
        designer = DesignerAgent()
        result = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"researcher"},
        )
        assert result.nodes["researcher"].timeout == 999  # type: ignore[union-attr]

    def test_design_without_frozen_nodes_unchanged(self) -> None:
        """Calling design_minimal() without seed/frozen params works as before."""
        designer = DesignerAgent()
        wf = designer.design_minimal("test benchmark")
        assert 3 <= len(wf.nodes) <= 4
        issues = wf.validate_graph()
        assert issues == [], f"Validation issues: {issues}"

    def test_design_minimal_preserves_frozen_data_node(self) -> None:
        """DataNode auto-frozen via _auto_frozen_nodes should be preserved."""
        from factory.workflow.primitives import DataItem, DataNode

        designer = DesignerAgent()
        seed = Workflow(
            name="seed",
            nodes={
                "positions": DataNode(
                    id="positions",
                    inline_items=[DataItem(id="pos1", prompt="test")],
                    subgraph_entry="solver",
                    subgraph_exit="solver",
                ),
                "solver": AgentNode(
                    id="solver",
                    role=AgentRole.BUILDER,
                ),
            },
            edges=[Edge(source="positions", target="solver")],
            start_node="positions",
        )
        result = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        assert "positions" in result.nodes
        assert type(result.nodes["positions"]).__name__ == "DataNode"

    def test_engine_designer_includes_auto_frozen_data_nodes(self) -> None:
        """_add_designer_variants should include auto-frozen DataNodes."""
        from factory.outer_loop.engine import _auto_frozen_nodes
        from factory.workflow.primitives import DataItem, DataNode

        seed = Workflow(
            name="seed",
            nodes={
                "positions": DataNode(
                    id="positions",
                    inline_items=[DataItem(id="pos1", prompt="test")],
                    subgraph_entry="solver",
                    subgraph_exit="solver",
                ),
                "solver": AgentNode(
                    id="solver",
                    role=AgentRole.BUILDER,
                ),
            },
            edges=[Edge(source="positions", target="solver")],
            start_node="positions",
        )

        # Verify _auto_frozen_nodes detects the DataNode
        auto_frozen = _auto_frozen_nodes(seed)
        assert "positions" in auto_frozen


class TestDataNodeRewiring:
    """Tests that frozen DataNodes are properly wired into designer templates."""

    @staticmethod
    def _seed_with_data_node() -> Workflow:
        """Seed workflow containing a DataNode with subgraph refs to 'solver'."""
        return Workflow(
            name="seed",
            nodes={
                "positions": DataNode(
                    id="positions",
                    inline_items=[DataItem(id="pos1", prompt="test")],
                    subgraph_entry="solver",
                    subgraph_exit="solver",
                ),
                "solver": AgentNode(
                    id="solver",
                    role=AgentRole.BUILDER,
                ),
            },
            edges=[Edge(source="positions", target="solver")],
            start_node="positions",
        )

    def test_minimal_start_node_is_data_node(self) -> None:
        """Designer variant with DataNode has start_node == DataNode ID."""
        designer = DesignerAgent()
        seed = self._seed_with_data_node()
        wf = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        assert wf.start_node == "positions"

    def test_minimal_subgraph_entry_points_to_template_start(self) -> None:
        """DataNode.subgraph_entry points to the template's original start."""
        designer = DesignerAgent()
        seed = self._seed_with_data_node()
        wf = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        data_node = wf.nodes["positions"]
        assert isinstance(data_node, DataNode)
        assert data_node.subgraph_entry == "researcher"

    def test_minimal_subgraph_exit_points_to_terminal(self) -> None:
        """DataNode.subgraph_exit points to template's terminal node."""
        designer = DesignerAgent()
        seed = self._seed_with_data_node()
        wf = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        data_node = wf.nodes["positions"]
        assert isinstance(data_node, DataNode)
        assert data_node.subgraph_exit == "gate_qa"

    def test_minimal_no_explicit_edge_from_data_node_to_entry(self) -> None:
        """No explicit edge from DataNode to subgraph_entry (executor uses subgraph_entry directly)."""
        designer = DesignerAgent()
        seed = self._seed_with_data_node()
        wf = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        edge_pairs = [(e.source, e.target) for e in wf.edges]
        assert ("positions", "researcher") not in edge_pairs

    def test_minimal_without_data_node_unchanged(self) -> None:
        """Designer without frozen DataNode retains original start_node."""
        designer = DesignerAgent()
        wf = designer.design_minimal("bench")
        assert wf.start_node == "researcher"
        edge_sources = {e.source for e in wf.edges}
        assert "positions" not in edge_sources

    def test_thorough_start_node_is_data_node(self) -> None:
        """design_thorough variant with DataNode has start_node == DataNode ID."""
        designer = DesignerAgent()
        seed = self._seed_with_data_node()
        wf = designer.design_thorough(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        assert wf.start_node == "positions"
        data_node = wf.nodes["positions"]
        assert isinstance(data_node, DataNode)
        assert data_node.subgraph_entry == "study"
        assert data_node.subgraph_exit == "gate_qa"
        # No explicit edge from DataNode to subgraph_entry
        edge_pairs = [(e.source, e.target) for e in wf.edges]
        assert ("positions", "study") not in edge_pairs

    def test_custom_start_node_is_data_node(self) -> None:
        """design_custom variant with DataNode has start_node == DataNode ID."""
        designer = DesignerAgent()
        seed = self._seed_with_data_node()
        wf = designer.design_custom(
            "bench",
            {"max_nodes": 6},
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        assert wf.start_node == "positions"
        data_node = wf.nodes["positions"]
        assert isinstance(data_node, DataNode)
        assert data_node.subgraph_entry == "researcher"
        assert data_node.subgraph_exit == "gate_qa"
        # No explicit edge from DataNode to subgraph_entry
        edge_pairs = [(e.source, e.target) for e in wf.edges]
        assert ("positions", "researcher") not in edge_pairs

    def test_rewired_workflow_validates_graph(self) -> None:
        """Rewired workflow with DataNode passes validate_graph() without structural issues."""
        designer = DesignerAgent()
        seed = self._seed_with_data_node()
        wf = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        issues = wf.validate_graph()
        # Injected subgraph nodes become dead after rewiring — unreachable
        # warnings are expected and safe.
        structural = [
            i for i in issues
            if "unreachable from start_node" not in i
        ]
        assert structural == [], f"Validation issues: {structural}"

    def test_data_node_id_collision_with_start(self) -> None:
        """DataNode ID == template start_node must not create self-referential subgraph_entry."""
        designer = DesignerAgent()
        # Create a seed where the DataNode ID is 'researcher' — same as
        # the minimal template's start_node.
        seed = Workflow(
            name="seed",
            nodes={
                "researcher": DataNode(
                    id="researcher",
                    inline_items=[DataItem(id="pos1", prompt="test")],
                    subgraph_entry="solver",
                    subgraph_exit="solver",
                ),
                "solver": AgentNode(
                    id="solver",
                    role=AgentRole.BUILDER,
                ),
            },
            edges=[Edge(source="researcher", target="solver")],
            start_node="researcher",
        )
        wf = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"researcher"},
        )
        data_node = wf.nodes["researcher"]
        assert isinstance(data_node, DataNode)
        # subgraph_entry must NOT be 'researcher' (self-reference)
        assert data_node.subgraph_entry != "researcher"
        # It should point to the first node reachable from original start via edges
        assert data_node.subgraph_entry == "builder"
        # No structural issues (cycle, double-execution edge, unreachable).
        # Data dependency warnings are expected since the DataNode replaced
        # the researcher that would normally write the file.
        # Injected subgraph nodes become dead after rewiring — unreachable
        # warnings are expected and safe.
        issues = wf.validate_graph()
        structural = [
            i for i in issues
            if "no predecessor writes" not in i
            and "unreachable from start_node" not in i
        ]
        assert structural == [], f"Structural issues: {structural}"


class TestInjectFrozenDataNodeSubgraph:
    """Tests for DataNode subgraph injection in _inject_frozen_nodes."""

    @staticmethod
    def _seed_with_multi_node_subgraph() -> Workflow:
        """Seed with DataNode whose subgraph spans generator → processor → validator."""
        return Workflow(
            name="seed",
            nodes={
                "positions": DataNode(
                    id="positions",
                    inline_items=[DataItem(id="pos1", prompt="test")],
                    subgraph_entry="generator",
                    subgraph_exit="validator",
                ),
                "generator": AgentNode(
                    id="generator",
                    role=AgentRole.BUILDER,
                    timeout=300,
                ),
                "processor": AgentNode(
                    id="processor",
                    role=AgentRole.RESEARCHER,
                    timeout=300,
                ),
                "validator": AgentNode(
                    id="validator",
                    role=AgentRole.CODE_REVIEWER,
                    timeout=300,
                ),
            },
            edges=[
                Edge(source="positions", target="generator"),
                Edge(source="generator", target="processor"),
                Edge(source="processor", target="validator"),
            ],
            start_node="positions",
        )

    def test_inject_frozen_data_node_includes_subgraph(self) -> None:
        """Freezing a DataNode injects all subgraph nodes and edges."""
        designer = DesignerAgent()
        seed = self._seed_with_multi_node_subgraph()
        result = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        # All 3 subgraph nodes should be present
        assert "generator" in result.nodes
        assert "processor" in result.nodes
        assert "validator" in result.nodes

        # Both subgraph-internal edges should be present
        edge_pairs = [(e.source, e.target) for e in result.edges]
        assert ("generator", "processor") in edge_pairs
        assert ("processor", "validator") in edge_pairs

    def test_inject_frozen_data_node_no_duplicate_nodes(self) -> None:
        """Freezing both DataNode and a subgraph node doesn't duplicate nodes."""
        designer = DesignerAgent()
        seed = self._seed_with_multi_node_subgraph()
        result = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions", "generator"},
        )
        # Each node should appear exactly once
        node_ids = list(result.nodes.keys())
        assert node_ids.count("generator") == 1
        assert node_ids.count("processor") == 1
        assert node_ids.count("validator") == 1

    def test_inject_frozen_data_node_no_duplicate_edges(self) -> None:
        """Subgraph edges already in the template are not duplicated."""
        designer = DesignerAgent()
        # Seed where subgraph has edge (researcher → builder) which is also
        # in the minimal template
        seed = Workflow(
            name="seed",
            nodes={
                "positions": DataNode(
                    id="positions",
                    inline_items=[DataItem(id="pos1", prompt="test")],
                    subgraph_entry="researcher",
                    subgraph_exit="builder",
                ),
                "researcher": AgentNode(
                    id="researcher",
                    role=AgentRole.RESEARCHER,
                    timeout=300,
                ),
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    timeout=600,
                ),
            },
            edges=[
                Edge(source="positions", target="researcher"),
                Edge(source="researcher", target="builder"),
            ],
            start_node="positions",
        )
        result = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        # (researcher, builder) edge should appear only once
        matching = [
            e for e in result.edges
            if e.source == "researcher" and e.target == "builder" and e.condition is None
        ]
        assert len(matching) == 1

    def test_inject_frozen_data_node_missing_subgraph_entry(self) -> None:
        """DataNode with missing subgraph_entry logs warning and skips expansion."""
        designer = DesignerAgent()
        seed = Workflow(
            name="seed",
            nodes={
                "positions": DataNode(
                    id="positions",
                    inline_items=[DataItem(id="pos1", prompt="test")],
                    subgraph_entry="missing",
                    subgraph_exit="validator",
                ),
                "validator": AgentNode(
                    id="validator",
                    role=AgentRole.CODE_REVIEWER,
                    timeout=300,
                ),
            },
            edges=[],
            start_node="positions",
        )
        # Should not crash
        result = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        # DataNode is still injected (it was already copied)
        assert "positions" in result.nodes
        # But subgraph node "missing" was not injected (it doesn't exist)
        assert "missing" not in result.nodes

    def test_inject_frozen_data_node_validates(self) -> None:
        """Variant with multi-node DataNode subgraph passes validation."""
        designer = DesignerAgent()
        seed = self._seed_with_multi_node_subgraph()
        result = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        issues = result.validate_graph()
        # Filter out data dependency warnings and unreachable-node warnings.
        # Injected subgraph nodes become dead after _rewire_data_nodes
        # rewires entry/exit to template nodes — this is expected and safe.
        structural = [
            i for i in issues
            if "no predecessor writes" not in i
            and "unreachable from start_node" not in i
        ]
        assert structural == [], f"Structural issues: {structural}"


class TestPromptPropagation:
    """Tests for _propagate_prompts_from_seed (Part C)."""

    @staticmethod
    def _seed_with_prompts() -> Workflow:
        """Seed workflow with prompt_templates for RESEARCHER and BUILDER."""
        return Workflow(
            name="seed",
            nodes={
                "researcher": AgentNode(
                    id="researcher",
                    role=AgentRole.RESEARCHER,
                    prompt_template="Research the project deeply.",
                    writes={".factory/strategy/research.md"},
                ),
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    prompt_template="Build the solution carefully.",
                    reads={".factory/strategy/research.md"},
                ),
            },
            edges=[Edge(source="researcher", target="builder")],
            start_node="researcher",
        )

    def test_propagation_fills_empty_prompts(self) -> None:
        """Template nodes with empty prompt get the seed's prompt by role."""
        nodes: dict[str, AgentNode | FnNode] = {
            "r": AgentNode(id="r", role=AgentRole.RESEARCHER),
            "b": AgentNode(id="b", role=AgentRole.BUILDER),
        }
        seed = self._seed_with_prompts()
        _propagate_prompts_from_seed(nodes, seed)  # type: ignore[arg-type]
        assert nodes["r"].prompt_template == "Research the project deeply."  # type: ignore[union-attr]
        assert nodes["b"].prompt_template == "Build the solution carefully."  # type: ignore[union-attr]

    def test_noop_when_seed_is_none(self) -> None:
        """No crash and no changes when seed_workflow is None."""
        nodes: dict[str, AgentNode] = {
            "r": AgentNode(id="r", role=AgentRole.RESEARCHER),
        }
        _propagate_prompts_from_seed(nodes, None)  # type: ignore[arg-type]
        assert nodes["r"].prompt_template == ""

    def test_unmatched_role_stays_empty(self) -> None:
        """Roles not in seed keep empty prompt (for Part E to fill)."""
        nodes: dict[str, AgentNode] = {
            "s": AgentNode(id="s", role=AgentRole.STRATEGIST),
        }
        seed = self._seed_with_prompts()
        _propagate_prompts_from_seed(nodes, seed)  # type: ignore[arg-type]
        assert nodes["s"].prompt_template == ""

    def test_multiple_same_role_all_receive_prompt(self) -> None:
        """Multiple nodes with the same role all get the seed's prompt."""
        nodes: dict[str, AgentNode] = {
            "b1": AgentNode(id="b1", role=AgentRole.BUILDER),
            "b2": AgentNode(id="b2", role=AgentRole.BUILDER),
        }
        seed = self._seed_with_prompts()
        _propagate_prompts_from_seed(nodes, seed)  # type: ignore[arg-type]
        assert nodes["b1"].prompt_template == "Build the solution carefully."
        assert nodes["b2"].prompt_template == "Build the solution carefully."

    def test_existing_prompt_not_overwritten(self) -> None:
        """Nodes that already have a prompt are left alone."""
        nodes: dict[str, AgentNode] = {
            "r": AgentNode(
                id="r", role=AgentRole.RESEARCHER,
                prompt_template="My custom prompt.",
            ),
        }
        seed = self._seed_with_prompts()
        _propagate_prompts_from_seed(nodes, seed)  # type: ignore[arg-type]
        assert nodes["r"].prompt_template == "My custom prompt."

    def test_design_minimal_with_seed_has_prompts(self) -> None:
        """design_minimal with a seed produces AgentNodes with non-empty prompts."""
        designer = DesignerAgent()
        seed = self._seed_with_prompts()
        wf = designer.design_minimal("bench", seed_workflow=seed)
        for node in wf.nodes.values():
            if type(node).__name__ == "AgentNode":
                assert node.prompt_template, f"Node {node.id} has empty prompt"  # type: ignore[union-attr]

    def test_design_thorough_with_seed_has_prompts(self) -> None:
        """design_thorough with a seed produces AgentNodes with non-empty prompts."""
        designer = DesignerAgent()
        seed = self._seed_with_prompts()
        wf = designer.design_thorough("bench", seed_workflow=seed)
        for node in wf.nodes.values():
            if type(node).__name__ == "AgentNode":
                assert node.prompt_template, f"Node {node.id} has empty prompt"  # type: ignore[union-attr]

    def test_design_custom_with_seed_has_prompts(self) -> None:
        """design_custom with a seed produces AgentNodes with non-empty prompts."""
        designer = DesignerAgent()
        seed = self._seed_with_prompts()
        wf = designer.design_custom("bench", {"max_nodes": 6}, seed_workflow=seed)
        for node in wf.nodes.values():
            if type(node).__name__ == "AgentNode":
                assert node.prompt_template, f"Node {node.id} has empty prompt"  # type: ignore[union-attr]

    def test_frozen_node_prompt_wins_over_propagated(self) -> None:
        """Frozen node's prompt takes priority over propagated prompt."""
        seed = Workflow(
            name="seed",
            nodes={
                "researcher": AgentNode(
                    id="researcher",
                    role=AgentRole.RESEARCHER,
                    prompt_template="Frozen prompt wins.",
                    timeout=999,
                ),
            },
            edges=[],
            start_node="researcher",
        )
        designer = DesignerAgent()
        wf = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"researcher"},
        )
        assert wf.nodes["researcher"].prompt_template == "Frozen prompt wins."  # type: ignore[union-attr]


class TestGateEdgeWiring:
    """Tests for Part D gate edge fix in _rewire_data_nodes."""

    @staticmethod
    def _seed_with_data_node_and_prompts() -> Workflow:
        """Seed with DataNode + prompted AgentNodes."""
        return Workflow(
            name="seed",
            nodes={
                "positions": DataNode(
                    id="positions",
                    inline_items=[DataItem(id="pos1", prompt="test")],
                    subgraph_entry="solver",
                    subgraph_exit="solver",
                ),
                "solver": AgentNode(
                    id="solver",
                    role=AgentRole.BUILDER,
                    prompt_template="Solve the task.",
                ),
            },
            edges=[Edge(source="positions", target="solver")],
            start_node="positions",
        )

    def test_gate_exit_gets_proceed_edge(self) -> None:
        """When DataNode rewiring makes gate_qa the subgraph_exit, a PROCEED edge is added."""
        designer = DesignerAgent()
        seed = self._seed_with_data_node_and_prompts()
        wf = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        proceed_edges = [
            e for e in wf.edges
            if e.source == "gate_qa" and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) >= 1, "gate_qa should have a PROCEED edge after rewiring"

    def test_non_gate_exit_no_proceed_added(self) -> None:
        """When the terminal node is not a GateNode, no PROCEED edge is added."""
        # Create a workflow where the terminal node is a FnNode
        seed = Workflow(
            name="seed",
            nodes={
                "data": DataNode(
                    id="data",
                    inline_items=[DataItem(id="d1", prompt="test")],
                    subgraph_entry="worker",
                    subgraph_exit="worker",
                ),
                "worker": AgentNode(
                    id="worker",
                    role=AgentRole.BUILDER,
                    prompt_template="Work.",
                ),
            },
            edges=[Edge(source="data", target="worker")],
            start_node="data",
        )
        # Build a minimal workflow that has FnNode as terminal
        from factory.outer_loop.designer import _inject_frozen_nodes, _rewire_data_nodes
        nodes: dict = {
            "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
            "b": FnNode(id="b", command="echo b", reads={"a.txt"}),
        }
        edges = [Edge(source="a", target="b")]
        _inject_frozen_nodes(nodes, edges, seed, {"data"})
        _rewire_data_nodes(nodes, edges, "a", seed, {"data"})
        proceed_edges = [
            e for e in edges
            if e.condition == VerdictType.PROCEED
        ]
        assert proceed_edges == [], "No PROCEED edge should be added for non-gate terminal"

    def test_idempotent_if_proceed_exists(self) -> None:
        """If gate already has a PROCEED edge, no duplicate is added."""
        designer = DesignerAgent()
        seed = self._seed_with_data_node_and_prompts()
        wf = designer.design_minimal(
            "bench",
            seed_workflow=seed,
            frozen_node_ids={"positions"},
        )
        proceed_edges = [
            e for e in wf.edges
            if e.source == "gate_qa" and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 1, "Should have exactly one PROCEED edge"


class TestValidateAndFixFallback:
    """Tests for _validate_and_fix (Part E)."""

    def test_noop_on_valid_workflow(self) -> None:
        """Valid workflow passes through unchanged."""
        wf = Workflow(
            name="test",
            nodes={
                "a": AgentNode(
                    id="a", role=AgentRole.RESEARCHER,
                    prompt_template="Do research.",
                ),
            },
            edges=[],
            start_node="a",
        )
        result = _validate_and_fix(wf, None)
        assert result.nodes["a"].prompt_template == "Do research."  # type: ignore[union-attr]

    def test_fills_empty_prompt_with_generic_default(self) -> None:
        """Empty prompt_template gets a generic default."""
        wf = Workflow(
            name="test",
            nodes={
                "a": AgentNode(id="a", role=AgentRole.RESEARCHER),
            },
            edges=[],
            start_node="a",
        )
        result = _validate_and_fix(wf, None)
        prompt = result.nodes["a"].prompt_template  # type: ignore[union-attr]
        assert prompt, "Prompt should be filled"
        assert "researcher" in prompt
        assert "{project_path}" in prompt

    def test_adds_proceed_edge_to_non_terminal_gate(self) -> None:
        """Non-terminal gate missing PROCEED gets one added."""
        wf = Workflow(
            name="test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(id="gate", evaluator_type="fn", reads={"a.txt"}),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="a", condition=VerdictType.RELOOP),
            ],
            start_node="a",
        )
        result = _validate_and_fix(wf, None)
        proceed = [
            e for e in result.edges
            if e.source == "gate" and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed) >= 1

    def test_preserves_existing_prompts(self) -> None:
        """Non-empty prompts are never overwritten."""
        wf = Workflow(
            name="test",
            nodes={
                "a": AgentNode(
                    id="a", role=AgentRole.RESEARCHER,
                    prompt_template="Custom prompt.",
                ),
            },
            edges=[],
            start_node="a",
        )
        result = _validate_and_fix(wf, None)
        assert result.nodes["a"].prompt_template == "Custom prompt."  # type: ignore[union-attr]

    def test_idempotent(self) -> None:
        """Calling twice produces the same result."""
        wf = Workflow(
            name="test",
            nodes={
                "a": AgentNode(id="a", role=AgentRole.RESEARCHER),
            },
            edges=[],
            start_node="a",
        )
        result1 = _validate_and_fix(wf, None)
        result2 = _validate_and_fix(result1, None)
        assert result1.nodes["a"].prompt_template == result2.nodes["a"].prompt_template  # type: ignore[union-attr]
