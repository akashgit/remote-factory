"""Unit tests for factory/skillopt/ — pure-logic modules only."""
from __future__ import annotations

import json
import subprocess
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from factory.skillopt.adapter import EnvAdapter
from factory.skillopt.clip import rank_and_select
from factory.skillopt.gate import evaluate_gate, select_gate_score
from factory.skillopt.skill import apply_edit, apply_patch
from factory.skillopt.trainer import SkillOptTrainer
from factory.skillopt.types import (
    Edit,
    GateResult,
    Patch,
    RawPatch,
    RolloutResult,
)


# ---------------------------------------------------------------------------
# types.py — Model validation
# ---------------------------------------------------------------------------


class TestEdit:
    def test_append(self) -> None:
        e = Edit(op="append", content="new line")
        assert e.op == "append"
        assert e.content == "new line"

    def test_insert_after(self) -> None:
        e = Edit(op="insert_after", target="## Rules", content="- rule 1")
        assert e.op == "insert_after"
        assert e.target == "## Rules"

    def test_replace(self) -> None:
        e = Edit(op="replace", target="old text", content="new text")
        assert e.op == "replace"

    def test_delete(self) -> None:
        e = Edit(op="delete", target="remove me")
        assert e.op == "delete"

    def test_content_default_empty_string(self) -> None:
        e = Edit(op="delete", target="x")
        assert e.content == ""

    def test_rejects_invalid_op(self) -> None:
        with pytest.raises(ValidationError):
            Edit(op="invalid_op")  # type: ignore[arg-type]

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            Edit(op="append", content="x", unknown_field="bad")  # type: ignore[call-arg]


class TestPatch:
    def test_construction(self) -> None:
        edits = [Edit(op="append", content="a"), Edit(op="delete", target="b")]
        p = Patch(edits=edits, reasoning="test patch")
        assert len(p.edits) == 2
        assert p.reasoning == "test patch"

    def test_empty_edits(self) -> None:
        p = Patch(edits=[])
        assert p.edits == []
        assert p.reasoning == ""


class TestRolloutResult:
    def test_hard_soft_scores(self) -> None:
        r = RolloutResult(id="task-1", hard=0.8, soft=0.6)
        assert r.hard == 0.8
        assert r.soft == 0.6
        assert r.n_turns == 0
        assert r.fail_reason == ""

    def test_with_extras(self) -> None:
        r = RolloutResult(id="task-2", hard=1.0, soft=0.9, extras={"key": "val"})
        assert r.extras == {"key": "val"}


class TestRawPatch:
    def test_failure_source(self) -> None:
        p = RawPatch(patch=Patch(edits=[]), source_type="failure")
        assert p.source_type == "failure"

    def test_success_source(self) -> None:
        p = RawPatch(patch=Patch(edits=[]), source_type="success")
        assert p.source_type == "success"

    def test_default_source_type(self) -> None:
        p = RawPatch(patch=Patch(edits=[]))
        assert p.source_type == "failure"


class TestGateResult:
    def test_accept_new_best(self) -> None:
        g = GateResult(
            action="accept_new_best",
            current_skill="skill v2",
            current_score=0.9,
            best_skill="skill v2",
            best_score=0.9,
            best_step=5,
        )
        assert g.action == "accept_new_best"

    def test_accept(self) -> None:
        g = GateResult(
            action="accept",
            current_skill="skill v2",
            current_score=0.7,
            best_skill="skill v1",
            best_score=0.8,
            best_step=3,
        )
        assert g.action == "accept"

    def test_reject(self) -> None:
        g = GateResult(
            action="reject",
            current_skill="skill v1",
            current_score=0.6,
            best_skill="skill v1",
            best_score=0.6,
            best_step=1,
        )
        assert g.action == "reject"


# ---------------------------------------------------------------------------
# skill.py — Edit application
# ---------------------------------------------------------------------------


class TestApplyEdit:
    def test_append_adds_to_end(self) -> None:
        result = apply_edit("line1", Edit(op="append", content="line2"))
        assert result == "line1\nline2"

    def test_append_before_protected_region(self) -> None:
        skill = "header\n<!-- SLOW_UPDATE_START -->\nprotected\n<!-- SLOW_UPDATE_END -->"
        result = apply_edit(skill, Edit(op="append", content="new stuff"))
        assert "new stuff" in result
        assert result.index("new stuff") < result.index("<!-- SLOW_UPDATE_START -->")

    def test_append_before_appendix_region(self) -> None:
        skill = "header\n<!-- APPENDIX_START -->\nappendix\n<!-- APPENDIX_END -->"
        result = apply_edit(skill, Edit(op="append", content="inserted"))
        assert result.index("inserted") < result.index("<!-- APPENDIX_START -->")

    def test_insert_after(self) -> None:
        skill = "## Rules\n- old rule"
        result = apply_edit(
            skill, Edit(op="insert_after", target="## Rules", content="- new rule")
        )
        assert "## Rules\n- new rule" in result

    def test_insert_after_target_not_found(self) -> None:
        skill = "some content"
        result = apply_edit(
            skill, Edit(op="insert_after", target="nonexistent", content="x")
        )
        assert result == skill

    def test_replace(self) -> None:
        skill = "old text here"
        result = apply_edit(
            skill, Edit(op="replace", target="old text", content="new text")
        )
        assert result == "new text here"

    def test_replace_target_not_found(self) -> None:
        skill = "some content"
        result = apply_edit(
            skill, Edit(op="replace", target="nonexistent", content="x")
        )
        assert result == skill

    def test_replace_empty_target(self) -> None:
        skill = "some content"
        result = apply_edit(skill, Edit(op="replace", target="", content="x"))
        assert result == skill

    def test_delete(self) -> None:
        skill = "keep this\nremove this\nkeep that"
        result = apply_edit(skill, Edit(op="delete", target="remove this\n"))
        assert result == "keep this\nkeep that"

    def test_delete_target_not_found(self) -> None:
        skill = "some content"
        result = apply_edit(skill, Edit(op="delete", target="nonexistent"))
        assert result == skill

    def test_delete_empty_target(self) -> None:
        skill = "some content"
        result = apply_edit(skill, Edit(op="delete", target=""))
        assert result == skill

    def test_protected_region_skips_replace(self) -> None:
        skill = "<!-- SLOW_UPDATE_START -->\nprotected text\n<!-- SLOW_UPDATE_END -->"
        result = apply_edit(
            skill, Edit(op="replace", target="protected text", content="hacked")
        )
        assert result == skill

    def test_protected_region_skips_delete(self) -> None:
        skill = "<!-- SLOW_UPDATE_START -->\nprotected text\n<!-- SLOW_UPDATE_END -->"
        result = apply_edit(skill, Edit(op="delete", target="protected text"))
        assert result == skill

    def test_protected_region_appendix_skips_edit(self) -> None:
        skill = "<!-- APPENDIX_START -->\nappendix data\n<!-- APPENDIX_END -->"
        result = apply_edit(
            skill, Edit(op="replace", target="appendix data", content="changed")
        )
        assert result == skill


class TestApplyPatch:
    def test_applies_multiple_edits_sequentially(self) -> None:
        skill = "line1\nline2\nline3"
        p = Patch(
            edits=[
                Edit(op="replace", target="line1", content="LINE1"),
                Edit(op="delete", target="line2\n"),
                Edit(op="append", content="line4"),
            ]
        )
        result = apply_patch(skill, p)
        assert "LINE1" in result
        assert "line2" not in result
        assert result.endswith("line4")

    def test_empty_patch(self) -> None:
        skill = "original"
        result = apply_patch(skill, Patch(edits=[]))
        assert result == skill


# ---------------------------------------------------------------------------
# gate.py — Validation gate
# ---------------------------------------------------------------------------


class TestSelectGateScore:
    def test_hard_metric(self) -> None:
        assert select_gate_score(0.8, 0.5, "hard") == 0.8

    def test_soft_metric(self) -> None:
        assert select_gate_score(0.8, 0.5, "soft") == 0.5

    def test_mixed_metric(self) -> None:
        assert select_gate_score(0.8, 0.6, "mixed") == pytest.approx(0.7)

    def test_default_is_hard(self) -> None:
        assert select_gate_score(0.9, 0.3) == 0.9


class TestEvaluateGate:
    def test_accept_new_best(self) -> None:
        result = evaluate_gate(
            candidate_skill="v2",
            cand_hard=0.9,
            cand_soft=0.8,
            current_skill="v1",
            current_score=0.7,
            best_skill="v1",
            best_score=0.7,
            best_step=1,
            global_step=2,
            metric="hard",
        )
        assert result.action == "accept_new_best"
        assert result.current_skill == "v2"
        assert result.best_skill == "v2"
        assert result.best_score == 0.9
        assert result.best_step == 2

    def test_accept_beats_current_not_best(self) -> None:
        result = evaluate_gate(
            candidate_skill="v3",
            cand_hard=0.75,
            cand_soft=0.7,
            current_skill="v2",
            current_score=0.6,
            best_skill="v1",
            best_score=0.8,
            best_step=1,
            global_step=3,
            metric="hard",
        )
        assert result.action == "accept"
        assert result.current_skill == "v3"
        assert result.current_score == 0.75
        assert result.best_skill == "v1"
        assert result.best_score == 0.8

    def test_reject(self) -> None:
        result = evaluate_gate(
            candidate_skill="v2",
            cand_hard=0.5,
            cand_soft=0.4,
            current_skill="v1",
            current_score=0.7,
            best_skill="v1",
            best_score=0.7,
            best_step=1,
            global_step=2,
            metric="hard",
        )
        assert result.action == "reject"
        assert result.current_skill == "v1"
        assert result.current_score == 0.7

    def test_reject_equal_scores(self) -> None:
        result = evaluate_gate(
            candidate_skill="v2",
            cand_hard=0.7,
            cand_soft=0.5,
            current_skill="v1",
            current_score=0.7,
            best_skill="v1",
            best_score=0.7,
            best_step=1,
            global_step=2,
            metric="hard",
        )
        assert result.action == "reject"

    def test_soft_metric(self) -> None:
        result = evaluate_gate(
            candidate_skill="v2",
            cand_hard=0.5,
            cand_soft=0.9,
            current_skill="v1",
            current_score=0.6,
            best_skill="v1",
            best_score=0.6,
            best_step=1,
            global_step=2,
            metric="soft",
        )
        assert result.action == "accept_new_best"
        assert result.best_score == 0.9


# ---------------------------------------------------------------------------
# clip.py — Edit ranking fallback
# ---------------------------------------------------------------------------


class TestRankAndSelect:
    def test_returns_patch_unchanged_when_under_limit(self) -> None:
        p = Patch(edits=[Edit(op="append", content="a"), Edit(op="append", content="b")])
        result = rank_and_select("skill", p, max_edits=3)
        assert result is p

    def test_returns_patch_unchanged_when_equal_to_limit(self) -> None:
        edits = [Edit(op="append", content=str(i)) for i in range(3)]
        p = Patch(edits=edits)
        result = rank_and_select("skill", p, max_edits=3)
        assert result is p

    def test_truncates_when_llm_unavailable(self) -> None:
        edits = [Edit(op="append", content=str(i)) for i in range(5)]
        p = Patch(edits=edits, reasoning="original reasoning")
        with patch("factory.skillopt.clip._call_llm", return_value=None):
            result = rank_and_select("skill content", p, max_edits=2)
        assert len(result.edits) == 2
        assert result.reasoning == "original reasoning"


# ---------------------------------------------------------------------------
# adapter.py — EnvAdapter
# ---------------------------------------------------------------------------


class TestEnvAdapter:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            EnvAdapter()  # type: ignore[abstract]

    def test_concrete_adapter(self) -> None:
        class StubAdapter(EnvAdapter):
            def build_train_env(self, batch_size: int, seed: int) -> Any:
                return {"batch_size": batch_size, "seed": seed}

            def build_eval_env(self, env_num: int, split: str, seed: int) -> Any:
                return {"env_num": env_num, "split": split}

            def rollout(
                self, env_manager: Any, skill_content: str, out_dir: str,
            ) -> list[RolloutResult]:
                return [RolloutResult(id="t1", hard=1.0, soft=0.5)]

            def get_task_types(self) -> list[str]:
                return ["type_a"]

        adapter = StubAdapter()
        env = adapter.build_train_env(4, seed=0)
        assert env["batch_size"] == 4
        results = adapter.rollout(env, "skill", "/tmp/out")
        assert len(results) == 1
        assert adapter.get_task_types() == ["type_a"]


# ---------------------------------------------------------------------------
# yaml_surface.py — compute_prompt_change_magnitude
# ---------------------------------------------------------------------------


class TestComputePromptChangeMagnitude:
    def test_identical_strings(self) -> None:
        from factory.skillopt.yaml_surface import compute_prompt_change_magnitude

        assert compute_prompt_change_magnitude("hello\nworld\n", "hello\nworld\n") == 0

    def test_one_line_added(self) -> None:
        from factory.skillopt.yaml_surface import compute_prompt_change_magnitude

        result = compute_prompt_change_magnitude("line1\n", "line1\nnew line\n")
        assert result == 1

    def test_one_line_replaced(self) -> None:
        from factory.skillopt.yaml_surface import compute_prompt_change_magnitude

        result = compute_prompt_change_magnitude("old line\n", "new line\n")
        assert result == 2  # 1 removed + 1 added

    def test_multiple_changes(self) -> None:
        from factory.skillopt.yaml_surface import compute_prompt_change_magnitude

        old = "line1\nline2\nline3\n"
        new = "line1\nchanged2\nline3\nadded4\n"
        result = compute_prompt_change_magnitude(old, new)
        assert result == 3  # -line2, +changed2, +added4

    def test_empty_strings(self) -> None:
        from factory.skillopt.yaml_surface import compute_prompt_change_magnitude

        assert compute_prompt_change_magnitude("", "") == 0

    def test_empty_to_content(self) -> None:
        from factory.skillopt.yaml_surface import compute_prompt_change_magnitude

        result = compute_prompt_change_magnitude("", "new\n")
        assert result == 1


# ---------------------------------------------------------------------------
# trainer.py — Basic trainer construction and _compute_score
# ---------------------------------------------------------------------------


class _DummyAdapter(EnvAdapter):
    def build_train_env(self, batch_size: int, seed: int) -> Any:
        return None

    def build_eval_env(self, env_num: int, split: str, seed: int) -> Any:
        return None

    def rollout(
        self, env_manager: Any, skill_content: str, out_dir: str,
    ) -> list[RolloutResult]:
        return []

    def get_task_types(self) -> list[str]:
        return []


class TestSkillOptTrainer:
    def test_instantiation(self, tmp_path: Any) -> None:
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("# Skill\n")
        trainer = SkillOptTrainer(
            adapter=_DummyAdapter(),
            skill_path=str(skill_file),
            epochs=1,
            steps_per_epoch=1,
            out_dir=str(tmp_path / ".skillopt"),
        )
        assert trainer.epochs == 1
        assert trainer.best_score == -1.0
        assert trainer.global_step == 0

    def test_compute_score_empty(self) -> None:
        trainer = SkillOptTrainer(
            adapter=_DummyAdapter(),
            skill_path="/dev/null",
        )
        hard, soft = trainer._compute_score([])
        assert hard == 0.0
        assert soft == 0.0

    def test_compute_score_averages(self) -> None:
        trainer = SkillOptTrainer(
            adapter=_DummyAdapter(),
            skill_path="/dev/null",
        )
        results = [
            RolloutResult(id="a", hard=0.8, soft=0.6),
            RolloutResult(id="b", hard=0.6, soft=0.4),
        ]
        hard, soft = trainer._compute_score(results)
        assert hard == pytest.approx(0.7)
        assert soft == pytest.approx(0.5)

    def test_compute_score_single(self) -> None:
        trainer = SkillOptTrainer(
            adapter=_DummyAdapter(),
            skill_path="/dev/null",
        )
        results = [RolloutResult(id="x", hard=1.0, soft=0.0)]
        hard, soft = trainer._compute_score(results)
        assert hard == 1.0
        assert soft == 0.0


# ---------------------------------------------------------------------------
# reflect.py — Minibatch reflection with mocked LLM
# ---------------------------------------------------------------------------


class TestFmtTrajectory:
    def test_basic(self) -> None:
        from factory.skillopt.reflect import fmt_trajectory

        data = {"id": "task-1", "fail_reason": "timeout", "trace_dump": "line1\nline2"}
        result = fmt_trajectory(data)
        assert "ID: task-1" in result
        assert "Failure: timeout" in result
        assert "Trace:\nline1\nline2" in result

    def test_no_failure(self) -> None:
        from factory.skillopt.reflect import fmt_trajectory

        data = {"id": "task-2"}
        result = fmt_trajectory(data)
        assert "ID: task-2" in result
        assert "Failure" not in result

    def test_unknown_id(self) -> None:
        from factory.skillopt.reflect import fmt_trajectory

        result = fmt_trajectory({})
        assert "ID: unknown" in result

    def test_trace_truncation(self) -> None:
        from factory.skillopt.reflect import fmt_trajectory

        data = {"id": "t", "trace_dump": "x" * 10000}
        result = fmt_trajectory(data)
        assert len(result) < 9000


class TestFmtMinibatchTrajectories:
    def test_formats_items(self) -> None:
        from factory.skillopt.reflect import fmt_minibatch_trajectories

        items = [
            RolloutResult(id="a", hard=0.0, soft=0.0, fail_reason="crash"),
            RolloutResult(
                id="b", hard=1.0, soft=1.0, extras={"trace_dump": "some trace"},
            ),
        ]
        result = fmt_minibatch_trajectories(items)
        assert "Trace 1/2" in result
        assert "Trace 2/2" in result
        assert "id=a" in result
        assert "Failure: crash" in result
        assert "some trace" in result

    def test_empty_list(self) -> None:
        from factory.skillopt.reflect import fmt_minibatch_trajectories

        assert fmt_minibatch_trajectories([]) == ""

    def test_no_trace_data(self) -> None:
        from factory.skillopt.reflect import fmt_minibatch_trajectories

        items = [RolloutResult(id="x", hard=0.5, soft=0.5)]
        result = fmt_minibatch_trajectories(items)
        assert "(no trace data)" in result


class TestExtractJson:
    def test_valid_json(self) -> None:
        from factory.skillopt.reflect import _extract_json

        result = _extract_json('Some text {"key": "value"} trailing')
        assert result == {"key": "value"}

    def test_no_json(self) -> None:
        from factory.skillopt.reflect import _extract_json

        assert _extract_json("no json here") is None

    def test_invalid_json(self) -> None:
        from factory.skillopt.reflect import _extract_json

        assert _extract_json("{not valid json}") is None


class TestParseRawPatch:
    def test_valid_patch(self) -> None:
        from factory.skillopt.reflect import _parse_raw_patch

        data = {
            "patch": {
                "edits": [{"op": "append", "content": "new rule"}],
                "reasoning": "test",
            },
            "failure_summary": [
                {"failure_type": "timeout", "count": 3, "description": "timed out"},
            ],
        }
        result = _parse_raw_patch(data, "failure", 4)
        assert result is not None
        assert len(result.patch.edits) == 1
        assert result.source_type == "failure"
        assert result.batch_size == 4
        assert len(result.failure_summary) == 1

    def test_flat_data(self) -> None:
        from factory.skillopt.reflect import _parse_raw_patch

        data = {"edits": [{"op": "delete", "target": "bad rule"}], "reasoning": "r"}
        result = _parse_raw_patch(data, "success", 2)
        assert result is not None
        assert result.patch.edits[0].op == "delete"

    def test_invalid_data(self) -> None:
        from factory.skillopt.reflect import _parse_raw_patch

        result = _parse_raw_patch({"edits": [{"op": "invalid_op"}]}, "failure", 1)
        assert result is None


class TestRunErrorAnalystMinibatch:
    def test_success(self, monkeypatch: Any) -> None:
        from factory.skillopt import reflect

        llm_response = json.dumps({
            "patch": {
                "edits": [{"op": "append", "content": "- handle timeout"}],
                "reasoning": "add timeout handling",
            },
        })
        monkeypatch.setattr(reflect, "_call_llm", lambda prompt, timeout=300: llm_response)
        items = [RolloutResult(id="t1", hard=0.0, soft=0.0, fail_reason="timeout")]
        result = reflect.run_error_analyst_minibatch("# Skill", items)
        assert result is not None
        assert result.source_type == "failure"
        assert len(result.patch.edits) == 1

    def test_llm_returns_none(self, monkeypatch: Any) -> None:
        from factory.skillopt import reflect

        monkeypatch.setattr(reflect, "_call_llm", lambda prompt, timeout=300: None)
        items = [RolloutResult(id="t1", hard=0.0, soft=0.0)]
        assert reflect.run_error_analyst_minibatch("# Skill", items) is None

    def test_llm_returns_bad_json(self, monkeypatch: Any) -> None:
        from factory.skillopt import reflect

        monkeypatch.setattr(reflect, "_call_llm", lambda prompt, timeout=300: "not json")
        items = [RolloutResult(id="t1", hard=0.0, soft=0.0)]
        assert reflect.run_error_analyst_minibatch("# Skill", items) is None


class TestRunSuccessAnalystMinibatch:
    def test_success(self, monkeypatch: Any) -> None:
        from factory.skillopt import reflect

        llm_response = json.dumps({
            "patch": {
                "edits": [{"op": "append", "content": "- reinforce pattern"}],
                "reasoning": "reinforce success",
            },
        })
        monkeypatch.setattr(reflect, "_call_llm", lambda prompt, timeout=300: llm_response)
        items = [RolloutResult(id="s1", hard=1.0, soft=1.0)]
        result = reflect.run_success_analyst_minibatch("# Skill", items)
        assert result is not None
        assert result.source_type == "success"

    def test_llm_returns_none(self, monkeypatch: Any) -> None:
        from factory.skillopt import reflect

        monkeypatch.setattr(reflect, "_call_llm", lambda prompt, timeout=300: None)
        items = [RolloutResult(id="s1", hard=1.0, soft=1.0)]
        assert reflect.run_success_analyst_minibatch("# Skill", items) is None


class TestRunMinibatchReflect:
    def test_splits_and_collects(self, monkeypatch: Any) -> None:
        from factory.skillopt import reflect

        call_count = 0

        def mock_llm(prompt: str, timeout: int = 300) -> str:
            nonlocal call_count
            call_count += 1
            return json.dumps({
                "patch": {
                    "edits": [{"op": "append", "content": f"edit-{call_count}"}],
                    "reasoning": "reason",
                },
            })

        monkeypatch.setattr(reflect, "_call_llm", mock_llm)
        results = [
            RolloutResult(id="f1", hard=0.0, soft=0.0),
            RolloutResult(id="s1", hard=1.0, soft=1.0),
        ]
        patches = reflect.run_minibatch_reflect(
            results, "# Skill", minibatch_size=4, workers=1,
        )
        assert len(patches) == 2

    def test_all_failures(self, monkeypatch: Any) -> None:
        from factory.skillopt import reflect

        response = json.dumps({
            "patch": {"edits": [{"op": "append", "content": "fix"}], "reasoning": "r"},
        })
        monkeypatch.setattr(reflect, "_call_llm", lambda prompt, timeout=300: response)
        results = [RolloutResult(id=f"f{i}", hard=0.0, soft=0.0) for i in range(3)]
        patches = reflect.run_minibatch_reflect(
            results, "# Skill", minibatch_size=4, workers=1,
        )
        assert len(patches) == 1
        assert patches[0].source_type == "failure"

    def test_empty_results(self, monkeypatch: Any) -> None:
        from factory.skillopt import reflect

        monkeypatch.setattr(reflect, "_call_llm", lambda prompt, timeout=300: None)
        patches = reflect.run_minibatch_reflect([], "# Skill", workers=1)
        assert patches == []


class TestCallLlm:
    def test_no_claude_binary(self, monkeypatch: Any) -> None:
        from factory.skillopt import reflect

        monkeypatch.setattr("shutil.which", lambda cmd: None)
        assert reflect._call_llm("test prompt") is None

    def test_subprocess_timeout(self, monkeypatch: Any) -> None:
        from factory.skillopt import reflect

        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/claude")

        def raise_timeout(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=300)

        monkeypatch.setattr("subprocess.run", raise_timeout)
        assert reflect._call_llm("test") is None

    def test_successful_call(self, monkeypatch: Any) -> None:
        from factory.skillopt import reflect

        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                args=["claude", "-p", "test"], returncode=0, stdout="response text\n",
            ),
        )
        assert reflect._call_llm("test") == "response text"

    def test_empty_stdout(self, monkeypatch: Any) -> None:
        from factory.skillopt import reflect

        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                args=[], returncode=0, stdout="  \n",
            ),
        )
        assert reflect._call_llm("test") is None


# ---------------------------------------------------------------------------
# aggregate.py — Patch merging with mocked LLM
# ---------------------------------------------------------------------------


class TestMergePatches:
    def test_empty_both(self) -> None:
        from factory.skillopt.aggregate import merge_patches

        result = merge_patches("# Skill", [], [])
        assert result.edits == []

    def test_failure_only(self, monkeypatch: Any) -> None:
        from factory.skillopt.aggregate import merge_patches

        merged_response = json.dumps({
            "edits": [{"op": "append", "content": "- fix bug"}],
            "reasoning": "merged failure",
        })
        monkeypatch.setattr(
            "factory.skillopt.reflect._call_llm",
            lambda prompt, timeout=300: merged_response,
        )
        fp = RawPatch(
            patch=Patch(edits=[Edit(op="append", content="fix1")]),
            source_type="failure",
        )
        result = merge_patches("# Skill", [fp], [])
        assert len(result.edits) >= 1

    def test_success_only(self, monkeypatch: Any) -> None:
        from factory.skillopt.aggregate import merge_patches

        merged_response = json.dumps({
            "edits": [{"op": "append", "content": "- reinforce"}],
            "reasoning": "merged success",
        })
        monkeypatch.setattr(
            "factory.skillopt.reflect._call_llm",
            lambda prompt, timeout=300: merged_response,
        )
        sp = RawPatch(
            patch=Patch(edits=[Edit(op="append", content="good")]),
            source_type="success",
        )
        result = merge_patches("# Skill", [], [sp])
        assert len(result.edits) >= 1

    def test_both_patches(self, monkeypatch: Any) -> None:
        from factory.skillopt.aggregate import merge_patches

        monkeypatch.setattr(
            "factory.skillopt.reflect._call_llm",
            lambda prompt, timeout=300: json.dumps({
                "edits": [{"op": "append", "content": "combined"}],
                "reasoning": "final merge",
            }),
        )
        fp = RawPatch(
            patch=Patch(edits=[Edit(op="append", content="fix")]),
            source_type="failure",
        )
        sp = RawPatch(
            patch=Patch(edits=[Edit(op="append", content="reinforce")]),
            source_type="success",
        )
        result = merge_patches("# Skill", [fp], [sp])
        assert len(result.edits) >= 1

    def test_llm_fails_returns_failure_patch(self, monkeypatch: Any) -> None:
        from factory.skillopt.aggregate import merge_patches

        monkeypatch.setattr(
            "factory.skillopt.reflect._call_llm",
            lambda prompt, timeout=300: None,
        )
        fp = RawPatch(
            patch=Patch(edits=[Edit(op="append", content="fix")]),
            source_type="failure",
        )
        sp = RawPatch(
            patch=Patch(edits=[Edit(op="append", content="good")]),
            source_type="success",
        )
        result = merge_patches("# Skill", [fp], [sp])
        assert result.edits[0].content == "fix"


class TestHierarchicalMerge:
    def test_empty_patches(self) -> None:
        from factory.skillopt.aggregate import _hierarchical_merge

        result = _hierarchical_merge("# Skill", [], "prompt")
        assert result.edits == []

    def test_single_patch(self) -> None:
        from factory.skillopt.aggregate import _hierarchical_merge

        p = Patch(edits=[Edit(op="append", content="x")])
        result = _hierarchical_merge("# Skill", [p], "prompt")
        assert result is p

    def test_multiple_patches_merged(self, monkeypatch: Any) -> None:
        from factory.skillopt.aggregate import _hierarchical_merge

        monkeypatch.setattr(
            "factory.skillopt.reflect._call_llm",
            lambda prompt, timeout=300: json.dumps({
                "edits": [{"op": "append", "content": "merged"}],
                "reasoning": "combined",
            }),
        )
        patches = [
            Patch(edits=[Edit(op="append", content=f"p{i}")])
            for i in range(3)
        ]
        result = _hierarchical_merge("# Skill", patches, "{{SKILL_CONTENT}}{{PATCHES}}")
        assert len(result.edits) >= 1


class TestParsePatchAggregate:
    def test_parse_patch(self) -> None:
        from factory.skillopt.aggregate import _parse_patch

        data = {
            "edits": [
                {"op": "append", "content": "new rule", "support_count": 3},
                {"op": "delete", "target": "old rule"},
            ],
            "reasoning": "test reason",
        }
        result = _parse_patch(data)
        assert len(result.edits) == 2
        assert result.reasoning == "test reason"
        assert result.edits[0].support_count == 3

    def test_parse_empty(self) -> None:
        from factory.skillopt.aggregate import _parse_patch

        result = _parse_patch({})
        assert result.edits == []
        assert result.reasoning == ""


class TestExtractJsonAggregate:
    def test_valid(self) -> None:
        from factory.skillopt.aggregate import _extract_json

        result = _extract_json('prefix {"a": 1} suffix')
        assert result == {"a": 1}

    def test_invalid(self) -> None:
        from factory.skillopt.aggregate import _extract_json

        assert _extract_json("no json") is None


# ---------------------------------------------------------------------------
# clip.py — LLM-driven ranking with mocked LLM
# ---------------------------------------------------------------------------


class TestRankAndSelectLLM:
    def test_llm_returns_ranked_edits(self) -> None:
        edits = [Edit(op="append", content=str(i)) for i in range(5)]
        p = Patch(edits=edits, reasoning="original")
        llm_response = json.dumps({
            "edits": [
                {"op": "append", "content": "2"},
                {"op": "append", "content": "0"},
            ],
            "reasoning": "top 2 selected",
            "ranking_details": {"method": "llm"},
        })
        with patch("factory.skillopt.clip._call_llm", return_value=llm_response):
            result = rank_and_select("skill content", p, max_edits=2)
        assert len(result.edits) == 2
        assert result.edits[0].content == "2"
        assert result.edits[1].content == "0"
        assert result.reasoning == "top 2 selected"
        assert result.ranking_details == {"method": "llm"}

    def test_llm_returns_no_json_match(self) -> None:
        edits = [Edit(op="append", content=str(i)) for i in range(5)]
        p = Patch(edits=edits, reasoning="original")
        with patch("factory.skillopt.clip._call_llm", return_value="no json here"):
            result = rank_and_select("skill content", p, max_edits=2)
        assert len(result.edits) == 2
        assert result.reasoning == "original"

    def test_llm_returns_bad_json(self) -> None:
        edits = [Edit(op="append", content=str(i)) for i in range(5)]
        p = Patch(edits=edits, reasoning="original")
        with patch("factory.skillopt.clip._call_llm", return_value="{invalid json}"):
            result = rank_and_select("skill content", p, max_edits=2)
        assert len(result.edits) == 2

    def test_llm_respects_max_edits_cap(self) -> None:
        edits = [Edit(op="append", content=str(i)) for i in range(5)]
        p = Patch(edits=edits, reasoning="original")
        llm_response = json.dumps({
            "edits": [
                {"op": "append", "content": str(i)} for i in range(10)
            ],
            "reasoning": "too many",
        })
        with patch("factory.skillopt.clip._call_llm", return_value=llm_response):
            result = rank_and_select("skill content", p, max_edits=3)
        assert len(result.edits) == 3


# ---------------------------------------------------------------------------
# trainer.py — Full training loop with mock adapter
# ---------------------------------------------------------------------------


class _MockAdapter(EnvAdapter):
    """Mock adapter that returns controlled scores for training loop tests."""

    def __init__(
        self,
        train_results: list[RolloutResult] | None = None,
        eval_results: list[RolloutResult] | None = None,
        reflect_patches: list[RawPatch] | None = None,
    ) -> None:
        self._train_results = train_results or [
            RolloutResult(id="t1", hard=0.5, soft=0.3),
        ]
        self._eval_results = eval_results or [
            RolloutResult(id="e1", hard=0.8, soft=0.6),
        ]
        self._reflect_patches = reflect_patches or [
            RawPatch(
                patch=Patch(
                    edits=[Edit(op="append", content="- new rule")],
                    reasoning="improve",
                ),
                source_type="failure",
            ),
        ]

    def build_train_env(self, batch_size: int, seed: int) -> Any:
        return {"batch_size": batch_size, "seed": seed}

    def build_eval_env(self, env_num: int, split: str, seed: int) -> Any:
        return {"env_num": env_num, "split": split, "seed": seed}

    def rollout(
        self, env_manager: Any, skill_content: str, out_dir: str,
    ) -> list[RolloutResult]:
        if isinstance(env_manager, dict) and env_manager.get("split") == "eval":
            return list(self._eval_results)
        return list(self._train_results)

    def reflect(
        self,
        results: list[RolloutResult],
        skill_content: str,
        out_dir: str,
        **kwargs: Any,
    ) -> list[RawPatch]:
        return list(self._reflect_patches)

    def get_task_types(self) -> list[str]:
        return ["test_type"]


class TestTrainLoop:
    def test_one_step_accepted(self, tmp_path: Any) -> None:
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("# Skill\noriginal content")
        adapter = _MockAdapter(
            eval_results=[RolloutResult(id="e1", hard=0.9, soft=0.7)],
        )
        trainer = SkillOptTrainer(
            adapter=adapter,
            skill_path=str(skill_file),
            epochs=1,
            steps_per_epoch=1,
            batch_size=2,
            learning_rate=3,
            out_dir=str(tmp_path / ".skillopt"),
        )
        with patch("factory.skillopt.aggregate.merge_patches") as mock_merge, \
             patch("factory.skillopt.clip.rank_and_select") as mock_clip:
            merged = Patch(
                edits=[Edit(op="append", content="- new rule")],
                reasoning="merged",
            )
            mock_merge.return_value = merged
            mock_clip.return_value = merged
            trainer.train()

        assert trainer.best_score > 0
        assert trainer.global_step == 1
        final_skill = skill_file.read_text()
        assert "- new rule" in final_skill

    def test_one_step_rejected(self, tmp_path: Any) -> None:
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("# Skill\noriginal")
        adapter = _MockAdapter(
            train_results=[RolloutResult(id="t1", hard=0.8, soft=0.6)],
            eval_results=[RolloutResult(id="e1", hard=0.3, soft=0.2)],
        )
        trainer = SkillOptTrainer(
            adapter=adapter,
            skill_path=str(skill_file),
            epochs=1,
            steps_per_epoch=1,
            batch_size=2,
            learning_rate=3,
            out_dir=str(tmp_path / ".skillopt"),
        )
        with patch("factory.skillopt.aggregate.merge_patches") as mock_merge, \
             patch("factory.skillopt.clip.rank_and_select") as mock_clip:
            merged = Patch(
                edits=[Edit(op="append", content="- bad rule")],
                reasoning="bad",
            )
            mock_merge.return_value = merged
            mock_clip.return_value = merged
            trainer.train()

        assert len(trainer.rejected_edits) == 1
        final_skill = skill_file.read_text()
        assert "- bad rule" not in final_skill

    def test_checkpoint_created(self, tmp_path: Any) -> None:
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("# Skill")
        out_dir = tmp_path / ".skillopt"
        adapter = _MockAdapter()
        trainer = SkillOptTrainer(
            adapter=adapter,
            skill_path=str(skill_file),
            epochs=1,
            steps_per_epoch=1,
            out_dir=str(out_dir),
        )
        with patch("factory.skillopt.aggregate.merge_patches") as mock_merge, \
             patch("factory.skillopt.clip.rank_and_select") as mock_clip:
            merged = Patch(edits=[Edit(op="append", content="x")])
            mock_merge.return_value = merged
            mock_clip.return_value = merged
            trainer.train()

        ckpt_dir = out_dir / "checkpoints"
        assert ckpt_dir.is_dir()
        assert (ckpt_dir / "epoch1_step1_skill.md").exists()
        assert (ckpt_dir / "epoch1_step1_state.json").exists()
        assert (ckpt_dir / "final_skill.md").exists()
        assert (ckpt_dir / "final_state.json").exists()

    def test_no_patches_rejects(self, tmp_path: Any) -> None:
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("# Skill")
        adapter = _MockAdapter(reflect_patches=[])
        trainer = SkillOptTrainer(
            adapter=adapter,
            skill_path=str(skill_file),
            epochs=1,
            steps_per_epoch=1,
            out_dir=str(tmp_path / ".skillopt"),
        )
        trainer.train()
        assert trainer.global_step == 1

    def test_empty_merged_edits_rejects(self, tmp_path: Any) -> None:
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("# Skill")
        adapter = _MockAdapter()
        trainer = SkillOptTrainer(
            adapter=adapter,
            skill_path=str(skill_file),
            epochs=1,
            steps_per_epoch=1,
            out_dir=str(tmp_path / ".skillopt"),
        )
        with patch("factory.skillopt.aggregate.merge_patches") as mock_merge:
            mock_merge.return_value = Patch(edits=[])
            trainer.train()
        assert trainer.global_step == 1

    def test_multi_epoch_multi_step(self, tmp_path: Any) -> None:
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("# Skill")
        adapter = _MockAdapter(
            eval_results=[RolloutResult(id="e1", hard=0.9, soft=0.8)],
        )
        trainer = SkillOptTrainer(
            adapter=adapter,
            skill_path=str(skill_file),
            epochs=2,
            steps_per_epoch=2,
            out_dir=str(tmp_path / ".skillopt"),
        )
        with patch("factory.skillopt.aggregate.merge_patches") as mock_merge, \
             patch("factory.skillopt.clip.rank_and_select") as mock_clip:
            merged = Patch(edits=[Edit(op="append", content="r")])
            mock_merge.return_value = merged
            mock_clip.return_value = merged
            trainer.train()

        assert trainer.global_step == 4

    def test_step_artifacts_written(self, tmp_path: Any) -> None:
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("# Skill")
        out_dir = tmp_path / ".skillopt"
        adapter = _MockAdapter(
            eval_results=[RolloutResult(id="e1", hard=0.9, soft=0.8)],
        )
        trainer = SkillOptTrainer(
            adapter=adapter,
            skill_path=str(skill_file),
            epochs=1,
            steps_per_epoch=1,
            out_dir=str(out_dir),
        )
        with patch("factory.skillopt.aggregate.merge_patches") as mock_merge, \
             patch("factory.skillopt.clip.rank_and_select") as mock_clip:
            merged = Patch(edits=[Edit(op="append", content="x")])
            mock_merge.return_value = merged
            mock_clip.return_value = merged
            trainer.train()

        step_dir = out_dir / "epoch1" / "step1"
        assert (step_dir / "patch.json").exists()
        assert (step_dir / "gate.json").exists()


# ---------------------------------------------------------------------------
# swebench.py — SWE-bench adapter with mocked subprocess
# ---------------------------------------------------------------------------


class TestSwebenchAdapter:
    def test_build_train_env(self) -> None:
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        result = adapter.build_train_env(batch_size=4, seed=42)
        assert result == 4

    def test_build_eval_env(self) -> None:
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        result = adapter.build_eval_env(env_num=8, split="eval", seed=0)
        assert result == 8

    def test_get_task_types(self) -> None:
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        assert adapter.get_task_types() == ["bug_fix"]

    def test_setup(self, tmp_path: Any) -> None:
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        custom_path = str(tmp_path / "custom" / "SKILL.md")
        adapter.setup({"skill_path": custom_path})
        assert str(adapter.skill_path) == custom_path


class TestParseJobsDir:
    def test_extracts_path(self) -> None:
        from factory.skillopt.adapters.swebench import _parse_jobs_dir

        stdout = "Some output\nJobs directory: /tmp/harbor/jobs/123\nMore output"
        assert _parse_jobs_dir(stdout) == "/tmp/harbor/jobs/123"

    def test_no_match(self) -> None:
        from factory.skillopt.adapters.swebench import _parse_jobs_dir

        assert _parse_jobs_dir("no jobs dir here") == ""

    def test_whitespace_handling(self) -> None:
        from factory.skillopt.adapters.swebench import _parse_jobs_dir

        stdout = "Jobs directory:   /tmp/dir  \n"
        assert _parse_jobs_dir(stdout) == "/tmp/dir"


class TestCollectResults:
    def test_no_results_dir(self, tmp_path: Any, monkeypatch: Any) -> None:
        from factory.skillopt.adapters import swebench

        monkeypatch.setattr(swebench, "_RESULTS_DIR", tmp_path / "nonexistent")
        result = swebench._collect_results(str(tmp_path / "out"), "")
        assert result == []

    def test_empty_results_dir(self, tmp_path: Any, monkeypatch: Any) -> None:
        from factory.skillopt.adapters import swebench

        results_dir = tmp_path / "results"
        results_dir.mkdir()
        monkeypatch.setattr(swebench, "_RESULTS_DIR", results_dir)
        result = swebench._collect_results(str(tmp_path / "out"), "")
        assert result == []

    def test_parses_result_file(self, tmp_path: Any, monkeypatch: Any) -> None:
        from factory.skillopt.adapters import swebench

        results_dir = tmp_path / "results"
        results_dir.mkdir()
        result_file = results_dir / "20260101T000000Z-swebench-full.json"
        result_file.write_text(json.dumps({
            "tasks": [
                {"instance_id": "test__1", "resolved": True},
                {"instance_id": "test__2", "resolved": False, "fail_reason": "crash"},
            ],
        }))
        monkeypatch.setattr(swebench, "_RESULTS_DIR", results_dir)
        monkeypatch.setattr(swebench, "_fetch_trace_dump", lambda trace_id: "")
        out_dir = tmp_path / "out"
        results = swebench._collect_results(str(out_dir), "")
        assert len(results) == 2
        assert results[0].hard == 1.0
        assert results[1].hard == 0.0
        assert results[1].fail_reason == "crash"
        assert (out_dir / "rollout_results.json").exists()

    def test_bad_json_in_result_file(self, tmp_path: Any, monkeypatch: Any) -> None:
        from factory.skillopt.adapters import swebench

        results_dir = tmp_path / "results"
        results_dir.mkdir()
        (results_dir / "20260101T000000Z-swebench-full.json").write_text("not json")
        monkeypatch.setattr(swebench, "_RESULTS_DIR", results_dir)
        result = swebench._collect_results(str(tmp_path / "out"), "")
        assert result == []

    def test_no_tasks_key(self, tmp_path: Any, monkeypatch: Any) -> None:
        from factory.skillopt.adapters import swebench

        results_dir = tmp_path / "results"
        results_dir.mkdir()
        (results_dir / "20260101T000000Z-swebench-full.json").write_text("{}")
        monkeypatch.setattr(swebench, "_RESULTS_DIR", results_dir)
        result = swebench._collect_results(str(tmp_path / "out"), "")
        assert result == []


class TestExtractTraceIds:
    def test_extracts_from_jobs_dir(self, tmp_path: Any) -> None:
        from factory.skillopt.adapters.swebench import _extract_trace_ids_from_jobs

        trial_dir = tmp_path / "test__instance__abc1234"
        trial_dir.mkdir(parents=True)
        (trial_dir / "trace_id.txt").write_text("trace-abc-123")
        result = _extract_trace_ids_from_jobs(str(tmp_path))
        assert result.get("test__instance") == "trace-abc-123"

    def test_empty_jobs_dir(self) -> None:
        from factory.skillopt.adapters.swebench import _extract_trace_ids_from_jobs

        assert _extract_trace_ids_from_jobs("") == {}

    def test_nonexistent_dir(self) -> None:
        from factory.skillopt.adapters.swebench import _extract_trace_ids_from_jobs

        assert _extract_trace_ids_from_jobs("/nonexistent/path") == {}

    def test_agent_subdirectory(self, tmp_path: Any) -> None:
        from factory.skillopt.adapters.swebench import _extract_trace_ids_from_jobs

        trial_dir = tmp_path / "test__inst__xyz9876" / "agent"
        trial_dir.mkdir(parents=True)
        (trial_dir / "trace_id.txt").write_text("trace-xyz")
        result = _extract_trace_ids_from_jobs(str(tmp_path))
        assert result.get("test__inst") == "trace-xyz"


class TestCleanResultFiles:
    def test_removes_swebench_files(self, tmp_path: Any, monkeypatch: Any) -> None:
        from factory.skillopt.adapters import swebench

        results_dir = tmp_path / "results"
        results_dir.mkdir()
        (results_dir / "20260101T000000Z-swebench-full.json").write_text("{}")
        (results_dir / "20260102T000000Z-swebench-full.json").write_text("{}")
        (results_dir / "20260101T000000Z-featurebench-full.json").write_text("{}")
        monkeypatch.setattr(swebench, "_RESULTS_DIR", results_dir)

        swebench._clean_result_files()

        remaining = list(results_dir.iterdir())
        assert len(remaining) == 1
        assert remaining[0].name == "20260101T000000Z-featurebench-full.json"

    def test_noop_when_dir_missing(self, tmp_path: Any, monkeypatch: Any) -> None:
        from factory.skillopt.adapters import swebench

        monkeypatch.setattr(swebench, "_RESULTS_DIR", tmp_path / "nonexistent")
        swebench._clean_result_files()

    def test_noop_when_dir_empty(self, tmp_path: Any, monkeypatch: Any) -> None:
        from factory.skillopt.adapters import swebench

        results_dir = tmp_path / "results"
        results_dir.mkdir()
        monkeypatch.setattr(swebench, "_RESULTS_DIR", results_dir)
        swebench._clean_result_files()
        assert list(results_dir.iterdir()) == []


class TestSwebenchRollout:
    def test_script_not_found(self, tmp_path: Any, monkeypatch: Any) -> None:
        from factory.skillopt.adapters import swebench

        monkeypatch.setattr(swebench, "_BENCHMARKS_DIR", tmp_path / "nonexistent")
        adapter = swebench.SwebenchAdapter()
        skill_dir = tmp_path / "skills" / "workflow-swebench"
        skill_dir.mkdir(parents=True)
        adapter.skill_path = skill_dir / "SKILL.md"
        result = adapter.rollout(4, "# Skill", str(tmp_path / "out"))
        assert result == []

    def test_subprocess_timeout(self, tmp_path: Any, monkeypatch: Any) -> None:
        from factory.skillopt.adapters import swebench

        bench_dir = tmp_path / "bench"
        bench_dir.mkdir()
        script = bench_dir / "run-harbor.sh"
        script.write_text("#!/bin/bash\nexit 0")
        monkeypatch.setattr(swebench, "_BENCHMARKS_DIR", bench_dir)
        adapter = swebench.SwebenchAdapter()
        skill_dir = tmp_path / "skills" / "workflow-swebench"
        skill_dir.mkdir(parents=True)
        adapter.skill_path = skill_dir / "SKILL.md"

        def raise_timeout(*a: Any, **kw: Any) -> None:
            raise subprocess.TimeoutExpired(cmd="run-harbor.sh", timeout=9000)

        monkeypatch.setattr("subprocess.run", raise_timeout)
        result = adapter.rollout(4, "# Skill", str(tmp_path / "out"))
        assert result == []

    def test_cleans_stale_results_before_run(self, tmp_path: Any, monkeypatch: Any) -> None:
        from factory.skillopt.adapters import swebench

        bench_dir = tmp_path / "bench"
        bench_dir.mkdir()
        script = bench_dir / "run-harbor.sh"
        script.write_text("#!/bin/bash\nexit 0")
        script.chmod(0o755)
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        stale = results_dir / "20260101T000000Z-swebench-full.json"
        stale.write_text(json.dumps({"tasks": [{"instance_id": "old", "resolved": True}]}))

        monkeypatch.setattr(swebench, "_BENCHMARKS_DIR", bench_dir)
        monkeypatch.setattr(swebench, "_RESULTS_DIR", results_dir)

        adapter = swebench.SwebenchAdapter()
        skill_dir = tmp_path / "skills" / "workflow-swebench"
        skill_dir.mkdir(parents=True)
        adapter.skill_path = skill_dir / "SKILL.md"

        cleaned: list[str] = []
        original_clean = swebench._clean_result_files

        def track_clean() -> None:
            original_clean()
            cleaned.append("called")

        monkeypatch.setattr(swebench, "_clean_result_files", track_clean)

        def fake_run(*a: Any, **kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", fake_run)
        adapter.rollout(4, "# Skill", str(tmp_path / "out"))
        assert len(cleaned) == 1


# ---------------------------------------------------------------------------
# __main__.py — CLI argument parsing
# ---------------------------------------------------------------------------


class TestCliParsing:
    def test_parse_args(self) -> None:
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--benchmark", required=True, choices=[
            "swebench", "featurebench", "programbench", "terminalbench", "legacybench",
        ])
        parser.add_argument("--skill-path", required=True)
        parser.add_argument("--epochs", type=int, default=3)
        parser.add_argument("--steps-per-epoch", type=int, default=5)
        parser.add_argument("--batch-size", type=int, default=8)
        parser.add_argument("--learning-rate", type=int, default=3)
        parser.add_argument("--metric", choices=["hard", "soft", "mixed"], default="hard")
        args = parser.parse_args([
            "--benchmark", "swebench",
            "--skill-path", "/tmp/SKILL.md",
            "--epochs", "2",
        ])
        assert args.benchmark == "swebench"
        assert args.skill_path == "/tmp/SKILL.md"
        assert args.epochs == 2
        assert args.batch_size == 8

    def test_load_adapter_unknown(self) -> None:
        from factory.skillopt.__main__ import _load_adapter

        with pytest.raises(SystemExit):
            _load_adapter("nonexistent_bench")

    def test_load_adapter_swebench(self) -> None:
        from factory.skillopt.__main__ import _load_adapter
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = _load_adapter("swebench")
        assert isinstance(adapter, SwebenchAdapter)

    def test_main_missing_args(self, monkeypatch: Any) -> None:
        monkeypatch.setattr("sys.argv", ["skillopt"])
        from factory.skillopt.__main__ import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# Adapter stubs — verify NotImplementedError
# ---------------------------------------------------------------------------


class TestAdapterStubs:
    def test_featurebench_raises(self) -> None:
        from factory.skillopt.adapters.featurebench import FeaturebenchAdapter

        adapter = FeaturebenchAdapter()
        with pytest.raises(NotImplementedError):
            adapter.build_train_env(4, seed=0)
        with pytest.raises(NotImplementedError):
            adapter.build_eval_env(4, "eval", seed=0)
        with pytest.raises(NotImplementedError):
            adapter.rollout(None, "skill", "/tmp/out")
        with pytest.raises(NotImplementedError):
            adapter.get_task_types()

    def test_programbench_raises(self) -> None:
        from factory.skillopt.adapters.programbench import ProgrambenchAdapter

        adapter = ProgrambenchAdapter()
        with pytest.raises(NotImplementedError):
            adapter.build_train_env(4, seed=0)
        with pytest.raises(NotImplementedError):
            adapter.build_eval_env(4, "eval", seed=0)
        with pytest.raises(NotImplementedError):
            adapter.rollout(None, "skill", "/tmp/out")
        with pytest.raises(NotImplementedError):
            adapter.get_task_types()

    def test_terminalbench_raises(self) -> None:
        from factory.skillopt.adapters.terminalbench import TerminalbenchAdapter

        adapter = TerminalbenchAdapter()
        with pytest.raises(NotImplementedError):
            adapter.build_train_env(4, seed=0)
        with pytest.raises(NotImplementedError):
            adapter.build_eval_env(4, "eval", seed=0)
        with pytest.raises(NotImplementedError):
            adapter.rollout(None, "skill", "/tmp/out")
        with pytest.raises(NotImplementedError):
            adapter.get_task_types()

    def test_legacybench_raises(self) -> None:
        from factory.skillopt.adapters.legacybench import LegacybenchAdapter

        adapter = LegacybenchAdapter()
        with pytest.raises(NotImplementedError):
            adapter.build_train_env(4, seed=0)
        with pytest.raises(NotImplementedError):
            adapter.build_eval_env(4, "eval", seed=0)
        with pytest.raises(NotImplementedError):
            adapter.rollout(None, "skill", "/tmp/out")
        with pytest.raises(NotImplementedError):
            adapter.get_task_types()


# ---------------------------------------------------------------------------
# adapter.py — Base EnvAdapter.reflect default implementation
# ---------------------------------------------------------------------------


class TestEnvAdapterReflect:
    def test_reflect_delegates_to_run_minibatch(self, monkeypatch: Any) -> None:
        from factory.skillopt import reflect

        captured_kwargs: dict[str, Any] = {}

        def mock_run_minibatch(
            results: list, skill_content: str,
            minibatch_size: int = 4, edit_budget: int = 5, workers: int = 4,
            step_buffer_context: str = "",
            prompt_slots: dict | None = None,
            prompt_slots_text: str | None = None,
        ) -> list:
            captured_kwargs["minibatch_size"] = minibatch_size
            captured_kwargs["edit_budget"] = edit_budget
            return [RawPatch(patch=Patch(edits=[]), source_type="failure")]

        monkeypatch.setattr(reflect, "run_minibatch_reflect", mock_run_minibatch)
        adapter = _DummyAdapter()
        result = adapter.reflect([], "skill", "/tmp", minibatch_size=2, edit_budget=7)
        assert len(result) == 1
        assert captured_kwargs["minibatch_size"] == 2
        assert captured_kwargs["edit_budget"] == 7

    def test_setup_is_noop(self) -> None:
        adapter = _DummyAdapter()
        adapter.setup({"key": "val"})
