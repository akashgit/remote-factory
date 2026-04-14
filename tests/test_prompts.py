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
