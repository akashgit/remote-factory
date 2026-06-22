"""Tests for QA Agent delegation patterns in CEO and QA prompts.

Verifies that:
- The QA prompt covers all 3 verification sections
- The CEO prompt delegates eval to the QA Agent (no direct factory eval in experiment pipeline)
- QA follows Builder in Improve, Research, and Refine modes
- Research R5a reads the QA report instead of running eval directly
- Clean PR delegates post-strip verification to the QA Agent
- Event-based flow validation detects Builder→QA sequencing
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

PROMPTS_DIR = Path(__file__).parent.parent / "factory" / "agents" / "prompts"
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
    @staticmethod
    def _extract_experiment_pipeline(ceo_prompt: str) -> str:
        """Extract the experiment execution pipeline (Step 2 through verdict)."""
        start = ceo_prompt.find("### Step 2: Execute (Per Approved Hypothesis)")
        end = ceo_prompt.find("### Step 2i: Persist New Backlog Items")
        assert start != -1, "Step 2 not found in CEO prompt"
        assert end != -1, "Step 2i not found in CEO prompt"
        return ceo_prompt[start:end]

    def test_ceo_prompt_no_direct_eval_in_experiment_pipeline(
        self, ceo_prompt: str
    ) -> None:
        """CEO must not run `factory eval` directly in the experiment pipeline.

        All eval calls in Step 2 must be inside QA Agent task descriptions
        (i.e., inside factory agent qa --task "..." blocks), not as standalone
        CEO commands.
        """
        pipeline = self._extract_experiment_pipeline(ceo_prompt)

        # Find all 'factory eval' occurrences in the pipeline
        for match in re.finditer(r"factory eval", pipeline):
            pos = match.start()
            # Look backwards from this position for the nearest 'factory agent qa'
            # or the nearest code block start to determine context
            preceding = pipeline[:pos]

            # Check if this eval is inside a QA agent task description
            last_qa_task = preceding.rfind('factory agent qa --task')
            last_code_block_end = preceding.rfind('```\n')

            # If the last QA task invocation is more recent than the last
            # code block end, this eval is inside a QA task — that's fine
            if last_qa_task > last_code_block_end:
                continue

            # Otherwise this is a direct CEO eval call — fail
            context = pipeline[max(0, pos - 80):pos + 40]
            pytest.fail(
                f"Direct 'factory eval' found in experiment pipeline outside "
                f"QA Agent task. Context: ...{context}..."
            )

    def test_ceo_prompt_delegates_to_qa_after_builder(
        self, ceo_prompt: str
    ) -> None:
        """QA Agent must follow Builder in Improve, Research, and Refine modes."""
        # Improve mode: Builder (2c) → QA (2c-qa)
        improve_pipeline = self._extract_experiment_pipeline(ceo_prompt)
        builder_pos = improve_pipeline.find("#### 2c. Implement (Builder Agent)")
        qa_pos = improve_pipeline.find(
            "#### 2c-qa: QA Agent Verification (MANDATORY"
        )
        assert builder_pos != -1, "Builder step 2c not found"
        assert qa_pos != -1, "QA step 2c-qa not found"
        assert qa_pos > builder_pos, "QA must come after Builder in Improve mode"

        # Research mode: R3b (Builder) → R3-qa (QA)
        r3b_pos = ceo_prompt.find("#### R3b. Implement")
        r3_qa_pos = ceo_prompt.find("**R3-qa: QA Agent Verification")
        assert r3b_pos != -1, "Research Builder step R3b not found"
        assert r3_qa_pos != -1, "Research QA step R3-qa not found"
        assert r3_qa_pos > r3b_pos, "QA must come after Builder in Research mode"

        # Refine mode: R4 (Builder) → R5 (QA)
        refine_section_start = ceo_prompt.find("## Mode: Refine")
        assert refine_section_start != -1, "Refine mode not found"
        refine_section = ceo_prompt[refine_section_start:]
        r4_pos = refine_section.find("### R4: Implement (Builder Agent)")
        r5_pos = refine_section.find("### R5")
        assert r4_pos != -1, "Refine Builder step R4 not found"
        assert r5_pos != -1, "Refine QA step R5 not found"
        assert r5_pos > r4_pos, "QA must come after Builder in Refine mode"

    def test_research_mode_hygiene_reads_qa_report(
        self, ceo_prompt: str
    ) -> None:
        """Research R5a must reference qa-latest.md, not run eval directly."""
        r5a_start = ceo_prompt.find("#### R5a. Hygiene Gate")
        r5b_start = ceo_prompt.find("#### R5b. Monotonic Improvement")
        assert r5a_start != -1, "R5a not found"
        assert r5b_start != -1, "R5b not found"
        r5a_section = ceo_prompt[r5a_start:r5b_start]

        assert "qa-latest.md" in r5a_section, (
            "R5a must read from .factory/reviews/qa-latest.md"
        )
        # Must not contain a standalone `factory eval` command
        assert "```bash" not in r5a_section or "factory eval" not in r5a_section.split("```bash")[-1].split("```")[0] if "```bash" in r5a_section else True, (
            "R5a must not run factory eval directly — read from QA report"
        )

    def test_clean_pr_delegates_to_qa(self, ceo_prompt: str) -> None:
        """Clean PR section must use QA Agent, not direct factory eval."""
        clean_pr_start = ceo_prompt.find("#### 2i-clean. Clean PR")
        assert clean_pr_start != -1, "Clean PR step not found"
        # Find the end of the clean PR section
        next_section = ceo_prompt.find("**Approve (DO NOT MERGE):**", clean_pr_start)
        clean_pr_section = ceo_prompt[clean_pr_start:next_section]

        assert "factory agent qa" in clean_pr_section, (
            "Clean PR must delegate verification to QA Agent"
        )
        assert "qa-latest.md" in clean_pr_section, (
            "Clean PR must read QA verdict from qa-latest.md"
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
