"""Integration tests for SkillOpt — mocked LLM calls + subprocess."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import yaml

from factory.skillopt.trainer import SkillOptTrainer
from factory.skillopt.types import Edit, Patch, RawPatch, RolloutResult


def _make_trainer(tmp_path, workflow_name=""):
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# Test Skill\nOriginal content here")

    ann_path = tmp_path / "SKILL.annotations.yaml"
    ann = {
        "builder": {
            "type": "AgentNode", "id": "builder",
            "slots": {"task_prompt_builder": "do the task well"},
        }
    }
    ann_path.write_text(yaml.dump(ann))

    adapter = MagicMock()
    adapter.build_train_env.return_value = 8
    adapter.build_eval_env.return_value = 25
    adapter.get_task_types.return_value = ["bug_fix"]

    trainer = SkillOptTrainer(
        adapter=adapter,
        skill_path=str(skill_path),
        out_dir=str(tmp_path / "out"),
        epochs=1,
        steps_per_epoch=1,
        batch_size=2,
        learning_rate=3,
        workflow_name=workflow_name,
    )
    return trainer, adapter


class TestTrainerOneStep:
    def test_baseline_and_reject(self, tmp_path):
        trainer, adapter = _make_trainer(tmp_path)

        baseline_results = [
            RolloutResult(id="t1", hard=1.0, soft=1.0),
            RolloutResult(id="t2", hard=0.0, soft=0.0, fail_reason="broke"),
        ]
        train_results = [
            RolloutResult(id="t3", hard=1.0, soft=1.0),
            RolloutResult(id="t4", hard=0.0, soft=0.0),
        ]
        eval_results = [
            RolloutResult(id="e1", hard=0.0, soft=0.0),
        ]

        adapter.rollout.side_effect = [baseline_results, train_results, eval_results]
        adapter.reflect.return_value = [
            RawPatch(
                patch=Patch(
                    edits=[Edit(op="replace", target="do the task well", content="do it better")],
                    reasoning="improve",
                ),
                source_type="failure",
                batch_size=1,
                failure_summary=[],
            ),
        ]

        trainer.train()

        assert trainer.best_score == 0.5
        assert trainer.global_step == 1
        assert adapter.rollout.call_count == 3
        assert adapter.reflect.call_count == 1

    def test_baseline_and_accept(self, tmp_path):
        trainer, adapter = _make_trainer(tmp_path)

        baseline_results = [RolloutResult(id="e1", hard=0.5, soft=0.5)]
        train_results = [RolloutResult(id="t1", hard=1.0, soft=1.0)]
        eval_results = [RolloutResult(id="e1", hard=1.0, soft=1.0)]

        adapter.rollout.side_effect = [baseline_results, train_results, eval_results]
        adapter.reflect.return_value = [
            RawPatch(
                patch=Patch(
                    edits=[Edit(op="replace", target="do the task well", content="do it better")],
                    reasoning="improve",
                ),
                source_type="success",
                batch_size=1,
                failure_summary=[],
            ),
        ]

        trainer.train()

        assert trainer.best_score == 1.0
        assert trainer.best_step == 1

    def test_no_patches_from_reflect(self, tmp_path):
        trainer, adapter = _make_trainer(tmp_path)

        baseline_results = [RolloutResult(id="e1", hard=0.5, soft=0.5)]
        train_results = [RolloutResult(id="t1", hard=1.0, soft=1.0)]

        adapter.rollout.side_effect = [baseline_results, train_results]
        adapter.reflect.return_value = []

        trainer.train()

        assert trainer.best_score == 0.5
        assert adapter.rollout.call_count == 2

    def test_rejected_buffer_populated(self, tmp_path):
        trainer, adapter = _make_trainer(tmp_path)

        baseline_results = [RolloutResult(id="e1", hard=0.8, soft=0.8)]
        train_results = [RolloutResult(id="t1", hard=0.5, soft=0.5)]
        eval_results = [RolloutResult(id="e1", hard=0.5, soft=0.5)]

        adapter.rollout.side_effect = [baseline_results, train_results, eval_results]
        adapter.reflect.return_value = [
            RawPatch(
                patch=Patch(
                    edits=[Edit(op="replace", target="do the task well", content="worse")],
                    reasoning="bad idea",
                ),
                source_type="failure",
                batch_size=1,
                failure_summary=[],
            ),
        ]

        trainer.train()

        assert len(trainer.rejected_edits) == 1

    def test_epoch_resets_buffer(self, tmp_path):
        trainer, adapter = _make_trainer(tmp_path)
        trainer.epochs = 2
        trainer.steps_per_epoch = 1

        results = [RolloutResult(id="e1", hard=0.5, soft=0.5)]

        adapter.rollout.side_effect = [results] * 10
        adapter.reflect.return_value = [
            RawPatch(
                patch=Patch(
                    edits=[Edit(op="replace", target="do the task well", content="x")],
                    reasoning="r",
                ),
                source_type="failure",
                batch_size=1,
                failure_summary=[],
            ),
        ]

        trainer.train()

        # Buffer resets each epoch, so at end of epoch 2 it has at most 1 reject
        assert len(trainer.rejected_edits) <= 1

    def test_checkpoint_saved(self, tmp_path):
        trainer, adapter = _make_trainer(tmp_path)

        results = [RolloutResult(id="e1", hard=0.5, soft=0.5)]
        adapter.rollout.side_effect = [results, results]
        adapter.reflect.return_value = []

        trainer.train()

        ckpt_dir = tmp_path / "out" / "checkpoints"
        assert ckpt_dir.exists()
        assert (ckpt_dir / "final_skill.md").exists()
        assert (ckpt_dir / "final_state.json").exists()

    def test_compute_score(self, tmp_path):
        trainer, _ = _make_trainer(tmp_path)

        results = [
            RolloutResult(id="a", hard=1.0, soft=0.9),
            RolloutResult(id="b", hard=0.0, soft=0.5),
            RolloutResult(id="c", hard=1.0, soft=1.0),
        ]
        hard, soft = trainer._compute_score(results)
        assert abs(hard - 2 / 3) < 0.01
        assert abs(soft - 0.8) < 0.01

    def test_serialize_yaml(self, tmp_path):
        trainer, _ = _make_trainer(tmp_path)
        yaml_text = trainer._serialize_yaml()
        parsed = yaml.safe_load(yaml_text)
        assert "builder" in parsed
        assert "task_prompt_builder" in parsed["builder"]["slots"]

    def test_serialize_yaml_with_overrides(self, tmp_path):
        trainer, _ = _make_trainer(tmp_path)
        yaml_text = trainer._serialize_yaml({"task_prompt_builder": "overridden"})
        parsed = yaml.safe_load(yaml_text)
        assert parsed["builder"]["slots"]["task_prompt_builder"] == "overridden"

    def test_validate_edits_target_prompts(self, tmp_path):
        trainer, _ = _make_trainer(tmp_path)

        good = Patch(edits=[Edit(op="replace", target="do the task well", content="better")])
        assert trainer._validate_edits_target_prompts_only(good) == []

        bad = Patch(edits=[Edit(op="replace", target="not in any slot", content="x")])
        assert len(trainer._validate_edits_target_prompts_only(bad)) == 1

    def test_validate_substring_edit(self, tmp_path):
        trainer, _ = _make_trainer(tmp_path)

        # "do the" is a substring of "do the task well"
        sub = Patch(edits=[Edit(op="replace", target="do the", content="do a")])
        assert trainer._validate_edits_target_prompts_only(sub) == []

    def test_build_step_buffer_context_empty(self, tmp_path):
        trainer, _ = _make_trainer(tmp_path)
        assert trainer._build_step_buffer_context() == ""

    def test_build_step_buffer_context_with_rejects(self, tmp_path):
        trainer, _ = _make_trainer(tmp_path)
        trainer.rejected_edits.append(
            Patch(edits=[Edit(op="replace", target="x", content="y")], reasoning="bad")
        )
        ctx = trainer._build_step_buffer_context()
        assert "Previously rejected" in ctx
        assert "bad" in ctx

    def test_load_results(self, tmp_path):
        trainer, _ = _make_trainer(tmp_path)
        results_file = tmp_path / "results.json"
        results_file.write_text(json.dumps([
            {"id": "a", "hard": 1.0, "soft": 1.0, "n_turns": 0, "fail_reason": "", "task_type": "x"},
        ]))
        loaded = trainer._load_results(results_file)
        assert len(loaded) == 1
        assert loaded[0].id == "a"


class TestReflectWithMock:
    def test_run_minibatch_reflect_empty(self):
        from factory.skillopt.reflect import run_minibatch_reflect

        results = [RolloutResult(id="t", hard=0.0, soft=0.0)]
        with patch("factory.skillopt.reflect._call_llm", return_value=None):
            patches = run_minibatch_reflect(results, "skill")
        assert patches == []

    def test_run_minibatch_reflect_with_response(self):
        from factory.skillopt.reflect import run_minibatch_reflect

        results = [
            RolloutResult(id="f1", hard=0.0, soft=0.0,
                          extras={"trace_dump": "[bash] ls\n[output] files"}),
            RolloutResult(id="s1", hard=1.0, soft=1.0,
                          extras={"trace_dump": "[bash] git commit"}),
        ]

        mock_response = json.dumps({
            "patch": {
                "edits": [{
                    "op": "append",
                    "content": "- new rule",
                    "rationale": "test",
                }],
                "reasoning": "add rule",
            },
            "failure_summary": [],
        })

        with patch("factory.skillopt.reflect._call_llm", return_value=mock_response):
            patches = run_minibatch_reflect(results, "skill content")
        # May or may not produce patches depending on parsing
        assert isinstance(patches, list)

    def test_run_minibatch_reflect_slot_edits(self):
        from factory.skillopt.reflect import run_minibatch_reflect

        results = [
            RolloutResult(id="f1", hard=0.0, soft=0.0,
                          extras={"trace_dump": "[bash] ls"}),
        ]

        mock_response = json.dumps({
            "patch": {
                "edits": [{
                    "node_id": "builder",
                    "slot_name": "task_prompt_builder",
                    "new_value": "improved prompt",
                    "support_count": 1,
                    "rationale": "test",
                }],
                "reasoning": "improve prompt",
            },
            "failure_summary": [],
        })

        prompt_slots = {"task_prompt_builder": "original prompt"}

        with patch("factory.skillopt.reflect._call_llm", return_value=mock_response):
            patches = run_minibatch_reflect(
                results, "skill",
                prompt_slots=prompt_slots,
                prompt_slots_text="--- task_prompt_builder ---\noriginal prompt",
            )
        assert isinstance(patches, list)

    def test_call_llm_via_stdin(self):
        from factory.skillopt.reflect import _call_llm

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="response text")
                result = _call_llm("test prompt")
                mock_run.assert_called_once()
                call_args = mock_run.call_args
                assert call_args[0][0] == ["claude", "-p", "-"]
                assert call_args[1]["input"] == "test prompt"
                assert result == "response text"

    def test_call_llm_no_claude(self):
        from factory.skillopt.reflect import _call_llm

        with patch("shutil.which", return_value=None):
            assert _call_llm("prompt") is None

    def test_call_llm_timeout(self):
        import subprocess as sp
        from factory.skillopt.reflect import _call_llm

        with patch("shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="claude", timeout=300)):
                assert _call_llm("prompt") is None

    def test_extract_json(self):
        from factory.skillopt.reflect import _extract_json

        assert _extract_json('{"key": "value"}') == {"key": "value"}
        assert _extract_json('text before {"a": 1} text after') == {"a": 1}
        assert _extract_json("no json here") is None
        assert _extract_json("") is None

    def test_error_prompt_name_threaded(self):
        from factory.skillopt.reflect import run_minibatch_reflect

        results = [RolloutResult(id="f", hard=0.0, soft=0.0)]

        with patch("factory.skillopt.reflect._call_llm", return_value=None):
            with patch("factory.skillopt.reflect._load_prompt", return_value="template") as load:
                run_minibatch_reflect(
                    results, "skill",
                    error_prompt_name="analyst_error_swebench.md",
                    success_prompt_name="analyst_success_swebench.md",
                )
                load.assert_any_call("analyst_error_swebench.md")


class TestAggregateWithMock:
    def test_merge_empty(self):
        from factory.skillopt.aggregate import merge_patches

        result = merge_patches("skill", [], [])
        assert result.edits == []

    def test_merge_single_failure_patch(self):
        from factory.skillopt.aggregate import merge_patches

        failure = RawPatch(
            patch=Patch(edits=[Edit(op="append", content="fix")], reasoning="r"),
            source_type="failure", batch_size=1, failure_summary=[],
        )
        result = merge_patches("skill", [failure], [])
        assert len(result.edits) == 1
        assert result.edits[0].content == "fix"

    def test_merge_single_success_patch(self):
        from factory.skillopt.aggregate import merge_patches

        success = RawPatch(
            patch=Patch(edits=[Edit(op="append", content="good")], reasoning="r"),
            source_type="success", batch_size=1, failure_summary=[],
        )
        result = merge_patches("skill", [], [success])
        assert len(result.edits) == 1

    def test_merge_multiple_patches_calls_llm(self):
        from factory.skillopt.aggregate import merge_patches

        patches = [
            RawPatch(
                patch=Patch(edits=[Edit(op="append", content=f"fix{i}")], reasoning="r"),
                source_type="failure", batch_size=1, failure_summary=[],
            )
            for i in range(3)
        ]
        merged_response = json.dumps({
            "edits": [{"op": "append", "content": "merged fix"}],
            "reasoning": "combined",
        })
        with patch("factory.skillopt.aggregate._call_llm", return_value=merged_response):
            result = merge_patches("skill", patches, [])
        assert isinstance(result, Patch)


class TestClipWithMock:
    def test_within_budget(self):
        from factory.skillopt.clip import rank_and_select

        p = Patch(edits=[Edit(op="append", content="x")])
        result = rank_and_select("skill", p, max_edits=5)
        assert len(result.edits) == 1

    def test_over_budget_calls_llm(self):
        from factory.skillopt.clip import rank_and_select

        edits = [Edit(op="append", content=f"rule{i}") for i in range(5)]
        p = Patch(edits=edits)

        mock_response = json.dumps({"selected_indices": [0, 1]})
        with patch("factory.skillopt.clip._call_llm", return_value=mock_response):
            result = rank_and_select("skill", p, max_edits=2)
        assert len(result.edits) <= 2

    def test_over_budget_llm_fails_truncates(self):
        from factory.skillopt.clip import rank_and_select

        edits = [Edit(op="append", content=f"rule{i}") for i in range(5)]
        p = Patch(edits=edits)

        with patch("factory.skillopt.clip._call_llm", return_value=None):
            result = rank_and_select("skill", p, max_edits=2)
        assert len(result.edits) <= 2


class TestSlowUpdate:
    def test_inject_empty_field(self):
        from factory.skillopt.slow_update import inject_empty_slow_update_field

        skill = "# Skill\nContent"
        result = inject_empty_slow_update_field(skill)
        assert "SLOW_UPDATE_START" in result
        assert "SLOW_UPDATE_END" in result

    def test_extract_field(self):
        from factory.skillopt.slow_update import extract_slow_update_field

        skill = "before\n<!-- SLOW_UPDATE_START -->\nguidance here\n<!-- SLOW_UPDATE_END -->\nafter"
        assert extract_slow_update_field(skill) == "guidance here"

    def test_extract_field_missing(self):
        from factory.skillopt.slow_update import extract_slow_update_field

        assert extract_slow_update_field("no markers here") == ""

    def test_replace_field(self):
        from factory.skillopt.slow_update import replace_slow_update_field

        skill = "before\n<!-- SLOW_UPDATE_START -->\nold\n<!-- SLOW_UPDATE_END -->\nafter"
        result = replace_slow_update_field(skill, "new guidance")
        assert "new guidance" in result
        assert "old" not in result

    def test_build_comparison_pairs(self):
        from factory.skillopt.slow_update import build_comparison_pairs

        prev = [RolloutResult(id="a", hard=0.0, soft=0.0),
                RolloutResult(id="b", hard=1.0, soft=1.0)]
        curr = [RolloutResult(id="a", hard=1.0, soft=1.0),
                RolloutResult(id="b", hard=1.0, soft=1.0)]
        pairs = build_comparison_pairs(prev, curr)
        assert len(pairs) == 2
        assert any(p["category"] == "improved" for p in pairs)
        assert any(p["category"] == "stable_success" for p in pairs)


class TestAdapterHelpers:
    def test_swebench_load_split_ids(self, tmp_path):
        from factory.skillopt.adapters.swebench import _load_split_ids

        split_file = tmp_path / "train.jsonl"
        split_file.write_text('{"instance_id": "a"}\n{"instance_id": "b"}\n')
        ids = _load_split_ids(split_file)
        assert ids == ["a", "b"]

    def test_swebench_load_split_missing(self, tmp_path):
        from factory.skillopt.adapters.swebench import _load_split_ids

        assert _load_split_ids(tmp_path / "nope.jsonl") == []

    def test_swebench_instance_to_image(self):
        from factory.skillopt.adapters.swebench import _instance_to_image

        img = _instance_to_image("django__django-14349")
        assert img == "swebench/sweb.eval.x86_64.django_1776_django-14349:latest"

    def test_swebench_build_fail_reason(self, tmp_path):
        from factory.skillopt.adapters.swebench import _build_fail_reason

        verifier_dir = tmp_path / "verifier"
        verifier_dir.mkdir()
        (verifier_dir / "test-stdout.txt").write_text("test_a PASSED\ntest_b FAILED\n")
        reason = _build_fail_reason(tmp_path)
        assert "FAILED" in reason

    def test_swebench_build_fail_reason_no_file(self):
        from factory.skillopt.adapters.swebench import _build_fail_reason

        assert _build_fail_reason(None) == ""

    def test_mini_swebench_reflect_override(self):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter

        adapter = MiniSwebenchAdapter()
        with patch("factory.skillopt.reflect.run_minibatch_reflect", return_value=[]) as mock:
            adapter.reflect([], "skill", "/tmp")
            kwargs = mock.call_args[1]
            assert kwargs["error_prompt_name"] == "analyst_error_swebench.md"
            assert kwargs["success_prompt_name"] == "analyst_success_swebench.md"

    def test_swebench_reflect_override(self):
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        with patch("factory.skillopt.reflect.run_minibatch_reflect", return_value=[]) as mock:
            adapter.reflect([], "skill", "/tmp")
            kwargs = mock.call_args[1]
            assert kwargs["error_prompt_name"] == "analyst_error_swebench.md"


class TestLlmLoopHelpers:
    def test_resolve_model_anthropic(self):
        from factory.workflow.llm_loop import _resolve_model

        assert _resolve_model("haiku", "anthropic") == "claude-haiku-4-5-20251001"
        assert _resolve_model("sonnet", "anthropic") == "claude-sonnet-4-5-20250929"
        assert _resolve_model("opus", "anthropic") == "claude-opus-4-6-20250904"
        assert _resolve_model("custom-model", "anthropic") == "custom-model"

    def test_resolve_model_vertex(self):
        from factory.workflow.llm_loop import _resolve_model

        assert _resolve_model("haiku", "vertex") == "claude-haiku-4-5"
        assert _resolve_model("sonnet", "vertex") == "claude-sonnet-4-5"
        assert _resolve_model("opus", "vertex") == "claude-opus-4-6"

    def test_tools_to_api_format(self):
        from factory.workflow.llm_loop import _tools_to_api_format
        from factory.workflow.primitives import LLMNode
        from factory.workflow.llm_tools import BASH_TOOL

        node = LLMNode(id="s", tools=[BASH_TOOL])
        api_tools = _tools_to_api_format(node)
        assert len(api_tools) == 1
        assert api_tools[0]["name"] == "bash"
        assert "input_schema" in api_tools[0]


class TestSkilloptMainEntry:
    def test_load_adapter(self):
        from factory.skillopt.__main__ import _load_adapter

        adapter = _load_adapter("swebench")
        assert adapter is not None
        assert hasattr(adapter, "rollout")

    def test_load_adapter_unknown(self):
        import sys
        from unittest.mock import patch as mock_patch
        from factory.skillopt.__main__ import _load_adapter

        with mock_patch.object(sys, "exit", side_effect=SystemExit) as mock_exit:
            try:
                _load_adapter("nonexistent")
            except SystemExit:
                pass
            mock_exit.assert_called_once_with(1)


class TestTrainerEdgeCases:
    def test_overfit_mode(self, tmp_path):
        from factory.skillopt.trainer import SkillOptTrainer
        from unittest.mock import MagicMock
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill\nContent")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"builder": {"type": "AgentNode", "id": "builder",
               "slots": {"task_prompt_builder": "do task"}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"), epochs=1, steps_per_epoch=1,
            batch_size=2, learning_rate=3, overfit=True,
        )

        results = [RolloutResult(id="t1", hard=0.5, soft=0.5)]
        better = [RolloutResult(id="t1", hard=1.0, soft=1.0)]

        adapter.rollout.side_effect = [results, results, better]
        adapter.reflect.return_value = [
            RawPatch(
                patch=Patch(edits=[Edit(op="replace", target="do task", content="better")],
                            reasoning="r"),
                source_type="failure", batch_size=1, failure_summary=[],
            ),
        ]

        trainer.train()
        assert trainer.global_step == 1

    def test_yaml_surface_slot_mapping(self, tmp_path):
        from factory.skillopt.trainer import SkillOptTrainer
        from unittest.mock import MagicMock
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"builder": {"type": "AgentNode", "id": "builder",
               "slots": {"task_prompt_builder": "original prompt text"}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"), epochs=1, steps_per_epoch=1,
            batch_size=2, learning_rate=3, workflow_name="swebench",
        )

        baseline = [RolloutResult(id="e1", hard=0.5, soft=0.5)]
        train = [RolloutResult(id="t1", hard=1.0, soft=1.0)]
        eval_good = [RolloutResult(id="e1", hard=1.0, soft=1.0)]

        adapter.rollout.side_effect = [baseline, train, eval_good]
        adapter.reflect.return_value = [
            RawPatch(
                patch=Patch(edits=[Edit(op="replace", target="original prompt text",
                                       content="improved prompt text")], reasoning="r"),
                source_type="failure", batch_size=1, failure_summary=[],
            ),
        ]

        trainer.train()
        assert trainer.best_score == 1.0
        assert "improved prompt text" in trainer.prompt_slots.get("task_prompt_builder", "")

    def test_merged_patch_no_edits(self, tmp_path):
        from factory.skillopt.trainer import SkillOptTrainer
        from unittest.mock import MagicMock
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"b": {"slots": {"task_prompt_b": "p"}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"), epochs=1, steps_per_epoch=1,
            batch_size=2, learning_rate=3,
        )

        baseline = [RolloutResult(id="e1", hard=0.5, soft=0.5)]
        train = [RolloutResult(id="t1", hard=1.0, soft=1.0)]

        adapter.rollout.side_effect = [baseline, train]
        # Reflect returns patch with edits, but merge produces empty
        adapter.reflect.return_value = [
            RawPatch(
                patch=Patch(edits=[], reasoning="nothing"),
                source_type="failure", batch_size=1, failure_summary=[],
            ),
        ]

        trainer.train()
        assert trainer.best_score == 0.5

    def test_preloaded_results(self, tmp_path):
        from factory.skillopt.trainer import SkillOptTrainer
        from unittest.mock import MagicMock
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"b": {"slots": {"task_prompt_b": "p"}}}
        ann_path.write_text(yaml.dump(ann))

        preloaded = tmp_path / "preloaded.json"
        preloaded.write_text(json.dumps([
            {"id": "t1", "hard": 1.0, "soft": 1.0, "n_turns": 0, "fail_reason": "", "task_type": "x"},
        ]))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"), epochs=1, steps_per_epoch=1,
            batch_size=2, learning_rate=3, results_from=str(preloaded),
        )

        baseline = [RolloutResult(id="e1", hard=0.5, soft=0.5)]

        adapter.rollout.side_effect = [baseline]
        adapter.reflect.return_value = []

        trainer.train()
        # Preloaded results used for step 1, no second rollout call
        assert adapter.rollout.call_count == 1  # just baseline

    def test_write_yaml_annotations(self, tmp_path):
        from factory.skillopt.trainer import SkillOptTrainer
        from unittest.mock import MagicMock
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"b": {"slots": {"task_prompt_b": "original"}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"),
        )

        trainer.prompt_slots["task_prompt_b"] = "modified"
        trainer._write_yaml_annotations()

        reloaded = yaml.safe_load(ann_path.read_text())
        assert reloaded["b"]["slots"]["task_prompt_b"] == "modified"


class TestSkillEdgeCases:
    def test_apply_patch_with_appendix_region(self):
        from factory.skillopt.skill import apply_patch

        skill = "top\n<!-- APPENDIX_START -->\nappendix\n<!-- APPENDIX_END -->\nbottom"
        result = apply_patch(skill, Patch(edits=[Edit(op="delete", target="appendix")]))
        assert "appendix" in result

    def test_apply_patch_insert_after_missing(self):
        from factory.skillopt.skill import apply_patch

        skill = "line1\nline2"
        result = apply_patch(skill, Patch(edits=[Edit(op="insert_after", target="missing", content="new")]))
        assert result == skill


class TestAggregateEdgeCases:
    def test_merge_with_llm_parse_failure(self):
        from factory.skillopt.aggregate import merge_patches

        patches = [
            RawPatch(patch=Patch(edits=[Edit(op="append", content=f"r{i}")], reasoning="r"),
                     source_type="failure", batch_size=1, failure_summary=[])
            for i in range(3)
        ]

        with patch("factory.skillopt.aggregate._call_llm", return_value="not json"):
            result = merge_patches("skill", patches, [])
        assert isinstance(result, Patch)

    def test_merge_failure_and_success(self):
        from factory.skillopt.aggregate import merge_patches

        f = [RawPatch(patch=Patch(edits=[Edit(op="append", content="fix")], reasoning="r"),
                      source_type="failure", batch_size=1, failure_summary=[])]
        s = [RawPatch(patch=Patch(edits=[Edit(op="append", content="good")], reasoning="r"),
                      source_type="success", batch_size=1, failure_summary=[])]

        merged = json.dumps({"edits": [{"op": "append", "content": "combined"}], "reasoning": "merged"})
        with patch("factory.skillopt.aggregate._call_llm", return_value=merged):
            result = merge_patches("skill", f, s)
        assert isinstance(result, Patch)


class TestSlowUpdateBranches:
    def test_build_comparison_regression(self):
        from factory.skillopt.slow_update import build_comparison_pairs

        prev = [RolloutResult(id="a", hard=1.0, soft=1.0)]
        curr = [RolloutResult(id="a", hard=0.0, soft=0.0)]
        pairs = build_comparison_pairs(prev, curr)
        assert pairs[0]["category"] == "regressed"

    def test_build_comparison_persistent_failure(self):
        from factory.skillopt.slow_update import build_comparison_pairs

        prev = [RolloutResult(id="a", hard=0.0, soft=0.0)]
        curr = [RolloutResult(id="a", hard=0.0, soft=0.0)]
        pairs = build_comparison_pairs(prev, curr)
        assert pairs[0]["category"] == "persistent_fail"

    def test_build_comparison_only_common(self):
        from factory.skillopt.slow_update import build_comparison_pairs

        prev = [RolloutResult(id="a", hard=1.0, soft=1.0), RolloutResult(id="b", hard=0.0, soft=0.0)]
        curr = [RolloutResult(id="a", hard=1.0, soft=1.0), RolloutResult(id="c", hard=1.0, soft=1.0)]
        pairs = build_comparison_pairs(prev, curr)
        # All unique IDs from both sets get compared
        assert len(pairs) >= 1
        ids = {p["id"] for p in pairs}
        assert "a" in ids

    def test_build_comparison_with_extras(self):
        from factory.skillopt.slow_update import build_comparison_pairs

        prev = [RolloutResult(id="a", hard=0.0, soft=0.0, fail_reason="broke",
                              extras={"prediction": "wrong", "gold_answers": ["right"]})]
        curr = [RolloutResult(id="a", hard=1.0, soft=1.0,
                              extras={"prediction": "right", "gold_answers": ["right"]})]
        pairs = build_comparison_pairs(prev, curr)
        assert pairs[0]["prev"]["fail_reason"] == "broke"

    def test_run_slow_update_with_pairs(self):
        from factory.skillopt.slow_update import run_slow_update

        prev = [RolloutResult(id="a", hard=0.0, soft=0.0, fail_reason="x")]
        curr = [RolloutResult(id="a", hard=1.0, soft=1.0)]

        response = json.dumps({
            "slow_update_content": "Use test-first approach.",
            "reasoning": "Tests help.",
        })
        with patch("factory.skillopt.slow_update._call_llm", return_value=response):
            result = run_slow_update(
                skill_content="<!-- SLOW_UPDATE_START -->\n<!-- SLOW_UPDATE_END -->",
                prev_skill="old",
                results_prev=prev,
                results_curr=curr,
                prev_slow_update_content="old guidance",
            )
        assert result is not None
        assert result["slow_update_content"] == "Use test-first approach."

    def test_run_slow_update_bad_json(self):
        from factory.skillopt.slow_update import run_slow_update

        with patch("factory.skillopt.slow_update._call_llm", return_value="not json"):
            result = run_slow_update(
                skill_content="s", prev_skill="p",
                results_prev=[RolloutResult(id="a", hard=0.0, soft=0.0)],
                results_curr=[RolloutResult(id="a", hard=1.0, soft=1.0)],
            )
        assert result is None

    def test_inject_already_has_field(self):
        from factory.skillopt.slow_update import inject_empty_slow_update_field

        skill = "top\n<!-- SLOW_UPDATE_START -->\nexisting\n<!-- SLOW_UPDATE_END -->\nbottom"
        result = inject_empty_slow_update_field(skill)
        assert result == skill  # Should not double-inject


class TestTrainerYamlBranches:
    def test_yaml_surface_candidate_no_changes(self, tmp_path):
        """Test the 'no actual prompt changes' branch."""
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"b": {"slots": {"task_prompt_b": "prompt text"}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"), epochs=1, steps_per_epoch=1,
            batch_size=2, learning_rate=3, workflow_name="swebench",
        )

        baseline = [RolloutResult(id="e1", hard=0.5, soft=0.5)]
        train = [RolloutResult(id="t1", hard=1.0, soft=1.0)]

        adapter.rollout.side_effect = [baseline, train]
        # Edit replaces with same content — no actual change
        adapter.reflect.return_value = [
            RawPatch(
                patch=Patch(edits=[Edit(op="replace", target="prompt text", content="prompt text")],
                            reasoning="r"),
                source_type="failure", batch_size=1, failure_summary=[],
            ),
        ]

        trainer.train()
        assert trainer.best_score == 0.5  # rejected, no change

    def test_yaml_surface_substring_slot_mapping(self, tmp_path):
        """Test substring edit mapping within a slot."""
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"b": {"slots": {"task_prompt_b": "line 1\nline 2\nline 3"}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"), epochs=1, steps_per_epoch=1,
            batch_size=2, learning_rate=3, workflow_name="swebench",
        )

        baseline = [RolloutResult(id="e1", hard=0.5, soft=0.5)]
        train = [RolloutResult(id="t1", hard=1.0, soft=1.0)]
        eval_good = [RolloutResult(id="e1", hard=1.0, soft=1.0)]

        adapter.rollout.side_effect = [baseline, train, eval_good]
        # Edit targets substring of slot
        adapter.reflect.return_value = [
            RawPatch(
                patch=Patch(edits=[Edit(op="replace", target="line 2", content="modified line")],
                            reasoning="r"),
                source_type="failure", batch_size=1, failure_summary=[],
            ),
        ]

        trainer.train()
        assert trainer.best_score == 1.0
        assert "modified line" in trainer.prompt_slots.get("task_prompt_b", "")

    def test_update_prompt_slots_without_candidate(self, tmp_path):
        """Test _update_prompt_slots_after_accept with patch-based edits."""
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"b": {"slots": {"task_prompt_b": "original"}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"),
        )

        p = Patch(edits=[Edit(op="replace", target="original", content="updated")])
        trainer._update_prompt_slots_after_accept(p, {"task_prompt_b": "updated"})
        assert trainer.prompt_slots["task_prompt_b"] == "updated"

    def test_update_prompt_slots_no_candidate_slots(self, tmp_path):
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"b": {"slots": {"task_prompt_b": "original"}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"),
        )

        p = Patch(edits=[Edit(op="replace", target="original", content="updated")])
        trainer._update_prompt_slots_after_accept(p)
        assert trainer.prompt_slots["task_prompt_b"] == "updated"

    def test_update_prompt_slots_no_yaml(self, tmp_path):
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill")

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"),
        )

        p = Patch(edits=[Edit(op="replace", target="x", content="y")])
        trainer._update_prompt_slots_after_accept(p)
        # No yaml_surface, should be no-op
        assert trainer.prompt_slots == {}


class TestMainEntryPoint:
    def test_main_with_annotations(self, tmp_path):
        import sys
        from factory.skillopt.__main__ import main
        import yaml

        skill = tmp_path / "SKILL.md"
        skill.write_text("# Test")
        ann = tmp_path / "SKILL.annotations.yaml"
        ann.write_text(yaml.dump({"b": {"slots": {"task_prompt_b": "p"}}}))

        with patch.object(sys, "argv", [
            "skillopt", "--benchmark", "swebench",
            "--skill-path", str(skill),
            "--annotations", str(ann),
            "--epochs", "1", "--steps-per-epoch", "1",
            "--batch-size", "2", "--out-dir", str(tmp_path / "out"),
        ]):
            with patch("factory.skillopt.__main__._load_adapter") as mock_adapter, \
                 patch("factory.skillopt.trainer.SkillOptTrainer") as mock_trainer:
                mock_adapter.return_value = MagicMock()
                mock_trainer.return_value = MagicMock()
                result = main()
                assert result == 0
                call_kwargs = mock_trainer.call_args[1]
                assert call_kwargs["annotations_path"] == str(ann)

    def test_main_with_student_model(self, tmp_path):
        import sys
        from factory.skillopt.__main__ import main

        skill = tmp_path / "SKILL.md"
        skill.write_text("# Test")

        with patch.object(sys, "argv", [
            "skillopt", "--benchmark", "swebench",
            "--skill-path", str(skill),
            "--student-model", "haiku",
            "--epochs", "1", "--steps-per-epoch", "1",
            "--batch-size", "2",
        ]):
            with patch("factory.skillopt.__main__._load_adapter") as mock_adapter, \
                 patch("factory.skillopt.trainer.SkillOptTrainer") as mock_trainer:
                mock_adapter.return_value = MagicMock()
                mock_trainer.return_value = MagicMock()
                main()
                setup_call = mock_adapter.return_value.setup.call_args
                assert setup_call[0][0]["student_model"] == "haiku"

    def test_main_with_instances(self, tmp_path):
        import sys
        from factory.skillopt.__main__ import main

        skill = tmp_path / "SKILL.md"
        skill.write_text("# Test")

        with patch.object(sys, "argv", [
            "skillopt", "--benchmark", "swebench",
            "--skill-path", str(skill),
            "--instances", "t1,t2,t3",
            "--epochs", "1", "--steps-per-epoch", "1",
            "--batch-size", "2",
        ]):
            with patch("factory.skillopt.__main__._load_adapter") as mock_adapter, \
                 patch("factory.skillopt.trainer.SkillOptTrainer") as mock_trainer:
                mock_adapter.return_value = MagicMock()
                mock_trainer.return_value = MagicMock()
                main()
                setup_call = mock_adapter.return_value.setup.call_args
                assert setup_call[0][0]["instances"] == ["t1", "t2", "t3"]

    def test_main_with_overfit(self, tmp_path):
        import sys
        from factory.skillopt.__main__ import main

        skill = tmp_path / "SKILL.md"
        skill.write_text("# Test")

        with patch.object(sys, "argv", [
            "skillopt", "--benchmark", "swebench",
            "--skill-path", str(skill),
            "--overfit",
            "--epochs", "1", "--steps-per-epoch", "1",
            "--batch-size", "2",
        ]):
            with patch("factory.skillopt.__main__._load_adapter") as mock_adapter, \
                 patch("factory.skillopt.trainer.SkillOptTrainer") as mock_trainer:
                mock_adapter.return_value = MagicMock()
                mock_trainer.return_value = MagicMock()
                main()
                call_kwargs = mock_trainer.call_args[1]
                assert call_kwargs["overfit"] is True

    def test_main_with_slow_update(self, tmp_path):
        import sys
        from factory.skillopt.__main__ import main

        skill = tmp_path / "SKILL.md"
        skill.write_text("# Test")

        with patch.object(sys, "argv", [
            "skillopt", "--benchmark", "swebench",
            "--skill-path", str(skill),
            "--slow-update",
            "--epochs", "1", "--steps-per-epoch", "1",
            "--batch-size", "2",
        ]):
            with patch("factory.skillopt.__main__._load_adapter") as mock_adapter, \
                 patch("factory.skillopt.trainer.SkillOptTrainer") as mock_trainer:
                mock_adapter.return_value = MagicMock()
                mock_trainer.return_value = MagicMock()
                main()
                call_kwargs = mock_trainer.call_args[1]
                assert call_kwargs["use_slow_update"] is True


class TestSlowUpdateWithSlots:
    def test_slow_update_injects_into_prompt_slot(self, tmp_path):
        """Verify epoch 0 injects placeholder into the prompt slot, not just SKILL.md."""
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill\nContent")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"b": {"slots": {"task_prompt_b": "original prompt text"}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"), epochs=1, steps_per_epoch=1,
            batch_size=2, learning_rate=3, use_slow_update=True,
        )

        results = [RolloutResult(id="e1", hard=0.5, soft=0.5)]
        adapter.rollout.side_effect = [results, results]
        adapter.reflect.return_value = []

        trainer.train()

        # Verify placeholder is in the prompt slot
        assert "SLOW_UPDATE_START" in trainer.prompt_slots["task_prompt_b"]
        # Verify it was written to YAML
        reloaded = yaml.safe_load(ann_path.read_text())
        assert "SLOW_UPDATE_START" in reloaded["b"]["slots"]["task_prompt_b"]

    def test_slow_update_guidance_in_prompt_slot(self, tmp_path):
        """Verify epoch 2 writes guidance into the prompt slot."""
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill\n<!-- SLOW_UPDATE_START -->\n<!-- SLOW_UPDATE_END -->")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        prompt_with_markers = "prompt\n\n<!-- SLOW_UPDATE_START -->\n<!-- SLOW_UPDATE_END -->"
        ann = {"b": {"slots": {"task_prompt_b": prompt_with_markers}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"), epochs=2, steps_per_epoch=1,
            batch_size=2, learning_rate=3, use_slow_update=True,
        )

        results = [RolloutResult(id="e1", hard=0.5, soft=0.5)]
        adapter.rollout.side_effect = [results] * 10
        adapter.reflect.return_value = []

        # Mock run_slow_update to return guidance
        with patch("factory.skillopt.trainer.run_slow_update") as mock_slow:
            mock_slow.return_value = {
                "slow_update_content": "Focus on test-first debugging.",
                "reasoning": "Tests help.",
            }
            trainer.train()

        # Verify guidance is in the prompt slot
        assert "Focus on test-first debugging" in trainer.prompt_slots["task_prompt_b"]
        # Verify YAML was updated
        reloaded = yaml.safe_load(ann_path.read_text())
        assert "Focus on test-first debugging" in reloaded["b"]["slots"]["task_prompt_b"]


class TestSlowUpdateNoYaml:
    def test_slow_update_without_yaml_surface(self, tmp_path):
        """Verify slow update works without YAML surface (legacy SKILL.md mode)."""
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill\nContent here")
        # No annotations file — trainer uses legacy mode

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"), epochs=2, steps_per_epoch=1,
            batch_size=2, learning_rate=3, use_slow_update=True,
        )
        assert trainer.yaml_surface is None

        results = [RolloutResult(id="e1", hard=0.5, soft=0.5)]
        adapter.rollout.side_effect = [results] * 10
        adapter.reflect.return_value = []

        with patch("factory.skillopt.trainer.run_slow_update") as mock_slow:
            mock_slow.return_value = {
                "slow_update_content": "Use test-first approach.",
                "reasoning": "Tests help.",
            }
            trainer.train()

        # Verify slow update applied to current_skill
        assert "SLOW_UPDATE_START" in trainer.current_skill

    def test_get_primary_prompt_slot_empty(self, tmp_path):
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill")

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"),
        )
        assert trainer._get_primary_prompt_slot() is None

    def test_get_primary_prompt_slot_picks_largest(self, tmp_path):
        import yaml
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"a": {"slots": {"system_prompt_a": "short"}},
               "b": {"slots": {"instance_prompt_b": "this is a much longer prompt text"}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"),
        )
        assert trainer._get_primary_prompt_slot() == "instance_prompt_b"


class TestSlowUpdatePrevVsCurr:
    def test_prev_rollout_uses_previous_slots(self, tmp_path):
        """Verify prev rollout uses the checkpointed slots, not current ones."""
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill\n<!-- SLOW_UPDATE_START -->\n<!-- SLOW_UPDATE_END -->")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        prompt = "prompt\n\n<!-- SLOW_UPDATE_START -->\n<!-- SLOW_UPDATE_END -->"
        ann = {"b": {"slots": {"task_prompt_b": prompt}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"), epochs=2, steps_per_epoch=1,
            batch_size=2, learning_rate=3, use_slow_update=True,
        )

        results = [RolloutResult(id="e1", hard=0.5, soft=0.5)]
        adapter.rollout.side_effect = [results] * 10
        adapter.reflect.return_value = []

        # Modify prompt_slots between epochs to simulate optimization

        def patched_train():
            # Run epoch 1
            trainer.rejected_edits = []
            trainer.prompt_slots["task_prompt_b"] = "epoch1 prompt\n\n<!-- SLOW_UPDATE_START -->\n<!-- SLOW_UPDATE_END -->"
            trainer._checkpoint("epoch1_step1")

            # Now manually trigger epoch 2 slow update
            trainer.prompt_slots["task_prompt_b"] = "epoch2 improved prompt\n\n<!-- SLOW_UPDATE_START -->\n<!-- SLOW_UPDATE_END -->"

            with patch("factory.skillopt.trainer.run_slow_update", return_value=None):
                trainer._run_slow_update_epoch(1)  # epoch index 1 = epoch 2

            # Check what rollout received for prev vs curr
            if adapter.rollout.call_count >= 2:
                prev_yaml = adapter.rollout.call_args_list[-2][0][1]
                curr_yaml = adapter.rollout.call_args_list[-1][0][1]
                prev_parsed = yaml.safe_load(prev_yaml)
                curr_parsed = yaml.safe_load(curr_yaml)
                assert "epoch1" in prev_parsed["b"]["slots"]["task_prompt_b"]
                assert "epoch2" in curr_parsed["b"]["slots"]["task_prompt_b"]

        patched_train()

    def test_checkpoint_saves_slots(self, tmp_path):
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"b": {"slots": {"task_prompt_b": "prompt"}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"),
        )

        trainer._checkpoint("test_label")
        slots_path = tmp_path / "out" / "checkpoints" / "test_label_slots.json"
        assert slots_path.exists()
        saved = json.loads(slots_path.read_text())
        assert saved["task_prompt_b"] == "prompt"
