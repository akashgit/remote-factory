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

    def test_uses_path_not_name_for_nested(self, archivist_prompt: str) -> None:
        # All nested paths should use path= not name=
        assert 'path="10-Projects' in archivist_prompt
        assert 'name="10-Projects' not in archivist_prompt


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
