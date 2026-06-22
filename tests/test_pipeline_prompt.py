"""Tests for the pipeline skill structure."""

from pathlib import Path

import pytest

from factory.agents.plugin import load_agent_config

_SKILLS_DIR = Path(__file__).parent.parent / "skills"


@pytest.fixture
def pipeline_skill() -> str:
    return (_SKILLS_DIR / "pipeline" / "SKILL.md").read_text()


@pytest.fixture
def subagents_skill() -> str:
    return (_SKILLS_DIR / "pipeline-subagents" / "SKILL.md").read_text()


class TestPipelineSkillStructure:
    def test_exists(self):
        assert (_SKILLS_DIR / "pipeline" / "SKILL.md").exists()

    def test_has_frontmatter(self, pipeline_skill):
        assert pipeline_skill.startswith("---\n")
        assert "name: pipeline" in pipeline_skill
        assert "disable-model-invocation: true" in pipeline_skill

    def test_has_design_phase(self, pipeline_skill):
        assert "Phase 1" in pipeline_skill
        assert "Design" in pipeline_skill

    def test_has_execution_phase(self, pipeline_skill):
        assert "Phase 2" in pipeline_skill
        assert "Execute" in pipeline_skill

    def test_has_pipeline_table_format(self, pipeline_skill):
        assert "| Step |" in pipeline_skill
        assert "Depends On" in pipeline_skill

    def test_has_gate_decision_protocol(self, pipeline_skill):
        assert "PROCEED" in pipeline_skill
        assert "REDIRECT" in pipeline_skill
        assert "ABORT" in pipeline_skill

    def test_has_parallel_execution(self, pipeline_skill):
        assert "parallel" in pipeline_skill.lower()

    def test_references_factory_agent_command(self, pipeline_skill):
        assert "factory agent" in pipeline_skill

    def test_references_roles_from_config(self, pipeline_skill):
        config = load_agent_config()
        core_roles = {"researcher", "strategist", "builder", "qa", "archivist"}
        for role in core_roles:
            assert role in config, f"{role} missing from agents.yml"
            assert role in pipeline_skill, f"{role} missing from pipeline skill"

    def test_has_archivist_requirement(self, pipeline_skill):
        prompt_lower = pipeline_skill.lower()
        assert "archivist" in prompt_lower
        assert "mandatory" in prompt_lower or "always include" in prompt_lower

    def test_creates_pipeline_directory(self, pipeline_skill):
        assert "mkdir -p .factory/pipeline" in pipeline_skill

    def test_has_error_recovery(self, pipeline_skill):
        assert "Error" in pipeline_skill or "error" in pipeline_skill

    def test_has_summary_output(self, pipeline_skill):
        assert "summary" in pipeline_skill.lower()

    def test_clarifies_parallel_vs_background(self, pipeline_skill):
        assert "not shell backgrounding" in pipeline_skill.lower() or \
               "not shell background" in pipeline_skill.lower()

    def test_step_table_has_structural_format(self, pipeline_skill):
        import re
        assert re.search(r"\|\s*S\d+\s*\|", pipeline_skill), \
            "Pipeline skill should contain step table rows like '| S1 |'"


class TestPipelineSubagentsSkillStructure:
    def test_exists(self):
        assert (_SKILLS_DIR / "pipeline-subagents" / "SKILL.md").exists()

    def test_has_frontmatter(self, subagents_skill):
        assert subagents_skill.startswith("---\n")
        assert "name: pipeline-subagents" in subagents_skill
        assert "disable-model-invocation: true" in subagents_skill

    def test_uses_agent_tool(self, subagents_skill):
        assert "Agent tool" in subagents_skill or "Agent(" in subagents_skill

    def test_references_roles_matching_config(self, subagents_skill):
        config = load_agent_config()
        core_roles = {"researcher", "strategist", "builder", "qa", "archivist"}
        for role in core_roles:
            assert role in config, f"{role} missing from agents.yml"
            assert role in subagents_skill, f"{role} missing from subagents skill"

    def test_subagent_types_use_plugin_namespace(self, subagents_skill):
        core_roles = {"researcher", "strategist", "builder", "qa", "archivist"}
        for role in core_roles:
            assert f"factory:{role}" in subagents_skill, \
                f"subagent type 'factory:{role}' not referenced in skill"

    def test_no_bare_or_dash_prefixed_subagent_types(self, subagents_skill):
        assert "factory-researcher" not in subagents_skill
        assert "factory-builder" not in subagents_skill

    def test_has_parallel_execution(self, subagents_skill):
        assert "parallel" in subagents_skill.lower()

    def test_has_background_execution(self, subagents_skill):
        assert "run_in_background" in subagents_skill

    def test_has_gate_protocol(self, subagents_skill):
        assert "PROCEED" in subagents_skill
        assert "REDIRECT" in subagents_skill
        assert "ABORT" in subagents_skill

    def test_has_design_phase(self, subagents_skill):
        assert "Phase 1" in subagents_skill

    def test_has_execution_phase(self, subagents_skill):
        assert "Phase 2" in subagents_skill

    def test_creates_pipeline_directory(self, subagents_skill):
        assert "mkdir -p .factory/pipeline" in subagents_skill

    def test_step_table_has_structural_format(self, subagents_skill):
        import re
        assert re.search(r"\|\s*S\d+\s*\|", subagents_skill), \
            "Subagents skill should contain step table rows like '| S1 |'"

    def test_mentions_failure_analyst(self, subagents_skill):
        assert "failure_analyst" in subagents_skill
