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
    def test_has_aggressive_documentation(self, archivist_prompt: str) -> None:
        assert "## Aggressive Documentation Protocol" in archivist_prompt

    def test_has_preflight_checklist(self, archivist_prompt: str) -> None:
        assert "Pre-flight Checklist" in archivist_prompt

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
        assert "## Mode: Build" in ceo_prompt
        assert "## Mode: Discover" in ceo_prompt
        assert "## Mode: Review" in ceo_prompt
        assert "## Mode: Improve" in ceo_prompt
        assert "## Mode: Meta" in ceo_prompt

    def test_has_sacred_rules(self, ceo_prompt: str) -> None:
        assert "## Sacred Rules" in ceo_prompt

    def test_has_mandatory_archival(self, ceo_prompt: str) -> None:
        assert "## Mandatory Archival Checkpoints" in ceo_prompt
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
        for role in ["Researcher", "Strategist", "Builder", "Reviewer", "Evaluator", "Archivist"]:
            assert role in ceo_prompt

    def test_seventh_sacred_rule_archival(self, ceo_prompt: str) -> None:
        assert "Do not skip archival checkpoints" in ceo_prompt

    def test_ceo_notes_convention(self, ceo_prompt: str) -> None:
        assert "ceo:keep" in ceo_prompt
        assert "ceo:revert" in ceo_prompt
        assert "archivist_spawned" in ceo_prompt

    def test_build_mode_has_full_pipeline(self, ceo_prompt: str) -> None:
        """Build mode must use Researcher + Strategist + Archivist before Builder."""
        # Find the Build mode section
        build_start = ceo_prompt.index("## Mode: Build")
        discover_start = ceo_prompt.index("## Mode: Discover")
        build_section = ceo_prompt[build_start:discover_start]

        # All agents must be present
        assert "factory agent researcher" in build_section
        assert "factory agent strategist" in build_section
        assert "factory agent archivist" in build_section
        assert "factory agent builder" in build_section

        # Research step must come before Build step
        assert build_section.index("factory agent researcher") < build_section.index("factory agent builder")
        # Strategy step must come before Build step
        assert build_section.index("factory agent strategist") < build_section.index("factory agent builder")

    def test_build_mode_does_not_skip_to_builder(self, ceo_prompt: str) -> None:
        """Build mode must NOT just say 'delegate to the Builder'."""
        build_start = ceo_prompt.index("## Mode: Build")
        discover_start = ceo_prompt.index("## Mode: Discover")
        build_section = ceo_prompt[build_start:discover_start]

        # Should have research and strategy steps
        assert "Research" in build_section
        assert "Strategy" in build_section
        assert "factory agent researcher" in build_section
        assert "factory agent strategist" in build_section

    # ── CEO Review Gate tests ────────────────────────────────────

    def test_has_review_gate_section(self, ceo_prompt: str) -> None:
        assert "### CEO Review Gate" in ceo_prompt

    def test_review_gate_defines_verdicts(self, ceo_prompt: str) -> None:
        assert "PROCEED" in ceo_prompt
        assert "REDIRECT" in ceo_prompt
        assert "ABORT" in ceo_prompt

    def test_review_gate_references_reviews_dir(self, ceo_prompt: str) -> None:
        assert ".factory/reviews/" in ceo_prompt

    def test_strategist_hard_gate_in_build_mode(self, ceo_prompt: str) -> None:
        """Build mode must have a hard gate after Strategist before Builder."""
        build_start = ceo_prompt.index("## Mode: Build")
        discover_start = ceo_prompt.index("## Mode: Discover")
        build_section = ceo_prompt[build_start:discover_start]

        assert "HARD GATE" in build_section
        assert "PLAN APPROVED" in build_section
        # Hard gate must come after strategist and before builder
        assert build_section.index("HARD GATE") < build_section.index("factory agent builder")
        assert build_section.index("factory agent strategist") < build_section.index("HARD GATE")

    def test_strategist_hard_gate_in_improve_mode(self, ceo_prompt: str) -> None:
        """Improve mode must have a hard gate after Strategist."""
        improve_start = ceo_prompt.index("## Mode: Improve")
        improve_section = ceo_prompt[improve_start:]

        assert "HARD GATE" in improve_section
        assert "PLAN APPROVED" in improve_section

    def test_build_mode_has_research_review(self, ceo_prompt: str) -> None:
        """Build mode must have CEO review after Researcher."""
        build_start = ceo_prompt.index("## Mode: Build")
        discover_start = ceo_prompt.index("## Mode: Discover")
        build_section = ceo_prompt[build_start:discover_start]

        assert "ceo-verdict-researcher" in build_section

    def test_build_mode_has_builder_review(self, ceo_prompt: str) -> None:
        """Build mode must have CEO review after Builder."""
        build_start = ceo_prompt.index("## Mode: Build")
        discover_start = ceo_prompt.index("## Mode: Discover")
        build_section = ceo_prompt[build_start:discover_start]

        assert "ceo-verdict-builder" in build_section

    def test_improve_mode_has_builder_pr_review(self, ceo_prompt: str) -> None:
        """Improve mode must have CEO reading PR diff before Reviewer."""
        improve_start = ceo_prompt.index("## Mode: Improve")
        improve_section = ceo_prompt[improve_start:]

        # CEO must read PR diff
        assert "gh pr diff" in improve_section
        assert "ceo-verdict-builder" in improve_section

    def test_improve_mode_has_reviewer_review(self, ceo_prompt: str) -> None:
        """CEO must validate the Reviewer's verdict, not blindly trust it."""
        improve_start = ceo_prompt.index("## Mode: Improve")
        improve_section = ceo_prompt[improve_start:]

        assert "ceo-verdict-reviewer" in improve_section
        assert "rubber-stamp" in improve_section.lower() or "rubber-stamped" in improve_section.lower()

    def test_review_assessment_criteria_table(self, ceo_prompt: str) -> None:
        """Review gate must define assessment criteria per role."""
        # Should have a table with criteria for each role
        for role in ["Researcher", "Strategist", "Builder", "Reviewer", "Evaluator"]:
            assert role in ceo_prompt

    # ── E2E Verification Gate tests ──────────────────────────────

    def test_build_mode_has_e2e_gate(self, ceo_prompt: str) -> None:
        """Build mode must have E2E verification before leaving."""
        build_start = ceo_prompt.index("## Mode: Build")
        discover_start = ceo_prompt.index("## Mode: Discover")
        build_section = ceo_prompt[build_start:discover_start]

        assert "E2E Verification" in build_section
        assert "ceo-verdict-e2e" in build_section

    def test_e2e_gate_before_improve(self, ceo_prompt: str) -> None:
        """E2E verification must come before Improve mode."""
        # The e2e gate in Build mode must come before re-detect
        build_start = ceo_prompt.index("## Mode: Build")
        discover_start = ceo_prompt.index("## Mode: Discover")
        build_section = ceo_prompt[build_start:discover_start]

        assert build_section.index("E2E Verification") < build_section.index("Re-detect state")

    def test_e2e_gate_asks_user_for_input(self, ceo_prompt: str) -> None:
        """E2E gate must ask user for missing env vars, not guess."""
        assert "ASK THE USER" in ceo_prompt

    def test_e2e_gate_in_review_mode(self, ceo_prompt: str) -> None:
        """Review mode must also reference E2E verification before Improve."""
        review_start = ceo_prompt.index("## Mode: Review")
        improve_start = ceo_prompt.index("## Mode: Improve")
        review_section = ceo_prompt[review_start:improve_start]

        assert "E2E Verification" in review_section

    # ── Archivist Enforcement tests ─────────────────────────────

    def test_archivist_checkpoint_file(self, ceo_prompt: str) -> None:
        """CEO must write archivist checkpoints to a tracking file."""
        assert "archivist-checkpoints.md" in ceo_prompt

    def test_archivist_all_blocking(self, ceo_prompt: str) -> None:
        """All archival checkpoints must be blocking (no async)."""
        # The checkpoints table should say YES for all rows
        assert "ALL archival is blocking" in ceo_prompt

    def test_archivist_do_not_skip_labels(self, ceo_prompt: str) -> None:
        """Every archivist call must have DO NOT SKIP label."""
        assert ceo_prompt.count("DO NOT SKIP") >= 5  # research, strategy, build, experiment, build-improve

    def test_archivist_in_build_mode(self, ceo_prompt: str) -> None:
        """Build mode must have archivist after research, strategy, and build."""
        build_start = ceo_prompt.index("## Mode: Build")
        discover_start = ceo_prompt.index("## Mode: Discover")
        build_section = ceo_prompt[build_start:discover_start]

        assert "archivist after research" in build_section
        assert "archivist after strategy" in build_section
        assert "archivist after build" in build_section

    def test_archivist_in_improve_mode(self, ceo_prompt: str) -> None:
        """Improve mode must have archivist after research, strategy, build, and experiment."""
        improve_start = ceo_prompt.index("## Mode: Improve")
        meta_start = ceo_prompt.index("## Mode: Meta")
        improve_section = ceo_prompt[improve_start:meta_start]

        assert "archivist after research" in improve_section
        assert "archivist after strategy" in improve_section
        assert "archivist after build" in improve_section
        assert "archivist after experiment" in improve_section

    def test_final_archive_preflight_check(self, ceo_prompt: str) -> None:
        """Final archive must verify all checkpoints before proceeding."""
        assert "Pre-flight check" in ceo_prompt
        assert "FINAL archivist" in ceo_prompt

    def test_no_async_archivist(self, ceo_prompt: str) -> None:
        """Archivist commands must NOT use & (async) — all blocking."""
        # Find all archivist task lines and make sure none end with &
        import re
        # Match archivist commands that end with & before the closing ```
        async_calls = re.findall(
            r'factory agent archivist --task.*?" --project "\$PROJECT_PATH" &',
            ceo_prompt,
        )
        assert len(async_calls) == 0, f"Found {len(async_calls)} async archivist calls — all must be blocking"

    # ── Phase 0: Ideation tests ────────────────────────────────

    def test_has_phase_0_ideation(self, ceo_prompt: str) -> None:
        assert "## Phase 0: Ideation" in ceo_prompt

    def test_phase_0_before_build_mode(self, ceo_prompt: str) -> None:
        """Phase 0 must appear before Build mode in the prompt."""
        phase0_idx = ceo_prompt.index("## Phase 0: Ideation")
        build_idx = ceo_prompt.index("## Mode: Build")
        assert phase0_idx < build_idx

    def test_phase_0_spawns_researcher(self, ceo_prompt: str) -> None:
        phase0_start = ceo_prompt.index("## Phase 0: Ideation")
        build_start = ceo_prompt.index("## Mode: Build")
        phase0_section = ceo_prompt[phase0_start:build_start]
        assert "factory agent researcher" in phase0_section

    def test_phase_0_spawns_strategist(self, ceo_prompt: str) -> None:
        phase0_start = ceo_prompt.index("## Phase 0: Ideation")
        build_start = ceo_prompt.index("## Mode: Build")
        phase0_section = ceo_prompt[phase0_start:build_start]
        assert "factory agent strategist" in phase0_section

    def test_phase_0_has_iteration_limit(self, ceo_prompt: str) -> None:
        assert "Maximum 5 iterations" in ceo_prompt

    def test_phase_0_persists_spec(self, ceo_prompt: str) -> None:
        phase0_start = ceo_prompt.index("## Phase 0: Ideation")
        build_start = ceo_prompt.index("## Mode: Build")
        phase0_section = ceo_prompt[phase0_start:build_start]
        assert "current.md" in phase0_section

    def test_phase_0_transitions_to_build(self, ceo_prompt: str) -> None:
        phase0_start = ceo_prompt.index("## Phase 0: Ideation")
        build_start = ceo_prompt.index("## Mode: Build")
        phase0_section = ceo_prompt[phase0_start:build_start]
        assert "Build mode" in phase0_section

    def test_phase_0_spawns_archivist(self, ceo_prompt: str) -> None:
        phase0_start = ceo_prompt.index("## Phase 0: Ideation")
        build_start = ceo_prompt.index("## Mode: Build")
        phase0_section = ceo_prompt[phase0_start:build_start]
        assert "factory agent archivist" in phase0_section


# ── Strategist Ideation Mode ─────────────────────────────────────


class TestStrategistIdeationMode:
    def test_has_ideation_section(self, strategist_prompt: str) -> None:
        assert "## Interactive / Ideation Mode" in strategist_prompt

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
