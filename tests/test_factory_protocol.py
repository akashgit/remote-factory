"""Tests for Factory V2 Protocol — FactoryContract, StateSummary, qualitative evals, executor dispatch."""

from __future__ import annotations

import asyncio
from pathlib import Path

from factory.eval.qualitative import eval_research_quality, eval_strategy_quality
from factory.workflow.primitives import (
    Edge,
    FactoryContract,
    FnNode,
    Workflow,
)
from factory.workflow.protocol import (
    StateSummary,
    read_summary,
    summarize_factory_output,
    write_summary,
)


# ── FactoryContract model ──────────────────────────────────────


class TestFactoryContract:
    def test_creation_minimal(self) -> None:
        fc = FactoryContract(
            id="research_factory",
            eval_command="python eval_research.py",
            transform="research",
        )
        assert fc.id == "research_factory"
        assert fc.eval_command == "python eval_research.py"
        assert fc.transform == "research"
        assert fc.transform_type == "workflow"
        assert fc.input_contract == {}
        assert fc.output_contract == {}

    def test_creation_full(self) -> None:
        fc = FactoryContract(
            id="build_factory",
            input_contract={"spec": ".factory/strategy/current.md"},
            output_contract={"code": "src/main.py", "tests": "tests/test_main.py"},
            eval_command="python eval/score.py {project_path}",
            transform="echo done",
            transform_type="command",
            reads={".factory/strategy/current.md"},
            writes={"src/main.py", "tests/test_main.py"},
        )
        assert fc.transform_type == "command"
        assert len(fc.input_contract) == 1
        assert len(fc.output_contract) == 2

    def test_serialization_roundtrip(self) -> None:
        fc = FactoryContract(
            id="test_fc",
            input_contract={"a": "path/a.txt"},
            output_contract={"b": "path/b.txt"},
            eval_command="python eval.py",
            transform="my_workflow",
        )
        data = fc.model_dump()
        fc2 = FactoryContract.model_validate(data)
        assert fc2.id == fc.id
        assert fc2.input_contract == fc.input_contract
        assert fc2.output_contract == fc.output_contract
        assert fc2.eval_command == fc.eval_command
        assert fc2.transform == fc.transform
        assert fc2.transform_type == fc.transform_type

    def test_json_roundtrip(self) -> None:
        fc = FactoryContract(
            id="json_test",
            eval_command="eval.sh",
            transform="build",
        )
        json_str = fc.model_dump_json()
        fc2 = FactoryContract.model_validate_json(json_str)
        assert fc2.id == fc.id

    def test_in_nodetype_union(self) -> None:
        from factory.workflow.primitives import NodeType
        fc = FactoryContract(
            id="union_test",
            eval_command="eval.sh",
            transform="build",
        )
        assert isinstance(fc, FactoryContract)
        # FactoryContract should be accepted in dict[str, NodeType]
        nodes: dict[str, NodeType] = {"fc": fc}  # type: ignore[type-arg]
        assert "fc" in nodes


# ── StateSummary ───────────────────────────────────────────────


class TestStateSummary:
    def test_creation(self) -> None:
        ss = StateSummary(
            source_factory="research_factory",
            produced_files={"report": ".factory/strategy/research.md"},
            eval_score=0.85,
            eval_details={"coverage": 0.9, "depth": 0.8},
            summary="Research produced 1/1 output files.",
            metadata={"run_id": "abc123"},
        )
        assert ss.source_factory == "research_factory"
        assert ss.eval_score == 0.85

    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        ss = StateSummary(
            source_factory="test_factory",
            produced_files={"out": "output.txt"},
            eval_score=0.75,
            eval_details={"score": 0.75},
            summary="Test output.",
            metadata={"key": "value"},
        )
        path = tmp_path / ".factory" / "state" / "test.summary.json"
        write_summary(ss, path)
        assert path.exists()

        loaded = read_summary(path)
        assert loaded.source_factory == ss.source_factory
        assert loaded.eval_score == ss.eval_score
        assert loaded.produced_files == ss.produced_files
        assert loaded.summary == ss.summary
        assert loaded.metadata == ss.metadata

    def test_write_creates_directories(self, tmp_path: Path) -> None:
        ss = StateSummary(source_factory="dir_test")
        path = tmp_path / "deep" / "nested" / "summary.json"
        write_summary(ss, path)
        assert path.exists()

    def test_defaults(self) -> None:
        ss = StateSummary(source_factory="minimal")
        assert ss.produced_files == {}
        assert ss.eval_score is None
        assert ss.eval_details == {}
        assert ss.summary == ""
        assert ss.metadata == {}


# ── summarize_factory_output ──────────────────────────────────


class TestSummarizeFactoryOutput:
    def test_with_existing_files(self, tmp_path: Path) -> None:
        (tmp_path / "output.md").write_text("# Research\n\nFindings here.\n")
        (tmp_path / "data.json").write_text('{"key": "value"}')

        result = summarize_factory_output(
            factory_id="test_factory",
            output_contract={"report": "output.md", "data": "data.json"},
            eval_result={"score": 0.9, "details": {"accuracy": 0.95}},
            project_path=tmp_path,
        )

        assert result.source_factory == "test_factory"
        assert result.eval_score == 0.9
        assert "2/2" in result.summary
        meta = result.metadata["file_metadata"]
        assert meta["report"]["exists"] is True
        assert meta["data"]["exists"] is True
        assert meta["report"]["line_count"] == 4

    def test_with_missing_files(self, tmp_path: Path) -> None:
        result = summarize_factory_output(
            factory_id="missing_factory",
            output_contract={"report": "nonexistent.md"},
            eval_result=None,
            project_path=tmp_path,
        )

        assert result.eval_score is None
        assert "0/1" in result.summary
        meta = result.metadata["file_metadata"]
        assert meta["report"]["exists"] is False

    def test_with_no_eval(self, tmp_path: Path) -> None:
        (tmp_path / "out.txt").write_text("hello")
        result = summarize_factory_output(
            factory_id="no_eval",
            output_contract={"out": "out.txt"},
            eval_result=None,
            project_path=tmp_path,
        )
        assert result.eval_score is None
        assert "Eval score" not in result.summary


# ── eval_research_quality ─────────────────────────────────────


class TestEvalResearchQuality:
    def test_nonexistent_file(self, tmp_path: Path) -> None:
        result = eval_research_quality(tmp_path / "missing.md")
        assert result["score"] == 0.0
        assert result["details"]["exists"] is False

    def test_empty_file(self, tmp_path: Path) -> None:
        (tmp_path / "empty.md").write_text("")
        result = eval_research_quality(tmp_path / "empty.md")
        assert result["score"] == 0.0

    def test_good_research(self, tmp_path: Path) -> None:
        content = (
            "# Research Report\n\n"
            "## Similar Projects\n"
            "Found several similar projects:\n"
            "- Project A: https://github.com/user/project-a\n"
            "- Project B: https://example.com/project-b\n\n"
            "## Tech Stack\n"
            "Recommended stack includes Python + FastAPI.\n\n"
            "## Pitfalls\n"
            "Common issues include:\n"
            "- Over-engineering the initial scaffold\n"
            "- Not having evals early\n\n"
            "## Architecture\n"
            "The system should use a modular design.\n\n"
            "## Conclusion\n"
            "The project is feasible with the recommended stack.\n"
            + "x" * 300
        )
        (tmp_path / "research.md").write_text(content)
        result = eval_research_quality(tmp_path / "research.md")
        assert result["score"] > 0.7
        assert result["details"]["has_references"] is True
        assert result["details"]["section_count"] >= 5
        assert result["details"]["meets_length"] is True

    def test_minimal_research(self, tmp_path: Path) -> None:
        (tmp_path / "short.md").write_text("Some findings here.")
        result = eval_research_quality(tmp_path / "short.md")
        assert result["score"] < 0.5
        assert result["details"]["meets_length"] is False


# ── eval_strategy_quality ─────────────────────────────────────


class TestEvalStrategyQuality:
    def test_nonexistent_file(self, tmp_path: Path) -> None:
        result = eval_strategy_quality(tmp_path / "missing.md")
        assert result["score"] == 0.0

    def test_empty_file(self, tmp_path: Path) -> None:
        (tmp_path / "empty.md").write_text("")
        result = eval_strategy_quality(tmp_path / "empty.md")
        assert result["score"] == 0.0

    def test_good_strategy(self, tmp_path: Path) -> None:
        content = (
            "## Strategy\n\n"
            "### H1: Add structured logging\n"
            "- **Category:** EXPLOIT\n"
            "- **What:** Add structlog to all modules\n"
            "- **Why:** Improve observability\n"
            "- **Expected impact:** observability 0.3 -> 0.6\n"
            "- **Growth dimension:** observability\n\n"
            "### H2: New CLI command\n"
            "- **Category:** EXPLORE\n"
            "- **What:** Add factory export command\n"
            "- **Why:** Enable data export\n"
            "- **Expected impact:** capability_surface +0.1\n"
            "- **Growth dimension:** capability_surface\n"
        )
        (tmp_path / "strategy.md").write_text(content)
        result = eval_strategy_quality(tmp_path / "strategy.md")
        assert result["score"] > 0.8
        assert result["details"]["has_hypotheses"] is True
        assert result["details"]["has_growth_dimension"] is True

    def test_strategy_with_calendar_penalty(self, tmp_path: Path) -> None:
        content = (
            "### H1: Refactor auth\n"
            "- **Category:** FIX\n"
            "- **What:** Fix auth module\n"
            "- **Why:** Security\n"
            "- **Expected impact:** security +0.2\n"
            "- **Growth dimension:** capability_surface\n\n"
            "Timeline: 2-3 weeks\n"
        )
        (tmp_path / "strategy.md").write_text(content)
        result = eval_strategy_quality(tmp_path / "strategy.md")
        assert result["details"]["has_calendar_estimates"] is True
        assert result["details"]["calendar_penalty"] < 0

    def test_strategy_no_hypotheses(self, tmp_path: Path) -> None:
        content = "## Strategy\n\nJust some notes about the project.\n"
        (tmp_path / "strategy.md").write_text(content)
        result = eval_strategy_quality(tmp_path / "strategy.md")
        assert result["details"]["has_hypotheses"] is False


# ── Executor dispatch (dry-run) ───────────────────────────────


class TestExecutorFactoryContract:
    def test_dry_run_dispatch(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        fc = FactoryContract(
            id="test_fc",
            input_contract={"spec": "spec.md"},
            output_contract={"out": "out.txt"},
            eval_command="python eval.py",
            transform="echo hello",
            transform_type="command",
        )
        wf = Workflow(
            name="test_factory_contract",
            nodes={"test_fc": fc},
            edges=[],
            start_node="test_fc",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.success is True
        assert result.nodes_executed == 1
        assert "test_fc" in result.node_outputs

    def test_factory_contract_in_graph(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        fn = FnNode(
            id="setup",
            command="echo setup",
            writes={"spec.md"},
        )
        fc = FactoryContract(
            id="build_fc",
            input_contract={"spec": "spec.md"},
            output_contract={"out": "out.txt"},
            eval_command="python eval.py",
            transform="echo build",
            transform_type="command",
            reads={"spec.md"},
        )
        wf = Workflow(
            name="test_graph",
            nodes={"setup": fn, "build_fc": fc},
            edges=[Edge(source="setup", target="build_fc")],
            start_node="setup",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.success is True
        assert result.nodes_executed == 2


# ── Recursion test ────────────────────────────────────────────


class TestRecursion:
    def test_factory_containing_factory(self, tmp_path: Path) -> None:
        inner_fc = FactoryContract(
            id="inner_factory",
            eval_command="echo '{\"score\": 0.9}'",
            transform="echo inner",
            transform_type="command",
        )
        outer_fc = FactoryContract(
            id="outer_factory",
            eval_command="echo '{\"score\": 0.8}'",
            transform="echo outer",
            transform_type="command",
        )
        leaf = FnNode(id="leaf", command="echo leaf")

        wf = Workflow(
            name="recursive_test",
            nodes={
                "outer_factory": outer_fc,
                "inner_factory": inner_fc,
                "leaf": leaf,
            },
            edges=[
                Edge(source="outer_factory", target="inner_factory"),
                Edge(source="inner_factory", target="leaf"),
            ],
            start_node="outer_factory",
        )

        from factory.workflow.executor import WorkflowExecutor

        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.success is True
        assert result.nodes_executed == 3


# ── Validation ────────────────────────────────────────────────


class TestValidation:
    def test_valid_factory_contract(self) -> None:
        from factory.workflow.validation import validate_workflow

        fc = FactoryContract(
            id="valid_fc",
            input_contract={"spec": "path/to/spec.md"},
            output_contract={"out": "path/to/out.txt"},
            eval_command="python eval.py",
            transform="my_workflow",
        )
        wf = Workflow(
            name="validation_test",
            nodes={"valid_fc": fc},
            edges=[],
            start_node="valid_fc",
        )
        issues = validate_workflow(wf)
        assert len(issues) == 0

    def test_empty_eval_command(self) -> None:
        from factory.workflow.validation import validate_workflow

        fc = FactoryContract(
            id="bad_fc",
            input_contract={"spec": "spec.md"},
            output_contract={"out": "out.txt"},
            eval_command="",
            transform="my_workflow",
        )
        wf = Workflow(
            name="bad_eval_test",
            nodes={"bad_fc": fc},
            edges=[],
            start_node="bad_fc",
        )
        issues = validate_workflow(wf)
        assert any("empty eval_command" in i for i in issues)

    def test_malformed_contract_path(self) -> None:
        from factory.workflow.validation import validate_workflow

        fc = FactoryContract(
            id="bad_path_fc",
            input_contract={"spec": "  leading_space.md"},
            output_contract={"out": "out.txt"},
            eval_command="python eval.py",
            transform="build",
        )
        wf = Workflow(
            name="bad_path_test",
            nodes={"bad_path_fc": fc},
            edges=[],
            start_node="bad_path_fc",
        )
        issues = validate_workflow(wf)
        assert any("malformed path" in i for i in issues)

    def test_empty_transform(self) -> None:
        from factory.workflow.validation import validate_workflow

        fc = FactoryContract(
            id="empty_transform",
            eval_command="python eval.py",
            transform="",
            transform_type="workflow",
        )
        wf = Workflow(
            name="empty_transform_test",
            nodes={"empty_transform": fc},
            edges=[],
            start_node="empty_transform",
        )
        issues = validate_workflow(wf)
        assert any("empty transform" in i for i in issues)


# ── Skill export ──────────────────────────────────────────────


class TestSkillExport:
    def test_factory_contract_renders(self) -> None:
        from factory.workflow.skill_export import workflow_to_skill_md

        fc = FactoryContract(
            id="research_factory",
            input_contract={"spec": ".factory/strategy/current.md"},
            output_contract={"report": ".factory/strategy/research.md"},
            eval_command="python eval_research.py {project_path}",
            transform="research",
            reads={".factory/strategy/current.md"},
            writes={".factory/strategy/research.md"},
        )
        wf = Workflow(
            name="skill_test",
            nodes={"research_factory": fc},
            edges=[],
            start_node="research_factory",
        )
        md = workflow_to_skill_md(wf)
        assert "Peer Factory" in md
        assert "research_factory" in md
        assert "Input contract" in md
        assert "Output contract" in md
        assert "Eval:" in md
        assert "Transform:" in md
