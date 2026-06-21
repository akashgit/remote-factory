"""Tests for WorkflowSkill, SkillPhase, SkillRegistry, QA role, gate criteria, and CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from factory.models import ProjectState
from factory.workflow.definitions import register_all
from factory.workflow.primitives import (
    AgentConfig,
    AgentRole,
    DEFAULT_AGENT_POOL,
    FnNode,
    GateNode,
    SkillPhase,
    Workflow,
    WorkflowSkill,
)
from factory.workflow.registry import SkillRegistry


# ── SkillPhase ──────────────────────────────────────────────────


class TestSkillPhase:
    def test_create_minimal(self) -> None:
        p = SkillPhase(name="research")
        assert p.name == "research"
        assert p.description == ""

    def test_create_with_description(self) -> None:
        p = SkillPhase(name="build", description="Implement the plan")
        assert p.name == "build"
        assert p.description == "Implement the plan"

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SkillPhase(name="test", unknown="bad")  # type: ignore[call-arg]

    def test_serialize_roundtrip(self) -> None:
        p = SkillPhase(name="strategy", description="Generate hypotheses")
        data = p.model_dump()
        p2 = SkillPhase.model_validate(data)
        assert p2.name == p.name
        assert p2.description == p.description


# ── WorkflowSkill ───────────────────────────────────────────────


class TestWorkflowSkill:
    def _simple_workflow(self) -> Workflow:
        return Workflow(
            name="test",
            nodes={"a": FnNode(id="a", command="echo a")},
            edges=[],
            start_node="a",
        )

    def test_create_minimal(self) -> None:
        wf = self._simple_workflow()
        skill = WorkflowSkill(name="test", workflow=wf)
        assert skill.name == "test"
        assert skill.workflow is wf
        assert skill.description == ""
        assert skill.aliases == []
        assert skill.phases == []

    def test_create_with_metadata(self) -> None:
        wf = self._simple_workflow()
        skill = WorkflowSkill(
            name="build",
            workflow=wf,
            description="Build a new project",
            trigger_description="Triggers on NO_REPO",
            phases=[SkillPhase(name="research"), SkillPhase(name="build")],
            inputs=["idea"],
            outputs=["project"],
            success_criteria="Tests pass",
            aliases=["new", "create"],
        )
        assert skill.description == "Build a new project"
        assert len(skill.phases) == 2
        assert skill.phases[0].name == "research"
        assert skill.aliases == ["new", "create"]
        assert skill.inputs == ["idea"]
        assert skill.outputs == ["project"]
        assert skill.success_criteria == "Tests pass"

    def test_validate_graph_delegates(self) -> None:
        wf = Workflow(
            name="test",
            nodes={
                "a": FnNode(id="a", command="echo a"),
                "orphan": FnNode(id="orphan", command="echo orphan"),
            },
            edges=[],
            start_node="a",
        )
        skill = WorkflowSkill(name="test", workflow=wf)
        issues = skill.validate_graph()
        assert any("orphan" in i and "unreachable" in i for i in issues)

    def test_validate_graph_clean(self) -> None:
        wf = self._simple_workflow()
        skill = WorkflowSkill(name="test", workflow=wf)
        issues = skill.validate_graph()
        assert issues == []

    def test_extra_fields_forbidden(self) -> None:
        wf = self._simple_workflow()
        with pytest.raises(ValidationError):
            WorkflowSkill(name="test", workflow=wf, unknown="bad")  # type: ignore[call-arg]

    def test_serialize_roundtrip(self) -> None:
        wf = self._simple_workflow()
        skill = WorkflowSkill(
            name="test",
            workflow=wf,
            description="Test skill",
            aliases=["t"],
        )
        data = skill.model_dump()
        assert data["name"] == "test"
        assert data["description"] == "Test skill"
        assert data["aliases"] == ["t"]


# ── QA AgentRole ────────────────────────────────────────────────


class TestQARole:
    def test_qa_enum_exists(self) -> None:
        assert AgentRole.QA == "qa"
        assert AgentRole.QA.value == "qa"

    def test_qa_in_default_pool(self) -> None:
        assert "qa" in DEFAULT_AGENT_POOL
        qa_config = DEFAULT_AGENT_POOL["qa"]
        assert qa_config.role == AgentRole.QA
        assert qa_config.model == "opus"

    def test_qa_config_valid(self) -> None:
        config = AgentConfig(role=AgentRole.QA, model="opus")
        assert config.role == AgentRole.QA

    def test_existing_roles_preserved(self) -> None:
        assert AgentRole.RESEARCHER.value == "researcher"
        assert AgentRole.REVIEWER.value == "reviewer"
        assert AgentRole.EVALUATOR.value == "evaluator"
        assert AgentRole.BUILDER.value == "builder"
        assert AgentRole.CEO.value == "ceo"


# ── GateNode new fields ────────────────────────────────────────


class TestGateNodeExtensions:
    def test_defaults_backward_compat(self) -> None:
        g = GateNode(id="gate1", evaluator_type="agent", evaluator_role=AgentRole.CEO)
        assert g.user_visible is False
        assert g.criteria_file is None
        assert g.skippable is False

    def test_with_criteria_file(self) -> None:
        g = GateNode(
            id="gate1",
            evaluator_type="agent",
            evaluator_role=AgentRole.CEO,
            criteria_file="builder-review.md",
        )
        assert g.criteria_file == "builder-review.md"

    def test_with_user_visible(self) -> None:
        g = GateNode(
            id="gate1",
            evaluator_type="user",
            user_visible=True,
        )
        assert g.user_visible is True

    def test_with_skippable(self) -> None:
        g = GateNode(
            id="gate1",
            evaluator_type="fn",
            evaluator_command="echo ok",
            skippable=True,
        )
        assert g.skippable is True

    def test_serialize_roundtrip(self) -> None:
        g = GateNode(
            id="gate1",
            evaluator_type="agent",
            evaluator_role=AgentRole.CEO,
            criteria_file="qa-review.md",
            user_visible=True,
            skippable=True,
        )
        data = g.model_dump()
        g2 = GateNode.model_validate(data)
        assert g2.criteria_file == "qa-review.md"
        assert g2.user_visible is True
        assert g2.skippable is True


# ── SkillRegistry ───────────────────────────────────────────────


class TestSkillRegistry:
    def test_create_discovers_all(self) -> None:
        registry = SkillRegistry.create()
        names = registry.names()
        assert len(names) == 5
        assert set(names) == {"build", "design", "improve", "research", "meta"}

    def test_by_name_exact(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.by_name("build")
        assert skill is not None
        assert skill.name == "build"

    def test_by_name_case_insensitive(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.by_name("BUILD")
        assert skill is not None
        assert skill.name == "build"

    def test_by_name_alias(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.by_name("new")
        assert skill is not None
        assert skill.name == "build"

    def test_by_name_alias_case_insensitive(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.by_name("CREATE")
        assert skill is not None
        assert skill.name == "build"

    def test_by_name_unknown(self) -> None:
        registry = SkillRegistry.create()
        assert registry.by_name("nonexistent") is None

    def test_select_no_repo(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.select(ProjectState.NO_REPO)
        assert skill is not None
        assert skill.name == "build"

    def test_select_has_factory(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.select(ProjectState.HAS_FACTORY)
        assert skill is not None
        assert skill.name == "improve"

    def test_select_has_factory_research(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.select(ProjectState.HAS_FACTORY, {"research_target": "accuracy"})
        assert skill is not None
        assert skill.name == "research"

    def test_select_meta(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.select(ProjectState.HAS_FACTORY, {"mode": "meta"})
        assert skill is not None
        assert skill.name == "meta"

    def test_select_interactive(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.select(ProjectState.NO_REPO, {"interactive": True})
        assert skill is not None
        assert skill.name == "design"

    def test_catalog_markdown(self) -> None:
        registry = SkillRegistry.create()
        catalog = registry.catalog()
        assert "# Available Workflow Skills" in catalog
        assert "## build" in catalog
        assert "## improve" in catalog
        assert "## research" in catalog
        assert "## meta" in catalog
        assert "## design" in catalog
        assert "**Description:**" in catalog
        assert "**Trigger:**" in catalog
        assert "**Phases:**" in catalog
        assert "**Aliases:**" in catalog

    def test_catalog_contains_all_aliases(self) -> None:
        registry = SkillRegistry.create()
        catalog = registry.catalog()
        assert "new, create" in catalog
        assert "interactive, discuss" in catalog
        assert "evolve, iterate" in catalog

    def test_names_list(self) -> None:
        registry = SkillRegistry.create()
        names = registry.names()
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)


# ── register_all returns WorkflowSkill ──────────────────────────


class TestRegisterAllSkills:
    def test_returns_workflow_skills(self) -> None:
        skills = register_all()
        for name, skill in skills.items():
            assert isinstance(skill, WorkflowSkill), f"{name} should be WorkflowSkill"

    def test_all_have_descriptions(self) -> None:
        skills = register_all()
        for name, skill in skills.items():
            assert skill.description, f"{name} missing description"

    def test_all_have_trigger_descriptions(self) -> None:
        skills = register_all()
        for name, skill in skills.items():
            assert skill.trigger_description, f"{name} missing trigger_description"

    def test_all_have_phases(self) -> None:
        skills = register_all()
        for name, skill in skills.items():
            assert len(skill.phases) > 0, f"{name} missing phases"

    def test_all_have_aliases(self) -> None:
        skills = register_all()
        for name, skill in skills.items():
            assert len(skill.aliases) > 0, f"{name} missing aliases"

    def test_all_validate(self) -> None:
        skills = register_all()
        for name, skill in skills.items():
            issues = skill.validate_graph()
            assert issues == [], f"{name} has validation issues: {issues}"

    def test_workflow_accessible(self) -> None:
        skills = register_all()
        for name, skill in skills.items():
            wf = skill.workflow
            assert isinstance(wf, Workflow)
            assert wf.name == name


# ── Gate criteria files ─────────────────────────────────────────


class TestGateCriteriaFiles:
    def _criteria_dir(self) -> Path:
        return Path(__file__).parent.parent / "factory" / "workflow" / "gate_criteria"

    def test_criteria_dir_exists(self) -> None:
        assert self._criteria_dir().is_dir()

    def test_all_five_files_exist(self) -> None:
        expected = [
            "researcher-review.md",
            "strategist-review.md",
            "builder-review.md",
            "qa-review.md",
            "surface-check.md",
        ]
        for fname in expected:
            path = self._criteria_dir() / fname
            assert path.is_file(), f"Missing criteria file: {fname}"

    def test_files_have_content(self) -> None:
        for path in self._criteria_dir().glob("*.md"):
            content = path.read_text()
            assert len(content) > 50, f"{path.name} is too short"
            assert "## Checklist" in content, f"{path.name} missing Checklist section"

    def test_files_have_template_vars(self) -> None:
        for path in self._criteria_dir().glob("*.md"):
            content = path.read_text()
            assert "{project_path}" in content, f"{path.name} missing {{project_path}} template var"

    def test_build_workflow_gates_reference_criteria(self) -> None:
        from factory.workflow.definitions import build_workflow

        wf = build_workflow()
        gate_research = wf.nodes.get("gate_research")
        gate_strategy = wf.nodes.get("gate_strategy")
        gate_build = wf.nodes.get("gate_build")

        assert isinstance(gate_research, GateNode)
        assert gate_research.criteria_file == "researcher-review.md"

        assert isinstance(gate_strategy, GateNode)
        assert gate_strategy.criteria_file == "strategist-review.md"

        assert isinstance(gate_build, GateNode)
        assert gate_build.criteria_file == "builder-review.md"

    def test_improve_workflow_gates_reference_criteria(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        gate_research = wf.nodes.get("gate_research")
        gate_strategy = wf.nodes.get("gate_strategy")
        gate_build = wf.nodes.get("gate_build")

        assert isinstance(gate_research, GateNode)
        assert gate_research.criteria_file == "researcher-review.md"

        assert isinstance(gate_strategy, GateNode)
        assert gate_strategy.criteria_file == "strategist-review.md"

        assert isinstance(gate_build, GateNode)
        assert gate_build.criteria_file == "builder-review.md"


# ── _build_gate_prompt with criteria ────────────────────────────


class TestBuildGatePromptCriteria:
    def test_prompt_includes_criteria_content(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        gate = GateNode(
            id="gate_test",
            evaluator_type="agent",
            evaluator_role=AgentRole.CEO,
            reads={"output.md"},
            criteria_file="builder-review.md",
        )
        wf = Workflow(
            name="test",
            nodes={"gate_test": gate},
            edges=[],
            start_node="gate_test",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        prompt = executor._build_gate_prompt(gate)
        assert "Review Criteria" in prompt
        assert "Builder Review Criteria" in prompt

    def test_prompt_substitutes_project_path(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        gate = GateNode(
            id="gate_test",
            evaluator_type="agent",
            evaluator_role=AgentRole.CEO,
            criteria_file="researcher-review.md",
        )
        wf = Workflow(
            name="test",
            nodes={"gate_test": gate},
            edges=[],
            start_node="gate_test",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        prompt = executor._build_gate_prompt(gate)
        assert str(tmp_path) in prompt

    def test_prompt_fallback_without_criteria(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        gate = GateNode(
            id="gate_test",
            evaluator_type="agent",
            evaluator_role=AgentRole.CEO,
        )
        wf = Workflow(
            name="test",
            nodes={"gate_test": gate},
            edges=[],
            start_node="gate_test",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        prompt = executor._build_gate_prompt(gate)
        assert "Review Criteria" not in prompt
        assert "reviewing the output" in prompt

    def test_prompt_fallback_missing_file(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor

        gate = GateNode(
            id="gate_test",
            evaluator_type="agent",
            evaluator_role=AgentRole.CEO,
            criteria_file="nonexistent-file.md",
        )
        wf = Workflow(
            name="test",
            nodes={"gate_test": gate},
            edges=[],
            start_node="gate_test",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        prompt = executor._build_gate_prompt(gate)
        assert "Review Criteria" not in prompt


# ── CLI commands ────────────────────────────────────────────────


class TestWorkflowCLI:
    def test_list_command(self, capsys: pytest.CaptureFixture[str]) -> None:
        import argparse

        from factory.workflow.cli import cmd_workflow

        args = argparse.Namespace(workflow_command="list")
        code = cmd_workflow(args)
        assert code == 0
        out = capsys.readouterr().out
        assert "build" in out
        assert "improve" in out
        assert "Phases" in out

    def test_catalog_command(self, capsys: pytest.CaptureFixture[str]) -> None:
        import argparse

        from factory.workflow.cli import cmd_workflow

        args = argparse.Namespace(workflow_command="catalog")
        code = cmd_workflow(args)
        assert code == 0
        out = capsys.readouterr().out
        assert "# Available Workflow Skills" in out
        assert "## build" in out
        assert "**Phases:**" in out

    def test_show_command(self, capsys: pytest.CaptureFixture[str]) -> None:
        import argparse

        from factory.workflow.cli import cmd_workflow

        args = argparse.Namespace(workflow_command="show", name="improve")
        code = cmd_workflow(args)
        assert code == 0
        out = capsys.readouterr().out
        assert "Workflow: improve" in out
        assert "Desc:" in out
        assert "Aliases:" in out
        assert "Phases:" in out

    def test_show_unknown(self, capsys: pytest.CaptureFixture[str]) -> None:
        import argparse

        from factory.workflow.cli import cmd_workflow

        args = argparse.Namespace(workflow_command="show", name="nonexistent")
        code = cmd_workflow(args)
        assert code == 1

    def test_validate_command(self, capsys: pytest.CaptureFixture[str]) -> None:
        import argparse

        from factory.workflow.cli import cmd_workflow

        args = argparse.Namespace(workflow_command="validate", name="build")
        code = cmd_workflow(args)
        assert code == 0
        out = capsys.readouterr().out
        assert "VALID" in out

    def test_no_subcommand(self, capsys: pytest.CaptureFixture[str]) -> None:
        import argparse

        from factory.workflow.cli import cmd_workflow

        args = argparse.Namespace(workflow_command=None)
        code = cmd_workflow(args)
        assert code == 1
