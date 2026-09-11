"""Tests for the DataNode graph primitive — models, executor, validation, skill export, and features."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from factory.workflow.primitives import (
    DataItem,
    DataNode,
    Edge,
    FnNode,
    Workflow,
)


# ── Phase 1: DataItem / DataNode Pydantic validation ──────────────


class TestDataItem:
    def test_minimal(self) -> None:
        item = DataItem(id="a")
        assert item.id == "a"
        assert item.path is None
        assert item.metadata == {}
        assert item.prompt == ""

    def test_full(self) -> None:
        item = DataItem(id="b", path="/tmp/b", metadata={"k": "v"}, prompt="do stuff")
        assert item.path == "/tmp/b"
        assert item.metadata == {"k": "v"}
        assert item.prompt == "do stuff"

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            DataItem(id="a", unknown="x")

    def test_roundtrip(self) -> None:
        item = DataItem(id="c", metadata={"x": 1})
        data = item.model_dump(mode="json")
        restored = DataItem.model_validate(data)
        assert restored.id == "c"
        assert restored.metadata == {"x": 1}


class TestDataNode:
    def test_inline_items_source(self) -> None:
        items = [DataItem(id="i1"), DataItem(id="i2")]
        node = DataNode(
            id="dn",
            inline_items=items,
            subgraph_entry="a",
            subgraph_exit="b",
        )
        assert len(node.inline_items) == 2
        assert node.task_ref is None
        assert node.source_path is None

    def test_task_ref_source(self) -> None:
        node = DataNode(
            id="dn",
            task_ref="my.module:MyTask",
            subgraph_entry="a",
            subgraph_exit="b",
        )
        assert node.task_ref == "my.module:MyTask"

    def test_source_path_source(self) -> None:
        node = DataNode(
            id="dn",
            source_path="/data/items",
            source_format="directory",
            subgraph_entry="a",
            subgraph_exit="b",
        )
        assert node.source_path == "/data/items"
        assert node.source_format == "directory"

    def test_no_source_raises(self) -> None:
        with pytest.raises(ValidationError, match="Exactly one"):
            DataNode(
                id="dn",
                subgraph_entry="a",
                subgraph_exit="b",
            )

    def test_multiple_sources_raises(self) -> None:
        with pytest.raises(ValidationError, match="Exactly one"):
            DataNode(
                id="dn",
                task_ref="x",
                inline_items=[DataItem(id="i")],
                subgraph_entry="a",
                subgraph_exit="b",
            )

    def test_source_path_requires_format(self) -> None:
        with pytest.raises(ValidationError, match="source_format"):
            DataNode(
                id="dn",
                source_path="/data/items",
                subgraph_entry="a",
                subgraph_exit="b",
            )

    def test_defaults(self) -> None:
        node = DataNode(
            id="dn",
            inline_items=[DataItem(id="i")],
            subgraph_entry="a",
            subgraph_exit="b",
        )
        assert node.parallelism == 1
        assert node.split == "all"
        assert node.shuffle is False
        assert node.shuffle_seed is None
        assert node.limit is None
        assert node.max_items == 500

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            DataNode(
                id="dn",
                inline_items=[DataItem(id="i")],
                subgraph_entry="a",
                subgraph_exit="b",
                unknown="x",
            )

    def test_from_dict_roundtrip(self) -> None:
        items = [DataItem(id="i1", prompt="do it")]
        node = DataNode(
            id="dn",
            inline_items=items,
            subgraph_entry="entry",
            subgraph_exit="exit",
            parallelism=5,
            max_items=100,
        )
        wf = Workflow(
            name="test",
            nodes={
                "dn": node,
                "entry": FnNode(id="entry", command="echo entry"),
                "exit": FnNode(id="exit", command="echo exit"),
            },
            edges=[
                Edge(source="dn", target="entry"),
                Edge(source="entry", target="exit"),
            ],
            start_node="dn",
        )
        data = wf.to_dict()
        restored = Workflow.from_dict(data)
        dn = restored.nodes["dn"]
        assert type(dn).__name__ == "DataNode"
        assert dn.parallelism == 5
        assert dn.max_items == 100
        assert len(dn.inline_items) == 1
        assert dn.inline_items[0].id == "i1"


# ── Phase 2: Executor _execute_data ──────────────────────────────


def _make_data_workflow(items: list[DataItem]) -> Workflow:
    """Build a minimal workflow with a DataNode driving a FnNode subgraph."""
    return Workflow(
        name="data_test",
        nodes={
            "data": DataNode(
                id="data",
                inline_items=items,
                subgraph_entry="sub_start",
                subgraph_exit="sub_end",
                parallelism=2,
            ),
            "sub_start": FnNode(id="sub_start", command="echo start"),
            "sub_end": FnNode(id="sub_end", command="echo end"),
        },
        edges=[
            Edge(source="sub_start", target="sub_end"),
        ],
        start_node="data",
    )


class TestExecuteData:
    def test_inline_items_execute(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        items = [DataItem(id="a", prompt="do a"), DataItem(id="b", prompt="do b")]
        wf = _make_data_workflow(items)
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())

        assert result.success
        assert "data" in result.node_outputs
        parsed = json.loads(result.node_outputs["data"])
        assert len(parsed) == 2
        assert parsed[0]["item_id"] == "a"
        assert parsed[1]["item_id"] == "b"

    def test_fault_isolation_one_bad_item(self, tmp_path: Path) -> None:
        """A failing subgraph for one item should not halt the whole DataNode."""
        from factory.workflow.executor import WorkflowExecutor

        items = [DataItem(id="good"), DataItem(id="bad"), DataItem(id="also_good")]
        wf = _make_data_workflow(items)

        original_init = WorkflowExecutor.__init__

        def tracking_init(self_inner, workflow, project_path, *args, **kwargs):
            original_init(self_inner, workflow, project_path, *args, **kwargs)
            ctx = kwargs.get("initial_context")
            self_inner._test_initial_context = ctx

        original_execute = WorkflowExecutor.execute

        async def selective_execute(self_inner):
            # Inner executors (sub-workflows) have _test_initial_context set
            if hasattr(self_inner, "_test_initial_context") and self_inner.workflow.name.endswith("__data_item"):
                # Find which item this is by checking if it's the 2nd call (bad)
                if not hasattr(selective_execute, "_inner_count"):
                    selective_execute._inner_count = 0
                selective_execute._inner_count += 1
                if selective_execute._inner_count == 2:
                    raise RuntimeError("simulated failure")
            return await original_execute(self_inner)

        selective_execute._inner_count = 0

        with patch.object(WorkflowExecutor, "__init__", tracking_init), \
             patch.object(WorkflowExecutor, "execute", selective_execute):
            executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
            result = asyncio.run(executor.execute())

        assert result.success
        parsed = json.loads(result.node_outputs["data"])
        assert len(parsed) == 3
        bad_item = next(r for r in parsed if r["item_id"] == "bad")
        assert bad_item["score"] == 0.0
        assert "error" in bad_item
        good_items = [r for r in parsed if r["item_id"] != "bad"]
        assert all(r["success"] for r in good_items)

    def test_max_items_exceeded_raises(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        items = [DataItem(id=str(i)) for i in range(10)]
        wf = Workflow(
            name="data_test",
            nodes={
                "data": DataNode(
                    id="data",
                    inline_items=items,
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                    max_items=5,
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.halted
        assert "max_items=5" in result.halt_reason

    def test_split_filter(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        items = [
            DataItem(id="train1", metadata={"split": "train"}),
            DataItem(id="val1", metadata={"split": "val"}),
            DataItem(id="train2", metadata={"split": "train"}),
        ]
        wf = Workflow(
            name="data_test",
            nodes={
                "data": DataNode(
                    id="data",
                    inline_items=items,
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                    split="train",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.success
        parsed = json.loads(result.node_outputs["data"])
        assert len(parsed) == 2
        assert all(r["item_id"].startswith("train") for r in parsed)

    def test_limit_filter(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        items = [DataItem(id=str(i)) for i in range(10)]
        wf = Workflow(
            name="data_test",
            nodes={
                "data": DataNode(
                    id="data",
                    inline_items=items,
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                    limit=3,
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.success
        parsed = json.loads(result.node_outputs["data"])
        assert len(parsed) == 3


# ── Phase 3: Validation ─────────────────────────────────────────


class TestDataNodeValidation:
    def test_valid_data_node_workflow(self) -> None:
        wf = _make_data_workflow([DataItem(id="i")])
        issues = wf.validate_graph()
        assert not issues

    def test_missing_subgraph_entry(self) -> None:
        wf = Workflow(
            name="bad",
            nodes={
                "data": DataNode(
                    id="data",
                    inline_items=[DataItem(id="i")],
                    subgraph_entry="missing",
                    subgraph_exit="sub",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        issues = wf.validate_graph()
        assert any("missing" in i and "entry" in i for i in issues)

    def test_missing_subgraph_exit(self) -> None:
        wf = Workflow(
            name="bad",
            nodes={
                "data": DataNode(
                    id="data",
                    inline_items=[DataItem(id="i")],
                    subgraph_entry="sub",
                    subgraph_exit="missing",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        issues = wf.validate_graph()
        assert any("missing" in i and "exit" in i for i in issues)

    def test_subgraph_nodes_reachable(self) -> None:
        """Subgraph nodes behind a DataNode should not be flagged as unreachable."""
        wf = _make_data_workflow([DataItem(id="i")])
        issues = wf.validate_graph()
        unreachable = [i for i in issues if "unreachable" in i]
        assert not unreachable


# ── Phase 4: Skill export ─────────────────────────────────────────


class TestDataNodeSkillExport:
    def test_data_node_renders(self) -> None:
        from factory.workflow.skill_export import workflow_to_skill_md

        wf = _make_data_workflow([DataItem(id="i")])
        md = workflow_to_skill_md(wf)
        assert "Data Iteration" in md
        assert "inline items" in md
        assert "fault isolation" in md.lower()

    def test_subgraph_nodes_not_duplicated(self) -> None:
        from factory.workflow.skill_export import workflow_to_skill_md

        wf = _make_data_workflow([DataItem(id="i")])
        md = workflow_to_skill_md(wf)
        # sub_start and sub_end should NOT appear as top-level phases
        assert "Sub Start" not in md or md.count("Sub Start") <= 1
        assert "Sub End" not in md or md.count("Sub End") <= 1


# ── Phase 6: compute_features arity ────────────────────────────────


class TestComputeFeaturesDataNode:
    def test_arity_is_9(self) -> None:
        from factory.outer_loop.similarity import compute_features

        wf = Workflow(
            name="w",
            nodes={"a": FnNode(id="a", command="x")},
            edges=[],
            start_node="a",
        )
        features = compute_features(wf)
        assert len(features) == 9

    def test_data_node_sets_feature(self) -> None:
        from factory.outer_loop.similarity import compute_features

        wf = _make_data_workflow([DataItem(id="i")])
        features = compute_features(wf)
        assert len(features) == 9
        assert features[8] == 1  # has_data_node is the appended axis

    def test_no_data_node_feature_is_zero(self) -> None:
        from factory.outer_loop.similarity import compute_features

        wf = Workflow(
            name="w",
            nodes={"a": FnNode(id="a", command="x")},
            edges=[],
            start_node="a",
        )
        features = compute_features(wf)
        assert features[8] == 0


class TestDiversityMetricNewAxis:
    def test_diversity_responds_to_data_node_axis(self) -> None:
        from factory.outer_loop.population import MAPElitesArchive, Population

        wf_no_data = Workflow(
            name="w",
            nodes={"a": FnNode(id="a", command="x")},
            edges=[],
            start_node="a",
        )
        wf_with_data = _make_data_workflow([DataItem(id="i")])

        ind1 = Population.make_individual(wf_no_data, score=0.5)
        ind2 = Population.make_individual(wf_with_data, score=0.5)

        archive = MAPElitesArchive()
        archive.add(ind1)
        d1 = archive.diversity_metric()

        archive.add(ind2)
        d2 = archive.diversity_metric()
        # Adding a structurally different individual should change diversity
        assert d2 != d1 or archive.size == 1


# ── Phase 5: compose CAN_ITERATE ──────────────────────────────────


# ── Phase 7: source_path code paths ─────────────────────────────


class TestSourcePathDirectory:
    def test_directory_source(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        src_dir = tmp_path / "data"
        src_dir.mkdir()
        (src_dir / "alpha").mkdir()
        (src_dir / "beta").mkdir()
        (src_dir / "plain_file.txt").write_text("not a dir")

        wf = Workflow(
            name="dir_test",
            nodes={
                "data": DataNode(
                    id="data",
                    source_path=str(src_dir),
                    source_format="directory",
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.success
        parsed = json.loads(result.node_outputs["data"])
        ids = [r["item_id"] for r in parsed]
        assert "alpha" in ids
        assert "beta" in ids
        assert "plain_file.txt" not in ids


class TestSourcePathJsonl:
    def test_jsonl_source(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        jsonl_file = tmp_path / "items.jsonl"
        jsonl_file.write_text('{"name": "first"}\n{"name": "second"}\n\n')

        wf = Workflow(
            name="jsonl_test",
            nodes={
                "data": DataNode(
                    id="data",
                    source_path=str(jsonl_file),
                    source_format="jsonl",
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.success
        parsed = json.loads(result.node_outputs["data"])
        assert len(parsed) == 2
        assert parsed[0]["item_id"] == "0"
        assert parsed[1]["item_id"] == "1"


class TestSourcePathCsv:
    def test_csv_source(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        csv_file = tmp_path / "items.csv"
        csv_file.write_text("id,value\na,1\nb,2\nc,3\n")

        wf = Workflow(
            name="csv_test",
            nodes={
                "data": DataNode(
                    id="data",
                    source_path=str(csv_file),
                    source_format="csv",
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.success
        parsed = json.loads(result.node_outputs["data"])
        assert len(parsed) == 3
        assert parsed[0]["item_id"] == "0"
        assert parsed[1]["item_id"] == "1"
        assert parsed[2]["item_id"] == "2"


class TestSourcePathNonExistent:
    def test_nonexistent_path_raises(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        wf = Workflow(
            name="missing_test",
            nodes={
                "data": DataNode(
                    id="data",
                    source_path=str(tmp_path / "does_not_exist"),
                    source_format="directory",
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.halted
        assert "source_path not found" in result.halt_reason


# ── Phase 8: inner_loop _step_with_data_node ────────────────────


class TestStepWithDataNode:
    def test_delegates_to_executor(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock

        from factory.inner_loop import InnerLoop
        from factory.workflow.executor import ExecutionResult

        wf = _make_data_workflow([DataItem(id="i", prompt="go")])

        mock_result = ExecutionResult()
        mock_result.success = True

        loop = InnerLoop(project_dir=tmp_path, workflow=wf)

        with patch(
            "factory.workflow.executor.WorkflowExecutor.execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            record = loop._step_with_data_node()

        assert record.score_end == 1.0
        assert record.cycle_number == 1


class TestSubgraphInheritsCompletedFiles:
    """Verify that subgraph executors inherit parent completed_files."""

    def test_subgraph_reads_upstream_artifact(self, tmp_path: Path) -> None:
        """Subgraph start node with reads={'data_ready'} should inherit the
        artifact from an upstream FnNode that writes={'data_ready'}, so it
        executes instead of timing out."""
        from factory.workflow.executor import WorkflowExecutor

        wf = Workflow(
            name="inherit_test",
            nodes={
                "upstream_fn": FnNode(
                    id="upstream_fn", command="echo ready", writes={"data_ready"},
                ),
                "data_loader": DataNode(
                    id="data_loader",
                    inline_items=[DataItem(id="item1", prompt="go")],
                    subgraph_entry="process_node",
                    subgraph_exit="exit_node",
                ),
                "process_node": FnNode(
                    id="process_node",
                    command="echo processing",
                    reads={"data_ready"},
                ),
                "exit_node": FnNode(id="exit_node", command="echo done"),
            },
            edges=[
                Edge(source="upstream_fn", target="data_loader"),
                Edge(source="process_node", target="exit_node"),
            ],
            start_node="upstream_fn",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())

        assert result.success, f"Expected success but got halt: {result.halt_reason}"
        assert not result.halted
        parsed = json.loads(result.node_outputs["data_loader"])
        assert len(parsed) == 1
        assert parsed[0]["nodes_executed"] > 0

    def test_subgraph_no_reads_still_works(self, tmp_path: Path) -> None:
        """Subgraph start node with no reads should execute normally (baseline)."""
        from factory.workflow.executor import WorkflowExecutor

        wf = Workflow(
            name="no_reads_test",
            nodes={
                "upstream_fn": FnNode(
                    id="upstream_fn", command="echo ready", writes={"data_ready"},
                ),
                "data_loader": DataNode(
                    id="data_loader",
                    inline_items=[DataItem(id="item1")],
                    subgraph_entry="process_node",
                    subgraph_exit="exit_node",
                ),
                "process_node": FnNode(id="process_node", command="echo processing"),
                "exit_node": FnNode(id="exit_node", command="echo done"),
            },
            edges=[
                Edge(source="upstream_fn", target="data_loader"),
                Edge(source="process_node", target="exit_node"),
            ],
            start_node="upstream_fn",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())

        assert result.success
        parsed = json.loads(result.node_outputs["data_loader"])
        assert len(parsed) == 1
        assert parsed[0]["nodes_executed"] > 0


class TestComposeCapsDataNode:
    def test_data_node_adds_can_iterate(self) -> None:
        from factory.compose import ModeCapabilities
        from factory.task import Capability

        wf = _make_data_workflow([DataItem(id="i")])
        caps = ModeCapabilities.from_workflow(wf)
        assert Capability.CAN_ITERATE in caps.provides


# ── Phase 9: task_ref verify integration ──────────────────────────


class _FakeTask:
    """Minimal Task-like object for testing verify() integration."""

    def __init__(self, instances_data: list[dict[str, Any]], verify_scores: dict[str, float]) -> None:
        self._instances_data = instances_data
        self._verify_scores = verify_scores
        self.setup_calls: list[str] = []
        self.prompt_calls: list[str] = []
        self.verify_calls: list[str] = []

    def instances(self):
        from factory.task import TaskInstance
        for d in self._instances_data:
            yield TaskInstance(id=d["id"], path=d.get("path"), metadata=d.get("metadata", {}))

    def setup(self, instance, workspace):
        self.setup_calls.append(instance.id)

    def prompt(self, instance):
        self.prompt_calls.append(instance.id)
        return f"prompt for {instance.id}"

    def verify(self, instance, workspace):
        from factory.task import VerifyResult
        self.verify_calls.append(instance.id)
        score = self._verify_scores.get(instance.id, 0.0)
        return VerifyResult(passed=score > 0.5, score=score, details={"source": "fake"})


def _make_task_ref_workflow(task_ref: str = "fake.module:FakeTask") -> Workflow:
    """Build a minimal workflow with a task_ref DataNode."""
    return Workflow(
        name="task_ref_test",
        nodes={
            "data": DataNode(
                id="data",
                task_ref=task_ref,
                subgraph_entry="sub_start",
                subgraph_exit="sub_end",
                parallelism=2,
            ),
            "sub_start": FnNode(id="sub_start", command="echo start"),
            "sub_end": FnNode(id="sub_end", command="echo end"),
        },
        edges=[
            Edge(source="sub_start", target="sub_end"),
        ],
        start_node="data",
    )


class TestTaskRefVerify:
    def test_verify_called_per_item_and_scores_used(self, tmp_path: Path) -> None:
        """task_ref DataNode must call verify() per item and use verify scores."""
        from factory.workflow.executor import WorkflowExecutor

        fake_task = _FakeTask(
            instances_data=[{"id": "inst_a"}, {"id": "inst_b"}],
            verify_scores={"inst_a": 0.8, "inst_b": 0.3},
        )

        wf = _make_task_ref_workflow()

        with patch("factory.task.TaskRef.resolve", return_value=fake_task):
            executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
            result = asyncio.run(executor.execute())

        assert result.success
        parsed = json.loads(result.node_outputs["data"])
        assert len(parsed) == 2

        item_a = next(r for r in parsed if r["item_id"] == "inst_a")
        item_b = next(r for r in parsed if r["item_id"] == "inst_b")
        assert item_a["score"] == 0.8
        assert item_a["passed"] is True
        assert item_b["score"] == 0.3
        assert item_b["passed"] is False

        assert "inst_a" in fake_task.verify_calls
        assert "inst_b" in fake_task.verify_calls

    def test_inline_items_no_verify(self, tmp_path: Path) -> None:
        """inline_items DataNode must NOT call verify — uses subgraph-success scoring."""
        from factory.workflow.executor import WorkflowExecutor

        items = [DataItem(id="x", prompt="go"), DataItem(id="y", prompt="go")]
        wf = _make_data_workflow(items)
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())

        assert result.success
        parsed = json.loads(result.node_outputs["data"])
        assert all(r["score"] == 1.0 for r in parsed)
        assert all("verify_details" not in r or r["verify_details"] == {} for r in parsed)

    def test_setup_prompt_called_per_item_in_run_item(self, tmp_path: Path) -> None:
        """setup() and prompt() must be called per-item inside run_item, not eagerly."""
        from factory.workflow.executor import WorkflowExecutor

        fake_task = _FakeTask(
            instances_data=[{"id": "i1"}, {"id": "i2"}, {"id": "i3"}],
            verify_scores={"i1": 1.0, "i2": 1.0, "i3": 1.0},
        )

        wf = _make_task_ref_workflow()

        with patch("factory.task.TaskRef.resolve", return_value=fake_task):
            executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
            result = asyncio.run(executor.execute())

        assert result.success
        assert sorted(fake_task.setup_calls) == ["i1", "i2", "i3"]
        assert sorted(fake_task.prompt_calls) == ["i1", "i2", "i3"]
        assert sorted(fake_task.verify_calls) == ["i1", "i2", "i3"]

    def test_failing_setup_does_not_block_other_items(self, tmp_path: Path) -> None:
        """A failing setup() for one item must not prevent other items from running."""
        from factory.workflow.executor import WorkflowExecutor

        fake_task = _FakeTask(
            instances_data=[{"id": "ok1"}, {"id": "fail_setup"}, {"id": "ok2"}],
            verify_scores={"ok1": 1.0, "ok2": 0.9},
        )
        original_setup = fake_task.setup

        def failing_setup(instance, workspace):
            if instance.id == "fail_setup":
                raise RuntimeError("setup exploded")
            original_setup(instance, workspace)

        fake_task.setup = failing_setup

        wf = _make_task_ref_workflow()

        with patch("factory.task.TaskRef.resolve", return_value=fake_task):
            executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
            result = asyncio.run(executor.execute())

        assert result.success
        parsed = json.loads(result.node_outputs["data"])
        assert len(parsed) == 3

        failed = next(r for r in parsed if r["item_id"] == "fail_setup")
        assert failed["score"] == 0.0
        assert "error" in failed

        ok_items = [r for r in parsed if r["item_id"] != "fail_setup"]
        assert all(r["score"] > 0 for r in ok_items)


class TestStepWithDataNodeVerifyScores:
    def test_aggregates_verify_scores(self, tmp_path: Path) -> None:
        """_step_with_data_node should aggregate per-item verify scores, not binary."""
        from unittest.mock import AsyncMock

        from factory.inner_loop import InnerLoop
        from factory.workflow.executor import ExecutionResult

        wf = _make_task_ref_workflow()

        mock_result = ExecutionResult()
        mock_result.success = True
        mock_result.node_outputs = {
            "data": json.dumps([
                {"item_id": "a", "score": 0.8, "success": True},
                {"item_id": "b", "score": 0.4, "success": True},
            ])
        }

        loop = InnerLoop(project_dir=tmp_path, workflow=wf)

        with patch(
            "factory.workflow.executor.WorkflowExecutor.execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            record = loop._step_with_data_node()

        assert record.score_end == pytest.approx(0.6)

    def test_falls_back_to_binary_without_scores(self, tmp_path: Path) -> None:
        """Without per-item scores in output, falls back to exec_result.success."""
        from unittest.mock import AsyncMock

        from factory.inner_loop import InnerLoop
        from factory.workflow.executor import ExecutionResult

        wf = _make_data_workflow([DataItem(id="i")])

        mock_result = ExecutionResult()
        mock_result.success = True
        mock_result.node_outputs = {}

        loop = InnerLoop(project_dir=tmp_path, workflow=wf)

        with patch(
            "factory.workflow.executor.WorkflowExecutor.execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            record = loop._step_with_data_node()

        assert record.score_end == 1.0


# ── PR #1483 Review Fixes — additional tests ─────────────────────


class TestParallelismDefault:
    def test_parallelism_default_is_1(self) -> None:
        node = DataNode(
            id="dn",
            inline_items=[DataItem(id="i")],
            subgraph_entry="a",
            subgraph_exit="b",
        )
        assert node.parallelism == 1

    def test_parallelism_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DataNode(
                id="dn",
                inline_items=[DataItem(id="i")],
                subgraph_entry="a",
                subgraph_exit="b",
                parallelism=0,
            )


class TestNonexistentSourcePathRaises:
    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        wf = Workflow(
            name="missing",
            nodes={
                "data": DataNode(
                    id="data",
                    source_path=str(tmp_path / "nope"),
                    source_format="jsonl",
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.halted
        assert "source_path not found" in result.halt_reason


class TestEmptySourceWarns:
    def test_empty_inline_warns(self, tmp_path: Path) -> None:
        """Zero items after filtering should log a warning."""
        from factory.workflow.executor import WorkflowExecutor

        # Use split filter to exclude all items
        wf = Workflow(
            name="empty_test",
            nodes={
                "data": DataNode(
                    id="data",
                    inline_items=[DataItem(id="a", metadata={"split": "train"})],
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                    split="val",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        # This should succeed but with 0 items (and log a warning)
        result = asyncio.run(executor.execute())
        assert result.success
        parsed = json.loads(result.node_outputs["data"])
        assert len(parsed) == 0


class TestMalformedJsonlLineIsolated:
    def test_bad_line_skipped_good_lines_kept(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        jsonl_file = tmp_path / "items.jsonl"
        jsonl_file.write_text(
            '{"name": "first"}\n'
            'NOT VALID JSON\n'
            '{"name": "third"}\n'
        )

        wf = Workflow(
            name="jsonl_malformed",
            nodes={
                "data": DataNode(
                    id="data",
                    source_path=str(jsonl_file),
                    source_format="jsonl",
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.success
        parsed = json.loads(result.node_outputs["data"])
        # Two good lines kept, one bad line skipped
        assert len(parsed) == 2

    def test_all_lines_bad_raises(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        jsonl_file = tmp_path / "items.jsonl"
        jsonl_file.write_text("bad line 1\nbad line 2\n")

        wf = Workflow(
            name="jsonl_all_bad",
            nodes={
                "data": DataNode(
                    id="data",
                    source_path=str(jsonl_file),
                    source_format="jsonl",
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.halted
        assert "JSONL lines" in result.halt_reason


class TestShuffleDeterministic:
    def test_shuffle_with_seed(self, tmp_path: Path) -> None:
        """Same seed -> same order across runs."""
        from factory.workflow.executor import WorkflowExecutor

        items = [DataItem(id=str(i)) for i in range(20)]
        wf = Workflow(
            name="shuffle_seed",
            nodes={
                "data": DataNode(
                    id="data",
                    inline_items=items,
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                    shuffle=True,
                    shuffle_seed=42,
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )

        # Run twice with the same seed -- order must match
        executor1 = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result1 = asyncio.run(executor1.execute())
        ids1 = [r["item_id"] for r in json.loads(result1.node_outputs["data"])]

        executor2 = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result2 = asyncio.run(executor2.execute())
        ids2 = [r["item_id"] for r in json.loads(result2.node_outputs["data"])]

        assert ids1 == ids2
        # Must actually be shuffled (not original order)
        original_ids = [str(i) for i in range(20)]
        assert ids1 != original_ids

    def test_shuffle_from_run_id(self, tmp_path: Path) -> None:
        """Unseeded shuffle derives seed from node_id + run_id."""
        from factory.workflow.executor import WorkflowExecutor

        items = [DataItem(id=str(i)) for i in range(20)]
        wf = Workflow(
            name="shuffle_runid",
            nodes={
                "data": DataNode(
                    id="data",
                    inline_items=items,
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                    shuffle=True,
                    # No shuffle_seed -- uses run_id hash
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )

        executor1 = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result1 = asyncio.run(executor1.execute())
        ids1 = [r["item_id"] for r in json.loads(result1.node_outputs["data"])]

        # Different run_id -> potentially different order (different executor)
        executor2 = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result2 = asyncio.run(executor2.execute())
        ids2 = [r["item_id"] for r in json.loads(result2.node_outputs["data"])]

        # Both should be 20 items
        assert len(ids1) == 20
        assert len(ids2) == 20


class TestExplicitEdgeToSubgraphRejected:
    def test_validation_error_on_datanode_subgraph_edge(self) -> None:
        wf = Workflow(
            name="bad_edge",
            nodes={
                "data": DataNode(
                    id="data",
                    inline_items=[DataItem(id="i")],
                    subgraph_entry="sub_start",
                    subgraph_exit="sub_end",
                ),
                "sub_start": FnNode(id="sub_start", command="echo start"),
                "sub_end": FnNode(id="sub_end", command="echo end"),
            },
            edges=[
                Edge(source="data", target="sub_start"),
                Edge(source="sub_start", target="sub_end"),
            ],
            start_node="data",
        )
        issues = wf.validate_graph()
        assert any("double-execution" in i for i in issues)


class TestCurrentItemJsonWritten:
    def test_current_item_json_created_and_cleaned(self, tmp_path: Path) -> None:
        """current_item.json should exist during subgraph execution."""
        from factory.workflow.executor import WorkflowExecutor

        items = [DataItem(id="test_item", prompt="do it")]
        wf = _make_data_workflow(items)

        # Track whether current_item.json exists during execution
        observed: list[bool] = []
        original_execute = WorkflowExecutor.execute

        async def tracking_execute(self_inner):
            item_json = self_inner.project_path / ".factory" / "current_item.json"
            # For sub-executors (data_item workflows), check if file exists
            if self_inner.workflow.name.endswith("__data_item"):
                observed.append(item_json.exists())
            return await original_execute(self_inner)

        with patch.object(WorkflowExecutor, "execute", tracking_execute):
            executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
            result = asyncio.run(executor.execute())

        assert result.success
        # current_item.json should have existed during subgraph execution
        assert any(observed)
        # And it should be cleaned up after
        assert not (tmp_path / ".factory" / "current_item.json").exists()


class TestDirectScoreLookup:
    def test_finds_score_by_data_node_id(self, tmp_path: Path) -> None:
        """Score lookup uses DataNode ID directly, not sniffing."""
        from unittest.mock import AsyncMock

        from factory.inner_loop import InnerLoop
        from factory.workflow.executor import ExecutionResult

        wf = _make_data_workflow([DataItem(id="i", prompt="go")])

        mock_result = ExecutionResult()
        mock_result.success = True
        mock_result.node_outputs = {
            "data": json.dumps([
                {"item_id": "i", "score": 0.75, "passed": True},
            ]),
            "some_other_node": json.dumps({"unrelated": "data"}),
        }

        loop = InnerLoop(project_dir=tmp_path, workflow=wf)

        with patch(
            "factory.workflow.executor.WorkflowExecutor.execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            record = loop._step_with_data_node()

        assert record.score_end == pytest.approx(0.75)


class TestInstanceResultsPopulated:
    def test_instance_results_on_cycle_record(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock

        from factory.inner_loop import InnerLoop
        from factory.workflow.executor import ExecutionResult

        wf = _make_data_workflow([DataItem(id="a"), DataItem(id="b")])

        mock_result = ExecutionResult()
        mock_result.success = True
        mock_result.node_outputs = {
            "data": json.dumps([
                {"item_id": "a", "score": 0.9, "passed": True},
                {"item_id": "b", "score": 0.3, "passed": False},
            ])
        }

        loop = InnerLoop(project_dir=tmp_path, workflow=wf)

        with patch(
            "factory.workflow.executor.WorkflowExecutor.execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            record = loop._step_with_data_node()

        assert record.instance_results is not None
        assert len(record.instance_results) == 2
        assert record.instance_results[0]["instance_id"] == "a"
        assert record.instance_results[0]["score"] == 0.9
        assert record.instance_results[1]["instance_id"] == "b"
        assert record.instance_results[1]["passed"] is False


class _ComposeTestTask:
    """Task that satisfies TaskProtocol for compose() tests."""

    def __init__(self) -> None:
        from factory.task import ScoringContract, TaskDefinition

        self.definition = TaskDefinition(
            name="mock", scoring=ScoringContract(method="exit_code"),
        )
        self.scoring = self.definition.scoring
        self.constraints = None

    def instances(self):
        from factory.task import TaskInstance
        return [TaskInstance(id="inst-1")]

    def setup(self, instance: Any, workspace: Path) -> None:
        pass

    def prompt(self, instance: Any) -> str:
        return "test prompt"

    def verify(self, instance: Any, workspace: Path):
        from factory.task import VerifyResult
        return VerifyResult(passed=True, score=1.0)

    def get_evaluator(self) -> Any:
        return None


class TestComposeDataNodeWorkflow:
    def test_compose_succeeds_without_builder(self, tmp_path: Path) -> None:
        """compose() should NOT raise IncompatibleCompositionError for DataNode workflows
        even when the task requires HAS_BUILDER/CAN_RUN_TESTS."""
        from factory.compose import compose
        from factory.workflow.primitives import AgentNode, AgentRole

        # Create a DataNode workflow WITHOUT a builder agent
        wf = Workflow(
            name="eval_only",
            nodes={
                "generator": AgentNode(
                    id="generator",
                    role=AgentRole.RESEARCHER,
                    prompt_template="generate",
                ),
                "data": DataNode(
                    id="data",
                    inline_items=[DataItem(id="i1", prompt="test")],
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[
                Edge(source="generator", target="data"),
            ],
            start_node="generator",
        )

        task = _ComposeTestTask()

        # This should NOT raise IncompatibleCompositionError
        loop = compose(wf, task, tmp_path)
        assert loop is not None
        assert loop.workflow is wf


# ── PR #1483 Second Review Fixes — additional tests ─────────────


class TestFormatPathKindMismatch:
    """FIX 1: source_format vs path kind mismatch must raise ValueError."""

    def test_directory_format_on_file_raises(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        a_file = tmp_path / "not_a_dir.txt"
        a_file.write_text("hello")

        wf = Workflow(
            name="mismatch_test",
            nodes={
                "data": DataNode(
                    id="data",
                    source_path=str(a_file),
                    source_format="directory",
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.halted
        assert "requires a directory" in result.halt_reason

    def test_jsonl_format_on_directory_raises(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        a_dir = tmp_path / "not_a_file"
        a_dir.mkdir()

        wf = Workflow(
            name="mismatch_test",
            nodes={
                "data": DataNode(
                    id="data",
                    source_path=str(a_dir),
                    source_format="jsonl",
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.halted
        assert "requires a file" in result.halt_reason

    def test_csv_format_on_directory_raises(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        a_dir = tmp_path / "not_a_file"
        a_dir.mkdir()

        wf = Workflow(
            name="mismatch_test",
            nodes={
                "data": DataNode(
                    id="data",
                    source_path=str(a_dir),
                    source_format="csv",
                    subgraph_entry="sub",
                    subgraph_exit="sub",
                ),
                "sub": FnNode(id="sub", command="echo x"),
            },
            edges=[],
            start_node="data",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.halted
        assert "requires a file" in result.halt_reason


class TestWorkflowHasDataNode:
    """FIX 5: _workflow_has_data_node coverage."""

    def test_returns_true_with_data_node(self, tmp_path: Path) -> None:
        from factory.inner_loop import InnerLoop

        wf = _make_data_workflow([DataItem(id="i")])
        loop = InnerLoop(project_dir=tmp_path, workflow=wf)
        assert loop._workflow_has_data_node() is True

    def test_returns_false_without_data_node(self, tmp_path: Path) -> None:
        from factory.inner_loop import InnerLoop

        wf = Workflow(
            name="no_data",
            nodes={"a": FnNode(id="a", command="echo x")},
            edges=[],
            start_node="a",
        )
        loop = InnerLoop(project_dir=tmp_path, workflow=wf)
        assert loop._workflow_has_data_node() is False

    def test_caches_result(self, tmp_path: Path) -> None:
        from factory.inner_loop import InnerLoop

        wf = _make_data_workflow([DataItem(id="i")])
        loop = InnerLoop(project_dir=tmp_path, workflow=wf)
        result1 = loop._workflow_has_data_node()
        result2 = loop._workflow_has_data_node()
        assert result1 is result2 is True
        # Verify it was cached (attribute should be set)
        assert loop._has_data_node is True


class TestStepWithDataNodeCoverage:
    """FIX 5: _step_with_data_node happy path and failure coverage."""

    def test_happy_path(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock

        from factory.inner_loop import InnerLoop
        from factory.workflow.executor import ExecutionResult

        wf = _make_data_workflow([DataItem(id="i", prompt="go")])

        mock_result = ExecutionResult()
        mock_result.success = True
        mock_result.node_outputs = {
            "data": json.dumps([
                {"item_id": "i", "score": 0.85, "passed": True},
            ])
        }

        loop = InnerLoop(project_dir=tmp_path, workflow=wf)

        with patch(
            "factory.workflow.executor.WorkflowExecutor.execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            record = loop._step_with_data_node()

        assert record.score_end == pytest.approx(0.85)
        assert record.cycle_number == 1
        assert record.instance_results is not None
        assert len(record.instance_results) == 1
        assert record.instance_results[0]["instance_id"] == "i"

    def test_executor_failure_defaults_score(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock

        from factory.inner_loop import InnerLoop
        from factory.workflow.executor import ExecutionResult

        wf = _make_data_workflow([DataItem(id="i")])

        mock_result = ExecutionResult()
        mock_result.success = False
        mock_result.node_outputs = {}

        loop = InnerLoop(project_dir=tmp_path, workflow=wf)

        with patch(
            "factory.workflow.executor.WorkflowExecutor.execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            record = loop._step_with_data_node()

        assert record.score_end == 0.0
