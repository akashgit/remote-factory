"""Tests for factory.optimization.mutators.agentic — AgenticMutator."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

_INVOKE_AGENT = "factory.agents.runner.invoke_agent"

from factory.optimization.mutators.agentic import AgenticMutator, _parse_json
from factory.optimization.protocols import Mutator
from factory.optimization.surface import Surface
from factory.optimization.types import ExecutionResult, Patch, StepRecord, TaskResult


def _make_execution_result(
    failed: list[dict] | None = None,
    passed: list[dict] | None = None,
) -> ExecutionResult:
    tasks: list[TaskResult] = []
    for f in (failed or []):
        tasks.append(TaskResult(
            task_id=f.get("task_id", "t1"),
            reward=0.0,
            predicted=f.get("predicted", "wrong"),
            gold=f.get("gold", "right"),
            question=f.get("question", "what?"),
        ))
    for p in (passed or []):
        tasks.append(TaskResult(
            task_id=p.get("task_id", "t2"),
            reward=1.0,
            predicted=p.get("predicted", "correct"),
            gold=p.get("gold", "correct"),
        ))
    return ExecutionResult(returncode=0, task_results=tasks)


class TestAgenticMutatorProtocol:
    def test_conforms_to_mutator_protocol(self, tmp_path: Path) -> None:
        m = AgenticMutator(project_path=tmp_path)
        assert isinstance(m, Mutator)


class TestAgenticMutatorNoFailures:
    def test_empty_patch_when_no_failures(self, tmp_path: Path) -> None:
        m = AgenticMutator(project_path=tmp_path)
        result = _make_execution_result(passed=[{"task_id": "t1"}])
        p = m.propose(Surface(), result, [])
        assert p.prompt_edits == []
        assert "no failures" in p.reasoning

    def test_empty_patch_when_no_task_results(self, tmp_path: Path) -> None:
        m = AgenticMutator(project_path=tmp_path)
        result = ExecutionResult(returncode=0)
        p = m.propose(Surface(), result, [])
        assert p.prompt_edits == []


class TestAgenticMutatorPromptConstruction:
    def test_failed_tasks_appear_in_prompt(self, tmp_path: Path) -> None:
        m = AgenticMutator(project_path=tmp_path)
        surface = Surface(prompt_slots={"skill": "You are a search assistant."})
        result = _make_execution_result(failed=[
            {"task_id": "q42", "question": "Who wrote Hamlet?", "predicted": "Dickens", "gold": "Shakespeare"},
        ])
        prompt = m._build_prompt(surface, result.task_results[:1], [])
        assert "q42" in prompt
        assert "Who wrote Hamlet?" in prompt
        assert "Dickens" in prompt
        assert "Shakespeare" in prompt
        assert "You are a search assistant." in prompt

    def test_history_appears_in_prompt(self, tmp_path: Path) -> None:
        m = AgenticMutator(project_path=tmp_path)
        history = [
            StepRecord(step_number=1, score_start=0.3, score_end=0.4, score_delta=0.1, verdict="keep"),
            StepRecord(step_number=2, score_start=0.4, score_end=0.35, score_delta=-0.05, verdict="revert"),
        ]
        prompt = m._build_prompt(Surface(prompt_slots={"x": "val"}), [], history)
        assert "Step 1" in prompt
        assert "Step 2" in prompt


class TestAgenticMutatorInvocation:
    def test_successful_agent_response(self, tmp_path: Path) -> None:
        response = json.dumps({
            "edits": [{"slot": "skill", "old": "old text", "new": "new text"}],
            "reasoning": "improved search instructions",
        })
        mock = AsyncMock(return_value=(response, 0))

        m = AgenticMutator(project_path=tmp_path)
        result = _make_execution_result(failed=[{"task_id": "t1"}])
        surface = Surface(prompt_slots={"skill": "old text"})

        with patch(_INVOKE_AGENT, mock):
            p = m.propose(surface, result, [])

        assert len(p.prompt_edits) == 1
        assert p.prompt_edits[0].slot_name == "skill"
        assert p.prompt_edits[0].old_value == "old text"
        assert p.prompt_edits[0].new_value == "new text"
        assert p.reasoning == "improved search instructions"

    def test_agent_failure_returns_empty_patch(self, tmp_path: Path) -> None:
        mock = AsyncMock(side_effect=RuntimeError("agent crashed"))

        m = AgenticMutator(project_path=tmp_path)
        result = _make_execution_result(failed=[{"task_id": "t1"}])

        with patch(_INVOKE_AGENT, mock):
            p = m.propose(Surface(prompt_slots={"skill": "x"}), result, [])

        assert p.prompt_edits == []
        assert "agent invocation failed" in p.reasoning

    def test_nonzero_exit_returns_empty_patch(self, tmp_path: Path) -> None:
        mock = AsyncMock(return_value=("error output", 1))

        m = AgenticMutator(project_path=tmp_path)
        result = _make_execution_result(failed=[{"task_id": "t1"}])

        with patch(_INVOKE_AGENT, mock):
            p = m.propose(Surface(prompt_slots={"skill": "x"}), result, [])

        assert p.prompt_edits == []
        assert "exit code 1" in p.reasoning


class TestParseJson:
    def test_direct_json(self) -> None:
        data = {"edits": [], "reasoning": "ok"}
        assert _parse_json(json.dumps(data)) == data

    def test_markdown_code_block(self) -> None:
        text = 'Here is my analysis:\n```json\n{"edits": [{"slot": "s", "old": "a", "new": "b"}], "reasoning": "test"}\n```\nDone.'
        result = _parse_json(text)
        assert result is not None
        assert len(result["edits"]) == 1

    def test_embedded_json_object(self) -> None:
        text = 'Some preamble text. {"edits": [], "reasoning": "found it"} and more text.'
        result = _parse_json(text)
        assert result is not None
        assert result["reasoning"] == "found it"

    def test_no_json_returns_none(self) -> None:
        assert _parse_json("no json here at all") is None

    def test_json_without_edits_or_reasoning_ignored(self) -> None:
        text = '{"unrelated": true}'
        assert _parse_json(text) is None


class TestSlotEditGeneration:
    def test_multiple_edits(self, tmp_path: Path) -> None:
        response = json.dumps({
            "edits": [
                {"slot": "skill", "old": "a", "new": "b"},
                {"slot": "system", "old": "c", "new": "d"},
            ],
            "reasoning": "multi-edit",
        })
        mock = AsyncMock(return_value=(response, 0))

        m = AgenticMutator(project_path=tmp_path)
        result = _make_execution_result(failed=[{"task_id": "t1"}])
        surface = Surface(prompt_slots={"skill": "a", "system": "c"})

        with patch(_INVOKE_AGENT, mock):
            p = m.propose(surface, result, [])

        assert len(p.prompt_edits) == 2
        assert p.prompt_edits[0].slot_name == "skill"
        assert p.prompt_edits[1].slot_name == "system"

    def test_malformed_edit_entry_skipped(self, tmp_path: Path) -> None:
        response = json.dumps({
            "edits": [
                {"slot": "skill", "old": "a", "new": "b"},
                "not a dict",
                {"no_slot_key": True},
            ],
            "reasoning": "partial",
        })
        mock = AsyncMock(return_value=(response, 0))

        m = AgenticMutator(project_path=tmp_path)
        result = _make_execution_result(failed=[{"task_id": "t1"}])
        surface = Surface(prompt_slots={"skill": "a"})

        with patch(_INVOKE_AGENT, mock):
            p = m.propose(surface, result, [])

        assert len(p.prompt_edits) == 1
