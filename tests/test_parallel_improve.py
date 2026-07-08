"""Tests for the parallel experiment execution workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from factory.models import ExperimentRecord, FactoryConfig, ParallelConfig
from factory.workflow.definitions import parallel_improve_workflow, register_all
from factory.workflow.executor import (
    WorkflowExecutor,
    _collect_subgraph_nodes,
    _parse_hypotheses,
)
from factory.workflow.primitives import (
    Edge,
    FnNode,
    SelectionNode,
    SubgraphForkNode,
    Workflow,
)


# ── ParallelConfig model tests ──────────────────────────────────


class TestParallelConfig:
    def test_defaults(self) -> None:
        config = ParallelConfig()
        assert config.parallel_hypotheses == 1
        assert config.selection_strategy == "best_score"

    def test_custom_values(self) -> None:
        config = ParallelConfig(parallel_hypotheses=4, selection_strategy="best_score")
        assert config.parallel_hypotheses == 4

    def test_max_hypotheses(self) -> None:
        config = ParallelConfig(parallel_hypotheses=8)
        assert config.parallel_hypotheses == 8

    def test_exceeds_max(self) -> None:
        with pytest.raises(ValidationError):
            ParallelConfig(parallel_hypotheses=9)

    def test_zero_invalid(self) -> None:
        with pytest.raises(ValidationError):
            ParallelConfig(parallel_hypotheses=0)

    def test_negative_invalid(self) -> None:
        with pytest.raises(ValidationError):
            ParallelConfig(parallel_hypotheses=-1)

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ParallelConfig(unknown_field="x")  # type: ignore[call-arg]


class TestFactoryConfigParallel:
    def test_parallel_none_by_default(self) -> None:
        config = FactoryConfig(
            goal="test", scope=[], guards=[], eval_command="echo 1",
            eval_threshold=0.5, constraints=[],
        )
        assert config.parallel is None

    def test_parallel_config_accepted(self) -> None:
        config = FactoryConfig(
            goal="test", scope=[], guards=[], eval_command="echo 1",
            eval_threshold=0.5, constraints=[],
            parallel=ParallelConfig(parallel_hypotheses=3),
        )
        assert config.parallel is not None
        assert config.parallel.parallel_hypotheses == 3


class TestSupersededVerdict:
    def test_superseded_valid(self) -> None:
        from datetime import datetime, timezone
        record = ExperimentRecord(
            id=1, timestamp=datetime.now(tz=timezone.utc),
            hypothesis="test", change_summary="superseded",
            issue_number=None, pr_number=None,
            score_before=0.5, score_after=0.6, delta=0.1,
            verdict="superseded", cost_usd=None, notes="",
        )
        assert record.verdict == "superseded"


# ── Primitive node type tests ────────────────────────────────────


class TestSubgraphForkNode:
    def test_basic(self) -> None:
        node = SubgraphForkNode(
            id="fork", subgraph_entry="begin", subgraph_exit="eval",
        )
        assert node.subgraph_entry == "begin"
        assert node.subgraph_exit == "eval"
        assert node.parallelism == 3
        assert node.worktree_isolated is True

    def test_custom_parallelism(self) -> None:
        node = SubgraphForkNode(
            id="fork", subgraph_entry="a", subgraph_exit="b",
            parallelism=5,
        )
        assert node.parallelism == 5

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SubgraphForkNode(
                id="fork", subgraph_entry="a", subgraph_exit="b",
                unknown=True,  # type: ignore[call-arg]
            )


class TestSelectionNode:
    def test_basic(self) -> None:
        node = SelectionNode(id="select")
        assert node.strategy == "best_score"

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SelectionNode(id="select", unknown=True)  # type: ignore[call-arg]


# ── Workflow definition tests ────────────────────────────────────


class TestParallelImproveWorkflow:
    def test_valid_graph(self) -> None:
        wf = parallel_improve_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"parallel-improve workflow has issues: {issues}"

    def test_name(self) -> None:
        wf = parallel_improve_workflow()
        assert wf.name == "parallel-improve"

    def test_start_node(self) -> None:
        wf = parallel_improve_workflow()
        assert wf.start_node == "study"

    def test_has_subgraph_fork(self) -> None:
        wf = parallel_improve_workflow()
        fork_nodes = [
            n for n in wf.nodes.values()
            if isinstance(n, SubgraphForkNode)
        ]
        assert len(fork_nodes) == 1
        assert fork_nodes[0].id == "fork_experiments"

    def test_has_selection_node(self) -> None:
        wf = parallel_improve_workflow()
        sel_nodes = [
            n for n in wf.nodes.values()
            if isinstance(n, SelectionNode)
        ]
        assert len(sel_nodes) == 1
        assert sel_nodes[0].id == "select_best"

    def test_registered(self) -> None:
        workflows = register_all()
        assert "parallel-improve" in workflows

    def test_trigger(self) -> None:
        from factory.models import ProjectState
        wf = parallel_improve_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "parallel-improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.NO_REPO, {"mode": "parallel-improve"})


# ── Helper function tests ────────────────────────────────────────


class TestParseHypotheses:
    def test_heading_format(self, tmp_path: Path) -> None:
        f = tmp_path / "current.md"
        f.write_text(
            "## Hypothesis 1\nAdd caching\n\n"
            "## Hypothesis 2\nRefactor auth\n"
        )
        result = _parse_hypotheses(f)
        assert len(result) == 2
        assert "caching" in result[0].lower()
        assert "auth" in result[1].lower()

    def test_bullet_fallback(self, tmp_path: Path) -> None:
        f = tmp_path / "current.md"
        f.write_text("- **Add caching** to API\n- **Refactor auth** module\n")
        result = _parse_hypotheses(f)
        assert len(result) == 2

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "current.md"
        f.write_text("")
        result = _parse_hypotheses(f)
        assert result == []


class TestCollectSubgraphNodes:
    def test_linear_subgraph(self) -> None:
        wf = Workflow(
            name="test",
            nodes={
                "pre": FnNode(id="pre", writes={"a"}),
                "a": FnNode(id="a", writes={"b"}),
                "b": FnNode(id="b", reads={"b"}, writes={"c"}),
                "c": FnNode(id="c", reads={"c"}, writes={"d"}),
                "post": FnNode(id="post", reads={"d"}),
            },
            edges=[
                Edge(source="pre", target="a"),
                Edge(source="a", target="b"),
                Edge(source="b", target="c"),
                Edge(source="c", target="post"),
            ],
            start_node="pre",
        )
        result = _collect_subgraph_nodes(wf, "a", "c")
        assert result == {"a", "b", "c"}

    def test_single_node(self) -> None:
        wf = Workflow(
            name="test",
            nodes={
                "a": FnNode(id="a", writes={"x"}),
                "b": FnNode(id="b", reads={"x"}),
            },
            edges=[Edge(source="a", target="b")],
            start_node="a",
        )
        result = _collect_subgraph_nodes(wf, "a", "a")
        assert result == {"a"}


# ── Executor dry-run tests ───────────────────────────────────────


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "strategy").mkdir()
    (factory_dir / "reviews").mkdir()
    (factory_dir / "experiments").mkdir()
    (factory_dir / "archive").mkdir()
    return tmp_path


class TestSubgraphForkDryRun:
    async def test_dry_run_subgraph_fork(self, tmp_project: Path) -> None:
        wf = Workflow(
            name="test-parallel",
            nodes={
                "pre": FnNode(id="pre", command="echo pre", writes={"pre.txt"}),
                "fork": SubgraphForkNode(
                    id="fork",
                    subgraph_entry="step_a",
                    subgraph_exit="step_b",
                    parallelism=2,
                    reads={"pre.txt"},
                    writes={"fork_result.json"},
                ),
                "step_a": FnNode(id="step_a", writes={"a.txt"}),
                "step_b": FnNode(id="step_b", reads={"a.txt"}, writes={"b.txt"}),
                "post": FnNode(id="post", reads={"fork_result.json"}, writes={"done.txt"}),
            },
            edges=[
                Edge(source="pre", target="fork"),
                Edge(source="step_a", target="step_b"),
                Edge(source="fork", target="post"),
            ],
            start_node="pre",
        )

        # Write strategy file so hypotheses can be parsed
        strategy_dir = tmp_project / ".factory" / "strategy"
        (strategy_dir / "current.md").write_text(
            "## Hypothesis 1\nAdd caching\n\n## Hypothesis 2\nRefactor\n"
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()

        assert result.success
        assert "fork" in result.node_outputs

    async def test_dry_run_selection(self, tmp_project: Path) -> None:
        wf = Workflow(
            name="test-select",
            nodes={
                "pre": FnNode(id="pre", command="echo pre", writes={"pre.txt"}),
                "select": SelectionNode(
                    id="select",
                    reads={"pre.txt"},
                    writes={"result.json"},
                ),
            },
            edges=[Edge(source="pre", target="select")],
            start_node="pre",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()

        assert result.success
        assert "select" in result.node_outputs
        selection = json.loads(result.node_outputs["select"])
        assert selection["strategy"] == "best_score"
        assert selection["winner"] is None


# ── Checkpoint tests ─────────────────────────────────────────────


class TestCheckpointParallelFields:
    def test_new_fields_default(self) -> None:
        from factory.checkpoint import CheckpointState
        state = CheckpointState(
            mode="parallel-improve",
            active_experiment_id=None,
            completed_agents=[],
            pending_agents=[],
            last_eval_scores={},
            current_hypothesis=None,
            timestamp="2026-01-01T00:00:00Z",
        )
        assert state.active_experiment_ids == []
        assert state.parallel_branch_status == {}

    def test_with_parallel_fields(self) -> None:
        from factory.checkpoint import CheckpointState, format_checkpoint
        state = CheckpointState(
            mode="parallel-improve",
            active_experiment_id=None,
            active_experiment_ids=[1, 2, 3],
            completed_agents=[],
            pending_agents=[],
            last_eval_scores={},
            current_hypothesis=None,
            parallel_branch_status={"1": "running", "2": "completed", "3": "failed"},
            timestamp="2026-01-01T00:00:00Z",
        )
        assert state.active_experiment_ids == [1, 2, 3]
        formatted = format_checkpoint(state)
        assert "Parallel exps" in formatted
        assert "Branch status" in formatted
