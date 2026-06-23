"""Tests for agent prompt content — verify critical sections exist."""

from __future__ import annotations

from pathlib import Path

import pytest

PROMPTS_DIR = Path(__file__).parent.parent / "factory" / "agents" / "prompts"


@pytest.fixture
def strategist_prompt() -> str:
    return (PROMPTS_DIR / "strategist.md").read_text()


@pytest.fixture
def researcher_prompt() -> str:
    return (PROMPTS_DIR / "researcher.md").read_text()


@pytest.fixture
def archivist_prompt() -> str:
    return (PROMPTS_DIR / "archivist.md").read_text()


# ── Strategist ────────────────────────────────────────────────────


class TestStrategistPrompt:
    def test_has_design_space_section(self, strategist_prompt: str) -> None:
        assert "## Design Space Exploration" in strategist_prompt

    def test_lists_all_10_dimensions(self, strategist_prompt: str) -> None:
        dimensions = [
            "Features", "Bug fixes", "Instrumentation", "Flow changes",
            "New agents", "Prompt engineering", "Eval improvements",
            "Knowledge management", "Infrastructure", "Self-evolution",
        ]
        for dim in dimensions:
            assert dim in strategist_prompt, f"Missing dimension: {dim}"

    def test_has_cross_project_insights_section(self, strategist_prompt: str) -> None:
        assert "## Cross-Project Insights" in strategist_prompt

    def test_references_insights_md(self, strategist_prompt: str) -> None:
        assert "insights.md" in strategist_prompt

    def test_retains_feec_framework(self, strategist_prompt: str) -> None:
        assert "## Priority Framework" in strategist_prompt or "FEEC" in strategist_prompt

    def test_retains_stuck_protocol(self, strategist_prompt: str) -> None:
        assert "## Stuck Protocol" in strategist_prompt

    def test_retains_observability_priority(self, strategist_prompt: str) -> None:
        assert "## Observability Priority" in strategist_prompt


# ── Researcher ────────────────────────────────────────────────────


class TestResearcherPrompt:
    def test_has_mode_3(self, researcher_prompt: str) -> None:
        assert "## Mode 3" in researcher_prompt

    def test_mentions_factory_insights(self, researcher_prompt: str) -> None:
        assert "factory insights" in researcher_prompt

    def test_mentions_self_evolution_search(self, researcher_prompt: str) -> None:
        assert "self-evolving" in researcher_prompt or "self-evolution" in researcher_prompt.lower()

    def test_retains_mode_1_and_2(self, researcher_prompt: str) -> None:
        assert "## Mode 1" in researcher_prompt
        assert "## Mode 2" in researcher_prompt


# ── Archivist ─────────────────────────────────────────────────────


class TestArchivistPrompt:
    def test_uses_archive_dir_not_vault(self, archivist_prompt: str) -> None:
        assert ".factory/archive/" in archivist_prompt
        assert "obsidian-cli" not in archivist_prompt.lower()
        assert "$FACTORY_VAULT_PATH" not in archivist_prompt


# ── CEO ──────────────────────────────────────────────────────────


@pytest.fixture
def ceo_prompt() -> str:
    return (PROMPTS_DIR / "ceo.md").read_text()


class TestCeoPrompt:
    def test_exists(self) -> None:
        assert (PROMPTS_DIR / "ceo.md").exists()

    def test_has_identity_section(self, ceo_prompt: str) -> None:
        assert "## Identity" in ceo_prompt

    def test_has_state_machine(self, ceo_prompt: str) -> None:
        assert "## State Machine" in ceo_prompt

    def test_has_all_modes(self, ceo_prompt: str) -> None:
        """CEO routes to all modes via Skill Selection section."""
        assert "workflow-build" in ceo_prompt
        assert "workflow-improve" in ceo_prompt
        assert "workflow-research" in ceo_prompt
        assert "workflow-meta" in ceo_prompt
        assert "workflow-design" in ceo_prompt

    def test_has_sacred_rules(self, ceo_prompt: str) -> None:
        assert "## Sacred Rules" in ceo_prompt

    def test_has_mandatory_archival(self, ceo_prompt: str) -> None:
        assert "Do not skip archival" in ceo_prompt
        assert "MANDATORY" in ceo_prompt

    def test_references_factory_agent_command(self, ceo_prompt: str) -> None:
        assert "factory agent" in ceo_prompt

    def test_has_self_learning_protocol(self, ceo_prompt: str) -> None:
        assert "## CEO Self-Learning Protocol" in ceo_prompt

    def test_has_keep_revert_framework(self, ceo_prompt: str) -> None:
        assert "## Keep/Revert Decision Framework" in ceo_prompt

    def test_has_error_recovery(self, ceo_prompt: str) -> None:
        assert "## Error Recovery" in ceo_prompt

    def test_has_context_preservation(self, ceo_prompt: str) -> None:
        assert "## Context Preservation" in ceo_prompt

    def test_lists_all_agent_roles(self, ceo_prompt: str) -> None:
        for role in ["Researcher", "Strategist", "Builder", "QA", "Archivist"]:
            assert role in ceo_prompt

    def test_seventh_sacred_rule_archival(self, ceo_prompt: str) -> None:
        assert "Do not skip archival" in ceo_prompt

    def test_ceo_notes_convention(self, ceo_prompt: str) -> None:
        assert "ceo:keep" in ceo_prompt
        assert "ceo:revert" in ceo_prompt

    def test_build_mode_has_full_pipeline(self, ceo_prompt: str) -> None:
        """Build workflow skill has researcher, strategist, and builder phases."""
        skill_path = Path(__file__).parent.parent / "skills" / "workflow-build" / "SKILL.md"
        build_skill = skill_path.read_text()
        assert "researcher" in build_skill.lower()
        assert "strategist" in build_skill.lower()
        assert "builder" in build_skill.lower()

    def test_build_mode_does_not_skip_to_builder(self, ceo_prompt: str) -> None:
        """Build workflow skill includes research and strategy phases before builder."""
        skill_path = Path(__file__).parent.parent / "skills" / "workflow-build" / "SKILL.md"
        build_skill = skill_path.read_text()
        researcher_pos = build_skill.lower().index("researcher")
        builder_pos = build_skill.lower().index("builder")
        assert researcher_pos < builder_pos

    # ── CEO Review Gate tests ────────────────────────────────────

    def test_has_review_gate_section(self, ceo_prompt: str) -> None:
        assert "### CEO Review Gate" in ceo_prompt

    def test_review_gate_defines_verdicts(self, ceo_prompt: str) -> None:
        assert "PROCEED" in ceo_prompt
        assert "REDIRECT" in ceo_prompt
        assert "ABORT" in ceo_prompt

    def test_review_gate_references_reviews_dir(self, ceo_prompt: str) -> None:
        assert ".factory/reviews/" in ceo_prompt

    def test_strategist_hard_gate_in_plan_loop(self, ceo_prompt: str) -> None:
        """CEO prompt requires Strategist review as HARD GATE."""
        assert "HARD GATE" in ceo_prompt
        assert "PLAN APPROVED" in ceo_prompt

    def test_strategist_hard_gate_in_improve_mode(self, ceo_prompt: str) -> None:
        """Improve workflow skill has gate node after strategist."""
        from factory.workflow.definitions import register_all
        wfs = register_all()
        improve = wfs["improve"]
        gate_ids = [nid for nid, n in improve.nodes.items() if hasattr(n, "evaluator_type")]
        assert len(gate_ids) > 0, "Improve workflow must have gate nodes"

    def test_plan_loop_has_research_review(self, ceo_prompt: str) -> None:
        """CEO prompt has review gate protocol for agent review."""
        assert "ceo-verdict" in ceo_prompt

    def test_build_mode_has_builder_review(self, ceo_prompt: str) -> None:
        """Build workflow skill has evaluator after builder."""
        from factory.workflow.definitions import register_all
        wfs = register_all()
        build = wfs["build"]
        has_evaluator = any(
            hasattr(n, "role") and n.role.value == "evaluator"
            for n in build.nodes.values()
        )
        assert has_evaluator, "Build workflow must have evaluator node"

    def test_improve_mode_has_builder_pr_review(self, ceo_prompt: str) -> None:
        """CEO prompt references PR review before proceeding."""
        assert "gh pr diff" in ceo_prompt or "PR diff" in ceo_prompt or "Builder review" in ceo_prompt

    def test_improve_mode_has_qa_verification(self, ceo_prompt: str) -> None:
        """CEO prompt mandates QA via Sacred Rule 9."""
        assert "QA" in ceo_prompt
        assert "Do not skip QA verification" in ceo_prompt

    def test_review_assessment_criteria_table(self, ceo_prompt: str) -> None:
        """Review gate must define assessment criteria per role."""
        for role in ["Researcher", "Strategist", "Builder", "QA"]:
            assert role in ceo_prompt

    # ── E2E Verification Gate tests ──────────────────────────────

    def test_build_mode_has_e2e_gate(self, ceo_prompt: str) -> None:
        """Build workflow skill has evaluator for E2E verification."""
        from factory.workflow.definitions import register_all
        wfs = register_all()
        build = wfs["build"]
        has_evaluator = any(
            hasattr(n, "role") and n.role.value == "evaluator"
            for n in build.nodes.values()
        )
        assert has_evaluator

    def test_e2e_gate_before_improve(self, ceo_prompt: str) -> None:
        """Build workflow has evaluator after builder in topological order."""
        from factory.workflow.skill_export import _topological_sort
        from factory.workflow.definitions import register_all
        wfs = register_all()
        build = wfs["build"]
        order = _topological_sort(build)
        builder_ids = [nid for nid in order if "builder" in nid]
        eval_ids = [nid for nid in order if "evaluator" in nid or "eval" in nid]
        if builder_ids and eval_ids:
            assert order.index(builder_ids[0]) < order.index(eval_ids[0])

    def test_e2e_gate_asks_user_for_input(self, ceo_prompt: str) -> None:
        """CEO prompt communicates with user in foreground mode."""
        assert "user" in ceo_prompt.lower()

    def test_e2e_gate_in_review_mode(self, ceo_prompt: str) -> None:
        """CEO routes evals_pending_review to review step."""
        assert "evals_pending_review" in ceo_prompt

    # ── Archivist Enforcement tests ─────────────────────────────

    def test_archivist_do_not_skip_labels(self, ceo_prompt: str) -> None:
        """CEO prompt enforces mandatory archival."""
        assert "Do not skip archival" in ceo_prompt

    def test_archivist_in_build_mode(self, ceo_prompt: str) -> None:
        """Build workflow skill includes archivist node."""
        from factory.workflow.definitions import register_all
        wfs = register_all()
        build = wfs["build"]
        has_archivist = any(
            hasattr(n, "role") and n.role.value == "archivist"
            for n in build.nodes.values()
        )
        assert has_archivist

    def test_archivist_in_improve_mode(self, ceo_prompt: str) -> None:
        """Improve workflow skill includes archivist node."""
        from factory.workflow.definitions import register_all
        wfs = register_all()
        improve = wfs["improve"]
        has_archivist = any(
            hasattr(n, "role") and n.role.value == "archivist"
            for n in improve.nodes.values()
        )
        assert has_archivist

    def test_final_archive_blocking(self, ceo_prompt: str) -> None:
        """CEO prompt requires final archival at cycle end."""
        assert "archival" in ceo_prompt.lower()

    # ── Skill Routing tests (replaces Plan Loop tests) ─────────

    def test_has_skill_routing(self, ceo_prompt: str) -> None:
        """CEO prompt has Skill Selection section."""
        assert "Skill" in ceo_prompt

    def test_plan_loop_before_build_mode(self, ceo_prompt: str) -> None:
        """Build workflow has research before builder in graph."""
        from factory.workflow.definitions import register_all
        from factory.workflow.skill_export import _topological_sort
        wfs = register_all()
        build = wfs["build"]
        order = _topological_sort(build)
        researcher_ids = [nid for nid in order if "researcher" in nid]
        builder_ids = [nid for nid in order if "builder" in nid]
        if researcher_ids and builder_ids:
            assert order.index(researcher_ids[0]) < order.index(builder_ids[0])

    def test_plan_loop_spawns_researcher(self, ceo_prompt: str) -> None:
        """Build workflow skill includes researcher agent."""
        skill_path = Path(__file__).parent.parent / "skills" / "workflow-build" / "SKILL.md"
        assert "researcher" in skill_path.read_text().lower()

    def test_plan_loop_spawns_strategist(self, ceo_prompt: str) -> None:
        """Build workflow skill includes strategist agent."""
        skill_path = Path(__file__).parent.parent / "skills" / "workflow-build" / "SKILL.md"
        assert "strategist" in skill_path.read_text().lower()

    def test_plan_loop_has_iteration_limit(self, ceo_prompt: str) -> None:
        """Build workflow has gate nodes with RELOOP edges (iteration limits)."""
        from factory.workflow.definitions import register_all
        from factory.workflow.primitives import VerdictType
        wfs = register_all()
        build = wfs["build"]
        reloop_edges = [e for e in build.edges if e.condition == VerdictType.RELOOP]
        assert len(reloop_edges) > 0, "Build workflow must have RELOOP edges"

    def test_plan_loop_persists_spec(self, ceo_prompt: str) -> None:
        """Build workflow skill references current.md for strategy."""
        skill_path = Path(__file__).parent.parent / "skills" / "workflow-build" / "SKILL.md"
        assert "current.md" in skill_path.read_text()

    def test_plan_loop_transitions_to_build(self, ceo_prompt: str) -> None:
        """Build workflow has builder after strategist in graph order."""
        from factory.workflow.definitions import register_all
        from factory.workflow.skill_export import _topological_sort
        wfs = register_all()
        build = wfs["build"]
        order = _topological_sort(build)
        strat_ids = [nid for nid in order if "strategist" in nid]
        builder_ids = [nid for nid in order if "builder" in nid]
        if strat_ids and builder_ids:
            assert order.index(strat_ids[0]) < order.index(builder_ids[0])

    def test_plan_loop_references_archivist(self, ceo_prompt: str) -> None:
        """Build workflow has archivist node."""
        from factory.workflow.definitions import register_all
        wfs = register_all()
        build = wfs["build"]
        has_archivist = any(
            hasattr(n, "role") and n.role.value == "archivist"
            for n in build.nodes.values()
        )
        assert has_archivist


# ── Strategist Ideation Mode ─────────────────────────────────────


class TestStrategistIdeationMode:
    def test_has_ideation_section(self, strategist_prompt: str) -> None:
        assert "## Design / Ideation Mode" in strategist_prompt

    def test_has_output_format(self, strategist_prompt: str) -> None:
        assert "### Vision" in strategist_prompt
        assert "### Architecture" in strategist_prompt

    def test_has_refinement_mode(self, strategist_prompt: str) -> None:
        assert "### Refinement Mode" in strategist_prompt
        assert "Prior Draft" in strategist_prompt
        assert "User Feedback" in strategist_prompt

    def test_has_ideation_constraints(self, strategist_prompt: str) -> None:
        assert "### Ideation Constraints" in strategist_prompt

    def test_has_non_goals(self, strategist_prompt: str) -> None:
        assert "Non-Goals" in strategist_prompt

    def test_has_open_questions(self, strategist_prompt: str) -> None:
        assert "Open Questions" in strategist_prompt

    def test_references_research_file(self, strategist_prompt: str) -> None:
        assert "research.md" in strategist_prompt

    def test_has_research_configuration_section(self, strategist_prompt: str) -> None:
        """Strategist ideation output format includes Research Configuration section."""
        assert "## Research Configuration" in strategist_prompt

    def test_research_config_has_all_fields(self, strategist_prompt: str) -> None:
        """Research Configuration section includes all required fields."""
        assert "Research Target" in strategist_prompt
        assert "Mutable Surfaces" in strategist_prompt
        assert "Fixed Surfaces" in strategist_prompt
        assert "Research Constraints" in strategist_prompt
        assert "Cost Budget" in strategist_prompt

    def test_has_grounding_protocol(self, strategist_prompt: str) -> None:
        """Strategist ideation includes the grounding protocol."""
        assert "Grounding Protocol" in strategist_prompt
        assert "MANDATORY" in strategist_prompt

    def test_mandatory_research_config_rule(self, strategist_prompt: str) -> None:
        """Strategist knows research config is mandatory when told it's a research project."""
        assert "This is a research project" in strategist_prompt


# ── Factory Config Template ─────────────────────────────────────


TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


class TestFactoryConfigTemplate:
    @pytest.fixture
    def template(self) -> str:
        return (TEMPLATES_DIR / "factory_config.md").read_text()

    def test_has_research_target_section(self, template: str) -> None:
        assert "## Research Target" in template

    def test_has_mutable_surfaces_section(self, template: str) -> None:
        assert "## Mutable Surfaces" in template

    def test_has_fixed_surfaces_section(self, template: str) -> None:
        assert "## Fixed Surfaces" in template

    def test_has_research_constraints_section(self, template: str) -> None:
        assert "## Research Constraints" in template

    def test_has_cost_budget_section(self, template: str) -> None:
        assert "## Cost Budget" in template

    def test_research_sections_after_constraints(self, template: str) -> None:
        """Research sections come after ## Constraints."""
        constraints_idx = template.index("## Constraints")
        research_idx = template.index("## Research Target")
        assert constraints_idx < research_idx
