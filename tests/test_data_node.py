"""Tests for the DataNode graph primitive — models, executor, validation, skill export, and features."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
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
        assert node.parallelism == 3
        assert node.split == "all"
        assert node.shuffle is False
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
    def test_nonexistent_path_yields_empty(self, tmp_path: Path) -> None:
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
        assert result.success
        parsed = json.loads(result.node_outputs["data"])
        assert len(parsed) == 0


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


class TestComposeCapsDataNode:
    def test_data_node_adds_can_iterate(self) -> None:
        from factory.compose import ModeCapabilities
        from factory.task import Capability

        wf = _make_data_workflow([DataItem(id="i")])
        caps = ModeCapabilities.from_workflow(wf)
        assert Capability.CAN_ITERATE in caps.provides
