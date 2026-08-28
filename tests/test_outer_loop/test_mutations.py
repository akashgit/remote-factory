"""Tests for mutation operators and MutationStrategy."""

from __future__ import annotations


from factory.outer_loop.models import MutationType
from factory.outer_loop.mutations import (
    MutationStrategy,
    WeightedRandomStrategy,
    apply_random_mutation,
    insert_node,
    mutate_knob,
    mutate_params,
    parallelize,
    redirect_edge,
    remove_node,
    serialize,
    validate_and_repair,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    Workflow,
)


class TestInsertNode:
    def test_insert_between_nodes(self, simple_workflow: Workflow) -> None:
        new_node = AgentNode(id="reviewer", role=AgentRole.CODE_REVIEWER)
        result = insert_node(simple_workflow, new_node, "strategist")
        assert result is not None
        wf, rec = result
        assert "reviewer" in wf.nodes
        assert rec.operator == MutationType.NODE_INSERT

    def test_insert_respects_frozen(self, simple_workflow: Workflow) -> None:
        new_node = AgentNode(id="new", role=AgentRole.RESEARCHER)
        result = insert_node(
            simple_workflow, new_node, "researcher", frozen_nodes={"researcher"}
        )
        assert result is None

    def test_insert_after_nonexistent(self, simple_workflow: Workflow) -> None:
        new_node = AgentNode(id="new", role=AgentRole.RESEARCHER)
        result = insert_node(simple_workflow, new_node, "nonexistent")
        assert result is None


class TestRemoveNode:
    def test_remove_middle_node(self, simple_workflow: Workflow) -> None:
        result = remove_node(simple_workflow, "strategist")
        assert result is not None
        wf, rec = result
        assert "strategist" not in wf.nodes
        assert rec.operator == MutationType.NODE_REMOVE
        has_edge = any(
            e.source == "researcher" and e.target == "builder" for e in wf.edges
        )
        assert has_edge

    def test_remove_start_node_fails(self, simple_workflow: Workflow) -> None:
        result = remove_node(simple_workflow, "study")
        assert result is None

    def test_remove_frozen_fails(self, simple_workflow: Workflow) -> None:
        result = remove_node(simple_workflow, "builder", frozen_nodes={"builder"})
        assert result is None


class TestRedirectEdge:
    def test_redirect_edge(self, simple_workflow: Workflow) -> None:
        result = redirect_edge(simple_workflow, "researcher", "strategist", "builder")
        assert result is not None
        wf, rec = result
        assert rec.operator == MutationType.EDGE_REDIRECT
        has_new = any(
            e.source == "researcher" and e.target == "builder" for e in wf.edges
        )
        assert has_new

    def test_redirect_nonexistent_target(self, simple_workflow: Workflow) -> None:
        result = redirect_edge(simple_workflow, "researcher", "strategist", "nonexistent")
        assert result is None

    def test_redirect_frozen_source(self, simple_workflow: Workflow) -> None:
        result = redirect_edge(
            simple_workflow, "researcher", "strategist", "builder",
            frozen_nodes={"researcher"},
        )
        assert result is None


class TestParallelize:
    def test_parallelize_two_nodes(self, simple_workflow: Workflow) -> None:
        result = parallelize(simple_workflow, ["researcher", "strategist"])
        assert result is not None
        wf, rec = result
        assert rec.operator == MutationType.PARALLELIZE
        fork_nodes = [nid for nid, n in wf.nodes.items() if type(n).__name__ == "ForkNode"]
        join_nodes = [nid for nid, n in wf.nodes.items() if type(n).__name__ == "JoinNode"]
        assert len(fork_nodes) >= 1
        assert len(join_nodes) >= 1

    def test_parallelize_single_node_fails(self, simple_workflow: Workflow) -> None:
        result = parallelize(simple_workflow, ["researcher"])
        assert result is None

    def test_parallelize_frozen_fails(self, simple_workflow: Workflow) -> None:
        result = parallelize(
            simple_workflow, ["researcher", "strategist"],
            frozen_nodes={"researcher"},
        )
        assert result is None


class TestSerialize:
    def test_serialize_reverses_parallelize(self, simple_workflow: Workflow) -> None:
        par_result = parallelize(simple_workflow, ["researcher", "strategist"])
        assert par_result is not None
        wf_par, _ = par_result

        fork_ids = [nid for nid, n in wf_par.nodes.items() if type(n).__name__ == "ForkNode"]
        assert len(fork_ids) >= 1

        ser_result = serialize(wf_par, fork_ids[0])
        assert ser_result is not None
        wf_ser, rec = ser_result
        assert rec.operator == MutationType.SERIALIZE
        assert not any(type(n).__name__ == "ForkNode" for n in wf_ser.nodes.values())

    def test_serialize_nonexistent_fails(self, simple_workflow: Workflow) -> None:
        result = serialize(simple_workflow, "nonexistent")
        assert result is None

    def test_serialize_non_fork_fails(self, simple_workflow: Workflow) -> None:
        result = serialize(simple_workflow, "researcher")
        assert result is None


class TestMutateParams:
    def test_change_timeout(self, simple_workflow: Workflow) -> None:
        result = mutate_params(simple_workflow, "researcher", {"timeout": 1200})
        assert result is not None
        wf, rec = result
        assert rec.operator == MutationType.PARAM_MUTATE
        node = wf.nodes["researcher"]
        assert hasattr(node, "timeout")
        assert node.timeout == 1200  # type: ignore[union-attr]

    def test_change_model(self, simple_workflow: Workflow) -> None:
        result = mutate_params(simple_workflow, "researcher", {"model": "opus"})
        assert result is not None
        wf, _ = result
        assert wf.nodes["researcher"].model == "opus"  # type: ignore[union-attr]

    def test_disallowed_param_ignored(self, simple_workflow: Workflow) -> None:
        result = mutate_params(simple_workflow, "researcher", {"role": "builder"})
        assert result is None

    def test_frozen_fails(self, simple_workflow: Workflow) -> None:
        result = mutate_params(
            simple_workflow, "researcher", {"timeout": 900},
            frozen_nodes={"researcher"},
        )
        assert result is None


class TestValidateAndRepair:
    def test_valid_workflow_passes(self, simple_workflow: Workflow) -> None:
        result = validate_and_repair(simple_workflow)
        assert result is not None

    def test_prunes_unreachable(self) -> None:
        nodes = {
            "start": FnNode(id="start", command="echo start"),
            "reachable": FnNode(id="reachable", command="echo r"),
            "orphan": FnNode(id="orphan", command="echo orphan"),
        }
        edges = [Edge(source="start", target="reachable")]
        wf = Workflow(name="test", nodes=nodes, edges=edges, start_node="start")
        result = validate_and_repair(wf)
        assert result is not None
        assert "orphan" not in result.nodes

    def test_cycle_without_gate_returns_none(self) -> None:
        nodes = {
            "a": FnNode(id="a", command="echo a"),
            "b": FnNode(id="b", command="echo b"),
        }
        edges = [
            Edge(source="a", target="b"),
            Edge(source="b", target="a"),
        ]
        wf = Workflow(name="test", nodes=nodes, edges=edges, start_node="a")
        result = validate_and_repair(wf)
        assert result is None


class TestWeightedRandomStrategy:
    def test_implements_protocol(self) -> None:
        strategy = WeightedRandomStrategy()
        assert isinstance(strategy, MutationStrategy)

    def test_select_operator_returns_valid(self, simple_workflow: Workflow) -> None:
        strategy = WeightedRandomStrategy()
        op = strategy.select_operator(simple_workflow, 0, {})
        assert isinstance(op, MutationType)

    def test_mutation_rate(self) -> None:
        strategy = WeightedRandomStrategy(mutation_rate=0.5)
        assert strategy.get_mutation_rate(0) == 0.5
        assert strategy.get_mutation_rate(10) == 0.5

    def test_designer_ratio(self) -> None:
        strategy = WeightedRandomStrategy(designer_ratio=0.4)
        assert strategy.get_designer_ratio(0) == 0.4

    def test_operator_weights(self) -> None:
        weights = {t.value: (1.0 if t == MutationType.NODE_INSERT else 0.0) for t in MutationType}
        strategy = WeightedRandomStrategy(weights=weights)
        ops = [strategy.select_operator(Workflow(
            name="dummy",
            nodes={"a": FnNode(id="a", command="x")},
            edges=[],
            start_node="a",
        ), 0, {}) for _ in range(20)]
        assert all(op == MutationType.NODE_INSERT for op in ops)


class TestApplyRandomMutation:
    def test_produces_valid_result(self, simple_workflow: Workflow) -> None:
        strategy = WeightedRandomStrategy()
        result = apply_random_mutation(
            simple_workflow, strategy, generation=0, max_attempts=20,
        )
        if result is not None:
            wf, rec = result
            assert isinstance(rec.operator, MutationType)
            assert wf.start_node in wf.nodes

    def test_with_frozen_nodes(self, simple_workflow: Workflow) -> None:
        strategy = WeightedRandomStrategy()
        all_nodes = set(simple_workflow.nodes.keys())
        result = apply_random_mutation(
            simple_workflow, strategy, generation=0,
            frozen_nodes=all_nodes,
            max_attempts=5,
        )
        assert result is None


class TestKnobMutate:
    def test_mutates_knob_within_bounds(self, simple_workflow: Workflow) -> None:
        wf = simple_workflow.model_copy(update={
            "knob_values": {"style": "broad"},
            "knob_bounds": {"style": ["broad", "focused", "deep"]},
        })
        result = mutate_knob(wf, expander=None)
        assert result is not None
        child_wf, rec = result
        assert rec.operator == MutationType.KNOB_MUTATE
        assert child_wf.knob_values["style"] != "broad"
        assert child_wf.knob_values["style"] in ["focused", "deep"]

    def test_returns_none_without_knob_values(self, simple_workflow: Workflow) -> None:
        result = mutate_knob(simple_workflow, expander=None)
        assert result is None

    def test_calls_expander_when_bounds_exhausted(self, simple_workflow: Workflow) -> None:
        wf = simple_workflow.model_copy(update={
            "knob_values": {"style": "only_option"},
            "knob_bounds": {"style": ["only_option"]},
            "knob_expandable": {"style": "invent a new style"},
        })
        expanded_value = None
        def fake_expander(name, hint, current, bounds):
            nonlocal expanded_value
            expanded_value = "invented_style"
            return "invented_style"
        result = mutate_knob(wf, expander=fake_expander)
        assert result is not None
        child_wf, rec = result
        assert child_wf.knob_values["style"] == "invented_style"
        assert "invented_style" in child_wf.knob_bounds["style"]

    def test_expander_not_called_when_not_expandable(self, simple_workflow: Workflow) -> None:
        wf = simple_workflow.model_copy(update={
            "knob_values": {"style": "only_option"},
            "knob_bounds": {"style": ["only_option"]},
        })
        called = False
        def spy_expander(name, hint, current, bounds):
            nonlocal called
            called = True
            return "should_not_appear"
        result = mutate_knob(wf, expander=spy_expander)
        assert not called


class TestKnobPreservation:
    def test_insert_node_preserves_knobs(self, simple_workflow: Workflow) -> None:
        wf = simple_workflow.model_copy(update={
            "knob_values": {"style": "broad", "depth": 3.0},
            "knob_bounds": {"style": ["broad", "deep"], "depth": [1.0, 5.0]},
            "knob_expandable": {"style": "prompt hint"},
        })
        new_node = AgentNode(id="new_agent", role=AgentRole.RESEARCHER)
        result = insert_node(wf, new_node, "strategist")
        assert result is not None
        child_wf, _ = result
        assert child_wf.knob_values == {"style": "broad", "depth": 3.0}
        assert child_wf.knob_bounds == {"style": ["broad", "deep"], "depth": [1.0, 5.0]}
        assert child_wf.knob_expandable == {"style": "prompt hint"}

    def test_remove_node_preserves_knobs(self, simple_workflow: Workflow) -> None:
        wf = simple_workflow.model_copy(update={
            "knob_values": {"mode": "parallel"},
            "knob_bounds": {"mode": ["parallel", "serial"]},
        })
        result = remove_node(wf, "strategist")
        assert result is not None
        child_wf, _ = result
        assert child_wf.knob_values == {"mode": "parallel"}
