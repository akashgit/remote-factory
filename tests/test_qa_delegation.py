"""Tests for QA Agent delegation — verify the CEO delegates eval to QA, not directly."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PROMPTS_DIR = Path(__file__).parent.parent / "factory" / "agents" / "prompts"


@pytest.fixture
def ceo_prompt() -> str:
    return (PROMPTS_DIR / "ceo.md").read_text()


@pytest.fixture
def qa_prompt() -> str:
    return (PROMPTS_DIR / "qa.md").read_text()


class TestQADelegation:
    def test_qa_agent_prompt_covers_health_check(self, qa_prompt: str) -> None:
        """QA prompt must have all 3 verification sections."""
        assert "### Section 1: Health Check" in qa_prompt
        assert "### Section 2: Code Review" in qa_prompt
        assert "### Section 3: Adversarial QA" in qa_prompt

    def test_ceo_prompt_no_direct_eval_in_experiment_pipeline(
        self, ceo_prompt: str,
    ) -> None:
        """In Step 2 (experiment execution), factory eval must only appear
        inside QA Agent task descriptions, not as a direct CEO command."""
        step2_match = re.search(
            r"### Step 2: Execute.*?(?=### Step 3:)", ceo_prompt, re.DOTALL,
        )
        assert step2_match, "Step 2 section not found in CEO prompt"
        step2 = step2_match.group()

        assert "#### 2a. Baseline Eval" not in step2, (
            "Step 2a (Baseline Eval) should be removed — QA Agent owns baseline measurement"
        )

        qa_task_blocks: list[str] = []
        non_qa_text = step2
        for block in re.finditer(
            r'factory agent qa --task ".*?"', step2, re.DOTALL,
        ):
            qa_task_blocks.append(block.group())
            non_qa_text = non_qa_text.replace(block.group(), "")

        assert qa_task_blocks, "No QA Agent task blocks found in Step 2"

        direct_eval_pattern = re.compile(r"```bash\s*\n\s*factory eval\b")
        direct_evals = direct_eval_pattern.findall(non_qa_text)
        allowed_direct = [m for m in direct_evals if "clean" in non_qa_text[
            max(0, non_qa_text.find(m) - 200):non_qa_text.find(m)
        ].lower()]

        non_clean_direct = len(direct_evals) - len(allowed_direct)
        assert non_clean_direct <= 1, (
            f"Found {non_clean_direct} direct 'factory eval' call(s) in Step 2 "
            "outside QA Agent tasks and Clean PR mode"
        )

    def test_ceo_prompt_delegates_to_qa_after_builder(
        self, ceo_prompt: str,
    ) -> None:
        """QA Agent invocation must follow every Builder invocation
        before a verdict is made."""
        step2_match = re.search(
            r"### Step 2: Execute.*?(?=### Step 3:)", ceo_prompt, re.DOTALL,
        )
        assert step2_match, "Step 2 section not found"
        step2 = step2_match.group()

        builder_pos = step2.find("factory agent builder")
        qa_pos = step2.find("factory agent qa")

        assert builder_pos != -1, "Builder invocation not found in Step 2"
        assert qa_pos != -1, "QA Agent invocation not found in Step 2"
        assert qa_pos > builder_pos, (
            "QA Agent must be invoked AFTER Builder in the experiment pipeline"
        )

        assert "QA Agent Verification (MANDATORY" in step2

    def test_research_mode_hygiene_gate_reads_qa_report(
        self, ceo_prompt: str,
    ) -> None:
        """Research mode R5a must read qa-latest.md, not call factory eval directly."""
        r5a_match = re.search(
            r"#### R5a\. Hygiene Gate.*?(?=#### R5b\.)",
            ceo_prompt,
            re.DOTALL,
        )
        assert r5a_match, "R5a section not found in CEO prompt"
        r5a = r5a_match.group()

        assert "qa-latest.md" in r5a, (
            "R5a must read the QA Agent's report from qa-latest.md"
        )
        assert "```bash" not in r5a or "factory eval" not in r5a, (
            "R5a must NOT call factory eval directly — read QA Agent report instead"
        )

    def test_events_jsonl_qa_after_builder(self, tmp_path: Path) -> None:
        """Given a mock events.jsonl, verify a helper can detect
        that QA ran after Builder."""
        events = [
            {"type": "agent.started", "agent": "builder", "ts": 1},
            {"type": "agent.completed", "agent": "builder", "ts": 2},
            {"type": "agent.started", "agent": "qa", "ts": 3},
            {"type": "agent.completed", "agent": "qa", "ts": 4},
        ]
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
        )

        lines = events_file.read_text().strip().splitlines()
        parsed = [json.loads(line) for line in lines]

        builder_completed_ts = None
        qa_started_ts = None
        for event in parsed:
            if event["type"] == "agent.completed" and event["agent"] == "builder":
                builder_completed_ts = event["ts"]
            if event["type"] == "agent.started" and event["agent"] == "qa":
                if builder_completed_ts is not None:
                    qa_started_ts = event["ts"]

        assert builder_completed_ts is not None, "No builder.completed event found"
        assert qa_started_ts is not None, "No qa.started event found after builder.completed"
        assert qa_started_ts > builder_completed_ts, (
            "QA must start after Builder completes"
        )
