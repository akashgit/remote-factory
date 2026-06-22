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
    Edge,
    FnNode,
    GateNode,
    SkillPhase,
    VerdictType,
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
        assert any("orphan" in i for i in issues)

    def test_extra_fields_forbidden(self) -> None:
        wf = self._simple_workflow()
        with pytest.raises(ValidationError):
            WorkflowSkill(name="test", workflow=wf, unknown="bad")  # type: ignore[call-arg]


# ── QA role ─────────────────────────────────────────────────────


class TestQARole:
    def test_qa_role_exists(self) -> None:
        assert AgentRole.QA == "qa"

    def test_qa_in_default_pool(self) -> None:
        assert "qa" in DEFAULT_AGENT_POOL
        assert DEFAULT_AGENT_POOL["qa"].role == AgentRole.QA
        assert DEFAULT_AGENT_POOL["qa"].model == "opus"

    def test_evaluator_removed_from_pool(self) -> None:
        assert "evaluator" not in DEFAULT_AGENT_POOL

    def test_evaluator_removed_from_enum(self) -> None:
        assert not hasattr(AgentRole, "EVALUATOR")

    def test_qa_agent_config(self) -> None:
        config = AgentConfig(role=AgentRole.QA, model="sonnet")
        assert config.role == AgentRole.QA

    def test_build_workflow_uses_qa(self) -> None:
        from factory.workflow.definitions import build_workflow
        wf = build_workflow()
        assert "qa" in wf.nodes
        assert "evaluator" not in wf.nodes

    def test_improve_workflow_uses_qa(self) -> None:
        from factory.workflow.definitions import improve_workflow
        wf = improve_workflow()
        assert "qa" in wf.nodes
        assert "evaluator" not in wf.nodes

    def test_research_workflow_uses_qa(self) -> None:
        from factory.workflow.definitions import research_workflow
        wf = research_workflow()
        assert "qa" in wf.nodes
        assert "evaluator" not in wf.nodes


# ── SkillRegistry ───────────────────────────────────────────────


class TestSkillRegistry:
    def test_create(self) -> None:
        registry = SkillRegistry.create()
        assert len(registry.names()) == 5

    def test_names(self) -> None:
        registry = SkillRegistry.create()
        names = set(registry.names())
        assert names == {"build", "design", "improve", "research", "meta"}

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
        skill = registry.by_name("Interactive")
        assert skill is not None
        assert skill.name == "design"

    def test_by_name_unknown(self) -> None:
        registry = SkillRegistry.create()
        assert registry.by_name("nonexistent") is None

    def test_select_no_repo(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.select(ProjectState.NO_REPO)
        assert skill is not None
        assert skill.name in {"build", "design"}

    def test_select_no_repo_interactive(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.select(ProjectState.NO_REPO, {"interactive": True})
        assert skill is not None
        assert skill.name == "design"

    def test_select_has_factory(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.select(ProjectState.HAS_FACTORY)
        assert skill is not None
        assert skill.name == "improve"

    def test_select_has_factory_with_research_target(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.select(ProjectState.HAS_FACTORY, {"research_target": "accuracy"})
        assert skill is not None
        assert skill.name == "research"

    def test_select_meta(self) -> None:
        registry = SkillRegistry.create()
        skill = registry.select(ProjectState.HAS_FACTORY, {"mode": "meta"})
        assert skill is not None
        assert skill.name == "meta"

    def test_catalog_markdown(self) -> None:
        registry = SkillRegistry.create()
        catalog = registry.catalog()
        assert "# Available Workflow Skills" in catalog
        assert "## build" in catalog
        assert "## improve" in catalog
        assert "**Description:**" in catalog
        assert "**Phases:**" in catalog
        assert "**Aliases:**" in catalog

    def test_catalog_contains_all_skills(self) -> None:
        registry = SkillRegistry.create()
        catalog = registry.catalog()
        for name in registry.names():
            assert f"## {name}" in catalog


# ── register_all returns WorkflowSkill ──────────────────────────


class TestRegisterAllSkills:
    def test_returns_workflow_skills(self) -> None:
        skills = register_all()
        for name, skill in skills.items():
            assert isinstance(skill, WorkflowSkill), f"{name} is not a WorkflowSkill"

    def test_all_have_descriptions(self) -> None:
        skills = register_all()
        for name, skill in skills.items():
            assert skill.description, f"{name} missing description"

    def test_all_have_phases(self) -> None:
        skills = register_all()
        for name, skill in skills.items():
            assert len(skill.phases) >= 2, f"{name} has fewer than 2 phases"

    def test_all_have_trigger_descriptions(self) -> None:
        skills = register_all()
        for name, skill in skills.items():
            assert skill.trigger_description, f"{name} missing trigger_description"

    def test_all_workflows_validate(self) -> None:
        skills = register_all()
        for name, skill in skills.items():
            issues = skill.validate_graph()
            assert issues == [], f"{name} has validation issues: {issues}"


# ── GateNode extensions ────────────────────────────────────────


class TestGateNodeExtensions:
    def test_criteria_file_field(self) -> None:
        gate = GateNode(id="g", criteria_file="builder-review.md")
        assert gate.criteria_file == "builder-review.md"

    def test_user_visible_default(self) -> None:
        gate = GateNode(id="g")
        assert gate.user_visible is False

    def test_skippable_default(self) -> None:
        gate = GateNode(id="g")
        assert gate.skippable is False

    def test_user_visible_true(self) -> None:
        gate = GateNode(id="g", user_visible=True)
        assert gate.user_visible is True

    def test_skippable_true(self) -> None:
        gate = GateNode(id="g", skippable=True)
        assert gate.skippable is True

    def test_criteria_file_in_build_workflow(self) -> None:
        from factory.workflow.definitions import build_workflow
        wf = build_workflow()
        gate_research = wf.nodes["gate_research"]
        assert isinstance(gate_research, GateNode)
        assert gate_research.criteria_file == "researcher-review.md"

        gate_strategy = wf.nodes["gate_strategy"]
        assert isinstance(gate_strategy, GateNode)
        assert gate_strategy.criteria_file == "strategist-review.md"

        gate_build = wf.nodes["gate_build"]
        assert isinstance(gate_build, GateNode)
        assert gate_build.criteria_file == "builder-review.md"


# ── Gate criteria file loading ──────────────────────────────────


class TestGateCriteriaLoading:
    def test_criteria_files_exist(self) -> None:
        criteria_dir = Path(__file__).parent.parent / "factory" / "workflow" / "gate_criteria"
        expected = [
            "researcher-review.md",
            "strategist-review.md",
            "builder-review.md",
            "qa-review.md",
            "surface-check.md",
        ]
        for name in expected:
            path = criteria_dir / name
            assert path.is_file(), f"Missing criteria file: {name}"

    def test_criteria_content_has_checklist(self) -> None:
        criteria_dir = Path(__file__).parent.parent / "factory" / "workflow" / "gate_criteria"
        for md_file in criteria_dir.glob("*.md"):
            content = md_file.read_text()
            assert "## Checklist" in content, f"{md_file.name} missing ## Checklist"
            assert "- [ ]" in content, f"{md_file.name} missing checklist items"

    def test_criteria_has_project_path_placeholder(self) -> None:
        criteria_dir = Path(__file__).parent.parent / "factory" / "workflow" / "gate_criteria"
        for md_file in criteria_dir.glob("*.md"):
            content = md_file.read_text()
            assert "{project_path}" in content, f"{md_file.name} missing {{project_path}}"

    def test_executor_loads_criteria(self) -> None:
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.definitions import build_workflow

        wf = build_workflow()
        executor = WorkflowExecutor(wf, Path("/tmp/test"), dry_run=True)
        content = executor._load_criteria_file("builder-review.md")
        assert "Builder Review Criteria" in content
        assert "/tmp/test" in content

    def test_executor_missing_criteria_returns_empty(self) -> None:
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.definitions import build_workflow

        wf = build_workflow()
        executor = WorkflowExecutor(wf, Path("/tmp/test"), dry_run=True)
        content = executor._load_criteria_file("nonexistent.md")
        assert content == ""

    def test_gate_prompt_includes_criteria(self) -> None:
        from factory.workflow.executor import WorkflowExecutor

        gate = GateNode(
            id="gate_build",
            evaluator_type="agent",
            evaluator_role=AgentRole.CEO,
            reads={".factory/reviews/builder-latest.md"},
            criteria_file="builder-review.md",
        )
        wf = Workflow(
            name="test",
            nodes={"gate_build": gate},
            edges=[],
            start_node="gate_build",
        )
        executor = WorkflowExecutor(wf, Path("/tmp/test"), dry_run=True)
        prompt = executor._build_gate_prompt(gate)
        assert "## Review Criteria" in prompt
        assert "Builder Review Criteria" in prompt


# ── Project context injection ───────────────────────────────────


class TestProjectContextInjection:
    def test_agent_prompt_substitution(self) -> None:
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import AgentNode

        node = AgentNode(
            id="builder",
            role=AgentRole.BUILDER,
            prompt_template="Build at {project_path} for {project_idea}",
        )
        wf = Workflow(
            name="test",
            nodes={"builder": node},
            edges=[],
            start_node="builder",
        )
        executor = WorkflowExecutor(
            wf, Path("/tmp/proj"), dry_run=True,
            context={"project_idea": "weather CLI"},
        )
        prompt = executor._build_agent_prompt(node)
        assert "/tmp/proj" in prompt
        assert "weather CLI" in prompt

    def test_missing_context_defaults_empty(self) -> None:
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import AgentNode

        node = AgentNode(
            id="builder",
            role=AgentRole.BUILDER,
            prompt_template="Focus: {focus_directive}",
        )
        wf = Workflow(
            name="test",
            nodes={"builder": node},
            edges=[],
            start_node="builder",
        )
        executor = WorkflowExecutor(wf, Path("/tmp/proj"), dry_run=True)
        prompt = executor._build_agent_prompt(node)
        assert "Focus: " in prompt
        assert "{focus_directive}" not in prompt


# ── User gate ───────────────────────────────────────────────────


class TestUserGate:
    async def test_dry_run_skips_user_gate(self) -> None:
        from factory.workflow.executor import WorkflowExecutor

        wf = Workflow(
            name="test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(id="gate", evaluator_type="user", reads={"a.txt"}),
                "b": FnNode(id="b", command="echo b"),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="b", condition=VerdictType.PROCEED),
            ],
            start_node="a",
        )
        executor = WorkflowExecutor(wf, Path("/tmp/test"), dry_run=True)
        result = await executor.execute()
        assert result.success

    async def test_headless_converts_user_gate_to_agent(self, tmp_path: Path) -> None:
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import Verdict

        agent_gate_called = False

        async def mock_agent_gate(node: GateNode) -> Verdict:
            nonlocal agent_gate_called
            agent_gate_called = True
            return Verdict.proceed()

        wf = Workflow(
            name="test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(id="gate", evaluator_type="user", reads={"a.txt"}),
                "b": FnNode(id="b", command="echo b"),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="b", condition=VerdictType.PROCEED),
            ],
            start_node="a",
        )
        executor = WorkflowExecutor(wf, tmp_path, dry_run=False, headless=True)
        executor._evaluate_agent_gate = mock_agent_gate  # type: ignore[assignment]
        await executor.execute()
        assert agent_gate_called


# ── CLI catalog command ─────────────────────────────────────────


class TestCLICatalog:
    def test_catalog_command(self) -> None:
        import subprocess
        result = subprocess.run(
            ["python", "-m", "factory", "workflow", "catalog"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "# Available Workflow Skills" in result.stdout
        assert "## build" in result.stdout
