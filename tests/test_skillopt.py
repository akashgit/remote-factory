"""Unit tests for factory/skillopt/ — pure-logic modules only."""
from __future__ import annotations

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
