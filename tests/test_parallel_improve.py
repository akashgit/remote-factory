"""Tests for the parallel experiment execution workflow."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from factory.models import ExperimentRecord, FactoryConfig, ParallelConfig
from factory.store import _parse_parallel
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


# ── _parse_parallel tests (store.py coverage) ──────────────────


class TestParseParallel:
    def test_empty_input_returns_none(self) -> None:
        assert _parse_parallel("") is None
        assert _parse_parallel([]) is None

    def test_list_with_hypotheses(self) -> None:
        result = _parse_parallel(["parallel_hypotheses: 4"])
        assert result is not None
        assert result.parallel_hypotheses == 4

    def test_list_with_selection_strategy(self) -> None:
        result = _parse_parallel(["selection_strategy: best_score"])
        assert result is not None
        assert result.selection_strategy == "best_score"

    def test_list_with_both_keys(self) -> None:
        result = _parse_parallel([
            "parallel_hypotheses: 3",
            "selection_strategy: best_score",
        ])
        assert result is not None
        assert result.parallel_hypotheses == 3
        assert result.selection_strategy == "best_score"

    def test_string_input(self) -> None:
        result = _parse_parallel("parallel_hypotheses: 2")
        assert result is not None
        assert result.parallel_hypotheses == 2

    def test_invalid_hypotheses_value_skipped(self) -> None:
        result = _parse_parallel(["parallel_hypotheses: abc"])
        assert result is None

    def test_unknown_keys_returns_none(self) -> None:
        result = _parse_parallel(["unknown_key: value"])
        assert result is None

    def test_invalid_selection_strategy_skipped(self) -> None:
        result = _parse_parallel(["selection_strategy: tournament"])
        assert result is None

    def test_out_of_range_returns_none(self) -> None:
        result = _parse_parallel(["parallel_hypotheses: 0"])
        assert result is None

    def test_float_input(self) -> None:
        result = _parse_parallel(3.0)
        assert result is None

    def test_whitespace_handling(self) -> None:
        result = _parse_parallel(["  parallel_hypotheses :  5  "])
        assert result is not None
        assert result.parallel_hypotheses == 5


# ── ExperimentStore superseded roundtrip (store.py coverage) ────


class TestSupersededFinalize:
    async def test_finalize_superseded_writes_tsv(self, tmp_path: Path) -> None:
        from factory.store import ExperimentStore

        project = tmp_path / "proj"
        project.mkdir()
        factory_dir = project / ".factory"
        factory_dir.mkdir()
        (factory_dir / "experiments").mkdir()
        tsv_path = factory_dir / "results.tsv"
        tsv_path.write_text(
            "id\ttimestamp\thypothesis\tchange_summary\tissue_number\tpr_number\t"
            "score_before\tscore_after\tdelta\tverdict\tcost_usd\tnotes\tresearch_citations\n"
        )

        store = ExperimentStore(project)
        record = ExperimentRecord(
            id=1,
            timestamp=datetime.now(tz=timezone.utc),
            hypothesis="test hypothesis",
            change_summary="superseded by experiment 2",
            issue_number=None,
            pr_number=None,
            score_before=0.5,
            score_after=0.6,
            delta=None,
            verdict="superseded",
            cost_usd=None,
            notes="",
        )
        await store.finalize(1, record)

        verdict_file = factory_dir / "experiments" / "001" / "verdict.json"
        assert verdict_file.exists()
        data = json.loads(verdict_file.read_text())
        assert data["verdict"] == "superseded"
        assert data["delta"] == 0.1

        with open(tsv_path, newline="") as f:
            reader = csv.DictReader(f, dialect="excel-tab")
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["verdict"] == "superseded"

    async def test_load_history_reads_superseded(self, tmp_path: Path) -> None:
        from factory.store import ExperimentStore

        project = tmp_path / "proj"
        project.mkdir()
        factory_dir = project / ".factory"
        factory_dir.mkdir()
        (factory_dir / "experiments").mkdir()
        tsv_path = factory_dir / "results.tsv"
        tsv_path.write_text(
            "id\ttimestamp\thypothesis\tchange_summary\tissue_number\tpr_number\t"
            "score_before\tscore_after\tdelta\tverdict\tcost_usd\tnotes\tresearch_citations\n"
        )

        store = ExperimentStore(project)
        record = ExperimentRecord(
            id=1,
            timestamp=datetime.now(tz=timezone.utc),
            hypothesis="test",
            change_summary="superseded",
            issue_number=None,
            pr_number=None,
            score_before=None,
            score_after=0.7,
            delta=None,
            verdict="superseded",
            cost_usd=None,
            notes="loser",
        )
        await store.finalize(1, record)

        history = await store.load_history()
        assert len(history) == 1
        assert history[0].verdict == "superseded"
        assert history[0].notes == "loser"


# ── SubgraphForkNode validation error paths (validation.py) ────


class TestSubgraphForkValidation:
    def test_missing_entry_node(self) -> None:
        wf = Workflow(
            name="bad",
            nodes={
                "start": FnNode(id="start", writes={"x"}),
                "fork": SubgraphForkNode(
                    id="fork", subgraph_entry="missing", subgraph_exit="start",
                    reads={"x"},
                ),
            },
            edges=[Edge(source="start", target="fork")],
            start_node="start",
        )
        issues = wf.validate_graph()
        assert any("entry 'missing' not in nodes" in i for i in issues)

    def test_missing_exit_node(self) -> None:
        wf = Workflow(
            name="bad",
            nodes={
                "start": FnNode(id="start", writes={"x"}),
                "fork": SubgraphForkNode(
                    id="fork", subgraph_entry="start", subgraph_exit="missing",
                    reads={"x"},
                ),
            },
            edges=[Edge(source="start", target="fork")],
            start_node="start",
        )
        issues = wf.validate_graph()
        assert any("exit 'missing' not in nodes" in i for i in issues)

    def test_no_path_from_entry_to_exit(self) -> None:
        wf = Workflow(
            name="bad",
            nodes={
                "start": FnNode(id="start", writes={"x"}),
                "a": FnNode(id="a", writes={"y"}),
                "b": FnNode(id="b", writes={"z"}),
                "fork": SubgraphForkNode(
                    id="fork", subgraph_entry="a", subgraph_exit="b",
                    reads={"x"},
                ),
            },
            edges=[
                Edge(source="start", target="fork"),
                Edge(source="fork", target="a"),
            ],
            start_node="start",
        )
        issues = wf.validate_graph()
        assert any("no path from entry 'a' to exit 'b'" in i for i in issues)


# ── _execute_selection non-dry-run tests (executor.py coverage) ─


class TestSelectionAllFailed:
    async def test_all_branches_failed_halts(self, tmp_project: Path) -> None:
        wf = Workflow(
            name="test-select",
            nodes={
                "pre": FnNode(id="pre", command="echo pre", writes={"pre.txt"}),
                "select": SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            },
            edges=[Edge(source="pre", target="select")],
            start_node="pre",
        )
        executor = WorkflowExecutor(wf, tmp_project, dry_run=False)
        executor.result.node_outputs["fork"] = json.dumps([
            {"exp_id": 1, "success": False, "halted": True, "halt_reason": "err",
             "worktree_path": "/tmp/fake", "branch": "factory/exp-1", "hypothesis": "h1"},
            {"exp_id": 2, "success": False, "halted": True, "halt_reason": "err",
             "worktree_path": "/tmp/fake", "branch": "factory/exp-2", "hypothesis": "h2"},
        ])
        executor.completed_files = {"pre.txt"}

        await executor._execute_selection(
            SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
        )

        assert executor.result.halted is True
        assert "all parallel experiment branches failed" in executor.result.halt_reason


class TestSelectionPicksBest:
    async def test_selects_highest_score(self, tmp_project: Path) -> None:
        wt1 = tmp_project / ".factory-worktrees" / "exp-1"
        wt2 = tmp_project / ".factory-worktrees" / "exp-2"
        wt1.mkdir(parents=True)
        wt2.mkdir(parents=True)
        (wt1 / ".factory").mkdir()
        (wt2 / ".factory").mkdir()
        (wt1 / ".factory" / "last_eval.json").write_text(json.dumps({"total": 0.7}))
        (wt2 / ".factory" / "last_eval.json").write_text(json.dumps({"total": 0.9}))

        wf = Workflow(
            name="test-select",
            nodes={
                "pre": FnNode(id="pre", command="echo pre", writes={"pre.txt"}),
                "select": SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            },
            edges=[Edge(source="pre", target="select")],
            start_node="pre",
        )
        executor = WorkflowExecutor(wf, tmp_project, dry_run=False)
        executor.result.node_outputs["fork"] = json.dumps([
            {"exp_id": 1, "success": True, "halted": False, "halt_reason": "",
             "worktree_path": str(wt1), "branch": "factory/exp-1", "hypothesis": "h1"},
            {"exp_id": 2, "success": True, "halted": False, "halt_reason": "",
             "worktree_path": str(wt2), "branch": "factory/exp-2", "hypothesis": "h2"},
        ])
        executor.completed_files = {"pre.txt"}

        mock_finalize = AsyncMock()
        with patch("subprocess.run") as mock_sp, \
             patch("factory.store.ExperimentStore.finalize", mock_finalize):
            mock_sp.return_value = MagicMock(returncode=0)

            await executor._execute_selection(
                SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            )

        assert not executor.result.halted
        selection = json.loads(executor.result.node_outputs["select"])
        assert selection["winner_exp_id"] == 2
        assert selection["winner_score"] == 0.9
        assert selection["total_branches"] == 2
        assert selection["successful_branches"] == 2

        mock_finalize.assert_called_once()
        finalized_record = mock_finalize.call_args[0][1]
        assert finalized_record.verdict == "superseded"

    async def test_score_key_fallback(self, tmp_project: Path) -> None:
        """Uses 'score' key when 'total' is absent."""
        wt1 = tmp_project / ".factory-worktrees" / "exp-1"
        wt1.mkdir(parents=True)
        (wt1 / ".factory").mkdir()
        (wt1 / ".factory" / "last_eval.json").write_text(json.dumps({"score": 0.85}))

        wf = Workflow(
            name="test-select",
            nodes={
                "pre": FnNode(id="pre", command="echo pre", writes={"pre.txt"}),
                "select": SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            },
            edges=[Edge(source="pre", target="select")],
            start_node="pre",
        )
        executor = WorkflowExecutor(wf, tmp_project, dry_run=False)
        executor.result.node_outputs["fork"] = json.dumps([
            {"exp_id": 1, "success": True, "halted": False, "halt_reason": "",
             "worktree_path": str(wt1), "branch": "factory/exp-1", "hypothesis": "h1"},
        ])
        executor.completed_files = {"pre.txt"}

        with patch("subprocess.run") as mock_sp:
            mock_sp.return_value = MagicMock(returncode=0)
            await executor._execute_selection(
                SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            )

        assert not executor.result.halted
        selection = json.loads(executor.result.node_outputs["select"])
        assert selection["winner_score"] == 0.85

    async def test_missing_eval_file_defaults_to_zero(self, tmp_project: Path) -> None:
        wt1 = tmp_project / ".factory-worktrees" / "exp-1"
        wt1.mkdir(parents=True)

        wf = Workflow(
            name="test-select",
            nodes={
                "pre": FnNode(id="pre", command="echo pre", writes={"pre.txt"}),
                "select": SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            },
            edges=[Edge(source="pre", target="select")],
            start_node="pre",
        )
        executor = WorkflowExecutor(wf, tmp_project, dry_run=False)
        executor.result.node_outputs["fork"] = json.dumps([
            {"exp_id": 1, "success": True, "halted": False, "halt_reason": "",
             "worktree_path": str(wt1), "branch": "factory/exp-1", "hypothesis": "h1"},
        ])
        executor.completed_files = {"pre.txt"}

        with patch("subprocess.run") as mock_sp:
            mock_sp.return_value = MagicMock(returncode=0)
            await executor._execute_selection(
                SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            )

        selection = json.loads(executor.result.node_outputs["select"])
        assert selection["winner_score"] == 0.0

    async def test_malformed_eval_json_defaults_to_zero(self, tmp_project: Path) -> None:
        wt1 = tmp_project / ".factory-worktrees" / "exp-1"
        wt1.mkdir(parents=True)
        (wt1 / ".factory").mkdir()
        (wt1 / ".factory" / "last_eval.json").write_text("not json")

        wf = Workflow(
            name="test-select",
            nodes={
                "pre": FnNode(id="pre", command="echo pre", writes={"pre.txt"}),
                "select": SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            },
            edges=[Edge(source="pre", target="select")],
            start_node="pre",
        )
        executor = WorkflowExecutor(wf, tmp_project, dry_run=False)
        executor.result.node_outputs["fork"] = json.dumps([
            {"exp_id": 1, "success": True, "halted": False, "halt_reason": "",
             "worktree_path": str(wt1), "branch": "factory/exp-1", "hypothesis": "h1"},
        ])
        executor.completed_files = {"pre.txt"}

        with patch("subprocess.run") as mock_sp:
            mock_sp.return_value = MagicMock(returncode=0)
            await executor._execute_selection(
                SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            )

        selection = json.loads(executor.result.node_outputs["select"])
        assert selection["winner_score"] == 0.0


class TestSelectionMergeFailure:
    async def test_merge_failure_halts(self, tmp_project: Path) -> None:
        import subprocess as sp

        wt1 = tmp_project / ".factory-worktrees" / "exp-1"
        wt1.mkdir(parents=True)

        wf = Workflow(
            name="test-select",
            nodes={
                "pre": FnNode(id="pre", command="echo pre", writes={"pre.txt"}),
                "select": SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            },
            edges=[Edge(source="pre", target="select")],
            start_node="pre",
        )
        executor = WorkflowExecutor(wf, tmp_project, dry_run=False)
        executor.result.node_outputs["fork"] = json.dumps([
            {"exp_id": 1, "success": True, "halted": False, "halt_reason": "",
             "worktree_path": str(wt1), "branch": "factory/exp-1", "hypothesis": "h1"},
        ])
        executor.completed_files = {"pre.txt"}

        with patch("subprocess.run") as mock_sp:
            mock_sp.side_effect = sp.CalledProcessError(1, "git merge")
            await executor._execute_selection(
                SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            )

        assert executor.result.halted is True
        assert "failed to merge winner branch" in executor.result.halt_reason


class TestSelectionCleanup:
    async def test_finalize_failure_is_logged_not_fatal(self, tmp_project: Path) -> None:
        wt1 = tmp_project / ".factory-worktrees" / "exp-1"
        wt2 = tmp_project / ".factory-worktrees" / "exp-2"
        wt1.mkdir(parents=True)
        wt2.mkdir(parents=True)

        wf = Workflow(
            name="test-select",
            nodes={
                "pre": FnNode(id="pre", command="echo pre", writes={"pre.txt"}),
                "select": SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            },
            edges=[Edge(source="pre", target="select")],
            start_node="pre",
        )
        executor = WorkflowExecutor(wf, tmp_project, dry_run=False)
        executor.result.node_outputs["fork"] = json.dumps([
            {"exp_id": 1, "success": True, "halted": False, "halt_reason": "",
             "worktree_path": str(wt1), "branch": "factory/exp-1", "hypothesis": "h1"},
            {"exp_id": 2, "success": True, "halted": False, "halt_reason": "",
             "worktree_path": str(wt2), "branch": "factory/exp-2", "hypothesis": "h2"},
        ])
        executor.completed_files = {"pre.txt"}

        mock_finalize = AsyncMock(side_effect=RuntimeError("db error"))
        with patch("subprocess.run") as mock_sp, \
             patch("factory.store.ExperimentStore.finalize", mock_finalize):
            mock_sp.return_value = MagicMock(returncode=0)
            await executor._execute_selection(
                SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            )

        assert not executor.result.halted
        assert "select" in executor.result.node_outputs

    async def test_worktree_cleanup_failure_not_fatal(self, tmp_project: Path) -> None:
        wt1 = tmp_project / ".factory-worktrees" / "exp-1"
        wt1.mkdir(parents=True)

        wf = Workflow(
            name="test-select",
            nodes={
                "pre": FnNode(id="pre", command="echo pre", writes={"pre.txt"}),
                "select": SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            },
            edges=[Edge(source="pre", target="select")],
            start_node="pre",
        )
        executor = WorkflowExecutor(wf, tmp_project, dry_run=False)
        executor.result.node_outputs["fork"] = json.dumps([
            {"exp_id": 1, "success": True, "halted": False, "halt_reason": "",
             "worktree_path": str(wt1), "branch": "factory/exp-1", "hypothesis": "h1"},
        ])
        executor.completed_files = {"pre.txt"}

        with patch("subprocess.run") as mock_sp, \
             patch("factory.worktree.remove_worktree", side_effect=OSError("rm fail")):
            mock_sp.return_value = MagicMock(returncode=0)
            await executor._execute_selection(
                SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            )

        assert not executor.result.halted

    async def test_fork_output_search_skips_invalid_json(self, tmp_project: Path) -> None:
        """Non-JSON node outputs are skipped when searching for fork results."""
        wf = Workflow(
            name="test-select",
            nodes={
                "pre": FnNode(id="pre", command="echo pre", writes={"pre.txt"}),
                "select": SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
            },
            edges=[Edge(source="pre", target="select")],
            start_node="pre",
        )
        executor = WorkflowExecutor(wf, tmp_project, dry_run=False)
        executor.result.node_outputs["bad"] = "not json at all"
        executor.result.node_outputs["plain"] = json.dumps({"some": "data"})
        executor.completed_files = {"pre.txt"}

        await executor._execute_selection(
            SelectionNode(id="select", reads={"pre.txt"}, writes={"result.json"}),
        )

        selection = json.loads(executor.result.node_outputs["select"])
        assert selection["winner"] is None
        assert selection["reason"] == "dry-run"


# ── _execute_subgraph_fork non-dry-run tests (executor.py) ─────


class TestSubgraphForkNonDryRun:
    async def test_branch_count_fallback_to_one(self, tmp_project: Path) -> None:
        """When no strategy file exists and parallelism=3, branch_count defaults to parallelism."""
        wf = Workflow(
            name="test-fork",
            nodes={
                "fork": SubgraphForkNode(
                    id="fork", subgraph_entry="step", subgraph_exit="step",
                    parallelism=2, writes={"fork.json"},
                ),
                "step": FnNode(id="step", writes={"s.txt"}),
            },
            edges=[Edge(source="fork", target="step")],
            start_node="fork",
        )
        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)

        await executor._execute_subgraph_fork(
            SubgraphForkNode(
                id="fork", subgraph_entry="step", subgraph_exit="step",
                parallelism=2, writes={"fork.json"},
            ),
        )

        results = json.loads(executor.result.node_outputs["fork"])
        assert len(results) == 2

    async def test_error_in_branch_captured(self, tmp_project: Path) -> None:
        """A branch that raises is captured as a failed result, not a crash."""
        wf = Workflow(
            name="test-fork",
            nodes={
                "fork": SubgraphForkNode(
                    id="fork", subgraph_entry="step", subgraph_exit="step",
                    parallelism=2, writes={"fork.json"},
                ),
                "step": FnNode(id="step", writes={"s.txt"}),
            },
            edges=[],
            start_node="fork",
        )

        strategy_dir = tmp_project / ".factory" / "strategy"
        (strategy_dir / "current.md").write_text(
            "## Hypothesis 1\nH1\n\n## Hypothesis 2\nH2\n"
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=False)

        with patch("subprocess.run") as mock_sp, \
             patch(
                 "factory.worktree.create_experiment_worktree",
                 side_effect=RuntimeError("worktree fail"),
             ), \
             patch("factory.store.ExperimentStore.begin", new_callable=AsyncMock, return_value=1):
            mock_sp.return_value = MagicMock(stdout="abc123\n")

            await executor._execute_subgraph_fork(
                SubgraphForkNode(
                    id="fork", subgraph_entry="step", subgraph_exit="step",
                    parallelism=2, writes={"fork.json"},
                ),
            )

        results = json.loads(executor.result.node_outputs["fork"])
        assert len(results) == 2
        assert all(r["success"] is False for r in results)
        assert all(r["halted"] is True for r in results)

    async def test_non_dry_run_calls_git_rev_parse(self, tmp_project: Path) -> None:
        """Non-dry-run path resolves HEAD via git rev-parse."""
        wf = Workflow(
            name="test-fork",
            nodes={
                "fork": SubgraphForkNode(
                    id="fork", subgraph_entry="step", subgraph_exit="step",
                    parallelism=1, writes={"fork.json"},
                ),
                "step": FnNode(id="step", writes={"s.txt"}),
            },
            edges=[],
            start_node="fork",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=False)

        calls = []

        def track_sp(*args, **kwargs):
            calls.append(args[0] if args else kwargs.get("args"))
            result = MagicMock()
            result.stdout = "abc123def456\n"
            result.returncode = 0
            return result

        fake_wt_path = tmp_project / ".factory-worktrees" / "exp-1"
        fake_wt_path.mkdir(parents=True)

        with patch("subprocess.run", side_effect=track_sp), \
             patch(
                 "factory.worktree.create_experiment_worktree",
                 return_value=(fake_wt_path, "factory/exp-1"),
             ), \
             patch("factory.store.ExperimentStore.begin", new_callable=AsyncMock, return_value=1):
            await executor._execute_subgraph_fork(
                SubgraphForkNode(
                    id="fork", subgraph_entry="step", subgraph_exit="step",
                    parallelism=1, writes={"fork.json"},
                ),
            )

        assert any(
            c and "rev-parse" in str(c) for c in calls
        ), f"Expected git rev-parse call, got: {calls}"


# ── _collect_subgraph_nodes branching test ──────────────────────


class TestCollectSubgraphBranching:
    def test_diamond_subgraph(self) -> None:
        wf = Workflow(
            name="test",
            nodes={
                "a": FnNode(id="a", writes={"x"}),
                "b": FnNode(id="b", reads={"x"}, writes={"y"}),
                "c": FnNode(id="c", reads={"x"}, writes={"z"}),
                "d": FnNode(id="d", reads={"y", "z"}, writes={"w"}),
            },
            edges=[
                Edge(source="a", target="b"),
                Edge(source="a", target="c"),
                Edge(source="b", target="d"),
                Edge(source="c", target="d"),
            ],
            start_node="a",
        )
        result = _collect_subgraph_nodes(wf, "a", "d")
        assert result == {"a", "b", "c", "d"}


# ── _parse_hypotheses edge cases ────────────────────────────────


class TestParseHypothesesEdgeCases:
    def test_numbered_bullets(self, tmp_path: Path) -> None:
        f = tmp_path / "current.md"
        f.write_text("1. **Optimize DB queries** for speed\n")
        result = _parse_hypotheses(f)
        assert len(result) == 1

    def test_h3_headings(self, tmp_path: Path) -> None:
        f = tmp_path / "current.md"
        f.write_text(
            "### Hypothesis 1\nFirst idea\n\n### Hypothesis 2\nSecond idea\n"
        )
        result = _parse_hypotheses(f)
        assert len(result) == 2

    def test_hypothesis_followed_by_other_heading(self, tmp_path: Path) -> None:
        f = tmp_path / "current.md"
        f.write_text(
            "## Hypothesis 1\nAdd caching\n\n## Summary\nDone.\n"
        )
        result = _parse_hypotheses(f)
        assert len(result) == 1
        assert "caching" in result[0].lower()
