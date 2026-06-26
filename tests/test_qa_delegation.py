"""Tests for QA Agent delegation patterns in CEO and QA prompts.

Verifies that:
- The QA prompt covers all 3 verification sections
- The CEO prompt references skill-based routing (mode sections moved to SKILL.md)
- Generated workflow skills do not reference nonexistent agent roles
- Builder precedes Evaluator in generated workflow skills (graph ordering)
- Event-based flow validation detects Builder→QA sequencing
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

PROMPTS_DIR = Path(__file__).parent.parent / "factory" / "agents" / "prompts"
SKILLS_DIR = Path(__file__).parent.parent / "skills"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def qa_prompt() -> str:
    return (PROMPTS_DIR / "qa.md").read_text()


@pytest.fixture
def ceo_prompt() -> str:
    return (PROMPTS_DIR / "ceo.md").read_text()


# ── QA Prompt Structure ──────────────────────────────────────────


class TestQAPromptStructure:
    def test_qa_agent_prompt_covers_all_sections(self, qa_prompt: str) -> None:
        """QA prompt must define all 3 verification sections."""
        assert "### Section 1: Health Check" in qa_prompt
        assert "### Section 2: Code Review" in qa_prompt
        assert "### Section 3: Adversarial QA" in qa_prompt


# ── CEO Delegation Patterns ──────────────────────────────────────


class TestCEODelegation:
    def test_ceo_prompt_no_direct_eval_in_experiment_pipeline(
        self, ceo_prompt: str
    ) -> None:
        """CEO prompt must not contain standalone `factory eval` calls.

        The CEO delegates all eval to QA Agent. Mode-specific pipelines
        now live in SKILL.md files, but the core CEO prompt should not
        contain any direct eval invocations.
        """
        for match in re.finditer(r"`?factory eval`?", ceo_prompt):
            hit = match.group()
            if hit.startswith("`") and hit.endswith("`"):
                continue
            pos = match.start()
            preceding = ceo_prompt[:pos]
            last_qa_task = preceding.rfind('factory agent qa --task')
            last_code_block_end = preceding.rfind('```\n')
            if last_qa_task > last_code_block_end:
                continue
            context = ceo_prompt[max(0, pos - 80):pos + 40]
            pytest.fail(
                f"Direct 'factory eval' found in CEO prompt outside "
                f"QA Agent task. Context: ...{context}..."
            )

    def test_ceo_prompt_delegates_to_qa_after_builder(
        self, ceo_prompt: str
    ) -> None:
        """CEO prompt must reference Sacred Rule 9 (QA verification mandatory)."""
        assert "Do not skip QA verification" in ceo_prompt, (
            "CEO prompt must include Sacred Rule 9 requiring QA after Builder"
        )

    def test_ceo_prompt_references_skill_routing(
        self, ceo_prompt: str
    ) -> None:
        """CEO prompt must reference skill-based routing for modes."""
        assert "skills/workflow-" in ceo_prompt, (
            "CEO prompt must reference workflow skill files for mode routing"
        )
        assert "SKILL.md" in ceo_prompt, (
            "CEO prompt must reference SKILL.md files"
        )

    def test_workflow_skills_use_valid_agent_roles(self) -> None:
        """Workflow skills must not reference nonexistent agent roles."""
        invalid_roles = ['factory agent evaluator', 'factory agent reviewer']
        for skill_dir in SKILLS_DIR.glob('workflow-*'):
            skill_path = skill_dir / 'SKILL.md'
            if not skill_path.exists():
                continue
            content = skill_path.read_text()
            for role in invalid_roles:
                assert role not in content, (
                    f'Invalid agent role in {skill_path.name}: {role}'
                )


# ── Event-Based Flow Validation ──────────────────────────────────


def _check_builder_qa_sequence(events: list[dict]) -> bool:
    """Return True if every builder.completed is followed by a qa agent start."""
    for i, event in enumerate(events):
        if event.get("type") == "agent.completed" and event.get("role") == "builder":
            remaining = events[i + 1:]
            found_qa = any(
                e.get("type") == "agent.started" and e.get("role") == "qa"
                for e in remaining
            )
            if not found_qa:
                return False
    return True


class TestEventsFlowValidation:
    def test_events_jsonl_qa_after_builder(self) -> None:
        """Helper detects correct Builder→QA sequencing in events."""
        events = [
            {"type": "agent.started", "role": "builder"},
            {"type": "agent.completed", "role": "builder"},
            {"type": "agent.started", "role": "qa"},
            {"type": "agent.completed", "role": "qa"},
        ]
        assert _check_builder_qa_sequence(events) is True

    def test_events_jsonl_detects_missing_qa(self) -> None:
        """Helper detects missing QA after Builder in events."""
        events = [
            {"type": "agent.started", "role": "builder"},
            {"type": "agent.completed", "role": "builder"},
            {"type": "agent.started", "role": "archivist"},
            {"type": "agent.completed", "role": "archivist"},
        ]
        assert _check_builder_qa_sequence(events) is False


# ── Test Fixture Validation ──────────────────────────────────────


class TestHelloCliFixture:
    def test_hello_cli_fixture_is_valid(self) -> None:
        """Fixture has required files and pytest passes."""
        fixture_dir = FIXTURES_DIR / "hello-cli"
        assert (fixture_dir / "main.py").is_file()
        assert (fixture_dir / "test_main.py").is_file()
        assert (fixture_dir / "factory.md").is_file()

        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(fixture_dir / "test_main.py"), "-q"],
            capture_output=True,
            text=True,
            cwd=str(fixture_dir),
        )
        assert result.returncode == 0, f"Fixture tests failed: {result.stdout}\n{result.stderr}"
