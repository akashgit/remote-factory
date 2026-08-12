"""Tests for the SkillOpt training loop components."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

from factory.skillopt.adapter import EnvAdapter
from factory.skillopt.gate import evaluate_gate, select_gate_score
from factory.skillopt.skill import apply_patch
from factory.skillopt.types import (
    Edit,
    FailureSummaryEntry,
    GateResult,
    Patch,
    RawPatch,
    RolloutResult,
)
from factory.skillopt.failure_tracker import (
    FailureMode,
    FailureTracker,
    classify_failure,
)
from factory.skillopt.yaml_surface import (
    SlotEdit,
    apply_slot_edits,
    compute_prompt_change_magnitude,
    extract_prompt_slots,
    format_prompt_slots_for_llm,
    render_skill_from_slots,
    validate_only_prompts_changed,
    yaml_to_workflow,
)
from factory.skillopt.reflect import fmt_minibatch_trajectories, fmt_trajectory


# ── gate ────────────────────────────────────────────────────────


class TestGate:
    def test_select_gate_score_hard(self):
        assert select_gate_score(0.8, 0.9, "hard") == 0.8

    def test_select_gate_score_soft(self):
        assert select_gate_score(0.8, 0.9, "soft") == 0.9

    def test_select_gate_score_mixed(self):
        score = select_gate_score(0.8, 0.6, "mixed")
        assert 0.6 < score < 0.8

    def test_evaluate_gate_accept_new_best(self):
        result = evaluate_gate(
            candidate_skill="new", cand_hard=0.9, cand_soft=0.9,
            current_skill="old", current_score=0.8,
            best_skill="old", best_score=0.8, best_step=0,
            global_step=1, metric="hard",
        )
        assert result.action == "accept_new_best"
        assert result.current_score == 0.9
        assert result.best_score == 0.9
        assert result.best_step == 1

    def test_evaluate_gate_accept_not_best(self):
        result = evaluate_gate(
            candidate_skill="new", cand_hard=0.85, cand_soft=0.85,
            current_skill="old", current_score=0.8,
            best_skill="best", best_score=0.9, best_step=0,
            global_step=1, metric="hard",
        )
        assert result.action == "accept"
        assert result.best_score == 0.9

    def test_evaluate_gate_reject_tie(self):
        result = evaluate_gate(
            candidate_skill="new", cand_hard=0.8, cand_soft=0.8,
            current_skill="old", current_score=0.8,
            best_skill="old", best_score=0.8, best_step=0,
            global_step=1, metric="hard",
        )
        assert result.action == "reject"

    def test_evaluate_gate_reject_worse(self):
        result = evaluate_gate(
            candidate_skill="new", cand_hard=0.5, cand_soft=0.5,
            current_skill="old", current_score=0.8,
            best_skill="old", best_score=0.8, best_step=0,
            global_step=1, metric="hard",
        )
        assert result.action == "reject"

    def test_evaluate_gate_accept_ties_in_overfit(self):
        result = evaluate_gate(
            candidate_skill="new", cand_hard=0.8, cand_soft=0.8,
            current_skill="old", current_score=0.8,
            best_skill="old", best_score=0.8, best_step=0,
            global_step=1, metric="hard", accept_ties=True,
        )
        assert result.action == "accept"


# ── skill edits ─────────────────────────────────────────────────


class TestSkillEdits:
    def test_apply_patch_replace(self):
        result = apply_patch("A\nB\nC", Patch(edits=[Edit(op="replace", target="B", content="X")]))
        assert "X" in result and "B" not in result

    def test_apply_patch_append(self):
        result = apply_patch("A", Patch(edits=[Edit(op="append", content="Z")]))
        assert "Z" in result

    def test_apply_patch_delete(self):
        result = apply_patch("A\nB\nC", Patch(edits=[Edit(op="delete", target="B")]))
        assert "B" not in result

    def test_apply_patch_insert_after(self):
        result = apply_patch("A\nB\nC", Patch(edits=[Edit(op="insert_after", target="A", content="X")]))
        lines = result.split("\n")
        assert lines.index("X") == lines.index("A") + 1

    def test_apply_patch_no_edits(self):
        assert apply_patch("unchanged", Patch(edits=[])) == "unchanged"

    def test_apply_patch_replace_missing_target(self):
        assert apply_patch("A", Patch(edits=[Edit(op="replace", target="Z", content="X")])) == "A"

    def test_apply_patch_multiple_edits(self):
        result = apply_patch(
            "A\nB\nC",
            Patch(edits=[
                Edit(op="replace", target="A", content="X"),
                Edit(op="delete", target="C"),
            ]),
        )
        assert "X" in result and "A" not in result and "C" not in result

    def test_apply_patch_protected_region(self):
        skill = "top\n<!-- SLOW_UPDATE_START -->\nprotected\n<!-- SLOW_UPDATE_END -->\nbottom"
        result = apply_patch(skill, Patch(edits=[Edit(op="delete", target="protected")]))
        assert "protected" in result


# ── failure tracker ─────────────────────────────────────────────


class TestFailureTracker:
    def test_classify_success(self):
        assert classify_failure(RolloutResult(id="t1", hard=1.0, soft=1.0)) == ""

    def test_classify_empty_trace(self):
        assert classify_failure(RolloutResult(id="t1", hard=0.0, soft=0.0)) == FailureMode.EMPTY_TRACE

    def test_classify_timeout_fail_reason(self):
        r = RolloutResult(id="t1", hard=0.0, soft=0.0, fail_reason="timeout expired",
                          extras={"trace_dump": "[bash] x"})
        assert classify_failure(r) == FailureMode.TIMEOUT

    def test_classify_timeout_in_trace(self):
        r = RolloutResult(id="t1", hard=0.0, soft=0.0,
                          extras={"trace_dump": "[assistant] timed out waiting"})
        assert classify_failure(r) == FailureMode.TIMEOUT

    def test_classify_build_error(self):
        r = RolloutResult(id="t1", hard=0.0, soft=0.0,
                          extras={"trace_dump": "[bash] python\n[output] ImportError: no module"})
        assert classify_failure(r) == FailureMode.BUILD_ERROR

    def test_classify_no_change(self):
        r = RolloutResult(id="t1", hard=0.0, soft=0.0,
                          extras={"trace_dump": "[assistant] thinking\n[output] data"})
        assert classify_failure(r) == FailureMode.NO_CHANGE

    def test_classify_wrong_patch(self):
        r = RolloutResult(id="t1", hard=0.0, soft=0.0, fail_reason="tests failed",
                          extras={"trace_dump": "[edit] f.py"})
        assert classify_failure(r) == FailureMode.WRONG_PATCH

    def test_classify_test_regression(self):
        r = RolloutResult(id="t1", hard=0.0, soft=0.0,
                          extras={"trace_dump": "[edit] f.py\n[VERIFIER TEST RESULTS]\n3 passed 2 FAILED"})
        assert classify_failure(r) == FailureMode.TEST_REGRESSION

    def test_classify_with_write(self):
        r = RolloutResult(id="t1", hard=0.0, soft=0.0,
                          extras={"trace_dump": "[write] f.py"})
        assert classify_failure(r) == FailureMode.WRONG_PATCH

    def test_classify_fail_reason_only(self):
        r = RolloutResult(id="t1", hard=0.0, soft=0.0, fail_reason="something broke")
        assert classify_failure(r) == FailureMode.NO_CHANGE

    def test_tracker_record_and_summary(self, tmp_path):
        tracker = FailureTracker(str(tmp_path))
        tracker.record_rollout(
            [RolloutResult(id="t1", hard=1.0, soft=1.0),
             RolloutResult(id="t2", hard=0.0, soft=0.0, fail_reason="timeout")],
            1, "train",
        )
        s = tracker.summary()
        assert s["total_failures"] == 1
        assert s["by_mode"]["timeout"] == 1

    def test_tracker_persistence(self, tmp_path):
        t1 = FailureTracker(str(tmp_path))
        t1.record_rollout([RolloutResult(id="x", hard=0.0, soft=0.0)], 1, "train")
        t2 = FailureTracker(str(tmp_path))
        assert len(t2.entries) == 1

    def test_tracker_always_fail(self, tmp_path):
        tracker = FailureTracker(str(tmp_path))
        for step in range(3):
            tracker.record_rollout([RolloutResult(id="bad", hard=0.0, soft=0.0)], step, "eval")
        assert "bad" in tracker.summary()["always_fail_top"]

    def test_tracker_by_phase(self, tmp_path):
        tracker = FailureTracker(str(tmp_path))
        tracker.record_rollout([RolloutResult(id="a", hard=0.0, soft=0.0)], 1, "train")
        tracker.record_rollout([RolloutResult(id="b", hard=0.0, soft=0.0)], 1, "eval")
        s = tracker.summary()
        assert "train" in s["by_phase"]
        assert "eval" in s["by_phase"]

    def test_tracker_print_summary(self, tmp_path, capsys):
        tracker = FailureTracker(str(tmp_path))
        tracker.record_rollout([RolloutResult(id="x", hard=0.0, soft=0.0)], 1, "train")
        tracker.print_summary()
        captured = capsys.readouterr()
        assert "Failure Tracker Summary" in captured.out


# ── yaml surface ───────────────────────────────────────────────


class TestYamlSurface:
    def test_extract_task_prompt(self):
        surface = {"b": {"slots": {"task_prompt_b": "prompt"}}}
        assert extract_prompt_slots(surface) == {"task_prompt_b": "prompt"}

    def test_extract_system_and_instance(self):
        surface = {"s": {"slots": {"system_prompt_s": "sys", "instance_prompt_s": "inst"}}}
        slots = extract_prompt_slots(surface)
        assert "system_prompt_s" in slots and "instance_prompt_s" in slots

    def test_extract_ignores_non_prompt(self):
        surface = {"n": {"slots": {"timeout_n": "600", "task_prompt_n": "p"}}}
        assert "timeout_n" not in extract_prompt_slots(surface)

    def test_extract_skips_non_dict(self):
        assert len(extract_prompt_slots({"meta": "str", "n": {"slots": {"task_prompt_n": "p"}}})) == 1

    def test_format_includes_prompts_only(self):
        surface = {"s": {"slots": {"system_prompt_s": "sys", "timeout_s": "600"}}}
        text = format_prompt_slots_for_llm(surface)
        assert "system_prompt_s" in text and "timeout_s" not in text

    def test_format_empty(self):
        assert format_prompt_slots_for_llm({}) == ""

    def test_format_multiple_nodes(self):
        surface = {
            "a": {"slots": {"task_prompt_a": "pa"}},
            "b": {"slots": {"task_prompt_b": "pb"}},
        }
        text = format_prompt_slots_for_llm(surface)
        assert "task_prompt_a" in text and "task_prompt_b" in text

    def test_validate_no_changes(self):
        s = {"n": {"type": "X", "slots": {"task_prompt_n": "v"}}}
        assert validate_only_prompts_changed(s, s) == []

    def test_validate_prompt_change_ok(self):
        o = {"n": {"type": "X", "slots": {"task_prompt_n": "old"}}}
        p = {"n": {"type": "X", "slots": {"task_prompt_n": "new"}}}
        assert validate_only_prompts_changed(o, p) == []

    def test_validate_system_prompt_change_ok(self):
        o = {"n": {"type": "X", "slots": {"system_prompt_n": "old"}}}
        p = {"n": {"type": "X", "slots": {"system_prompt_n": "new"}}}
        assert validate_only_prompts_changed(o, p) == []

    def test_validate_instance_prompt_change_ok(self):
        o = {"n": {"type": "X", "slots": {"instance_prompt_n": "old"}}}
        p = {"n": {"type": "X", "slots": {"instance_prompt_n": "new"}}}
        assert validate_only_prompts_changed(o, p) == []

    def test_validate_non_prompt_rejected(self):
        o = {"n": {"type": "X", "slots": {"timeout_n": "1"}}}
        p = {"n": {"type": "X", "slots": {"timeout_n": "2"}}}
        assert len(validate_only_prompts_changed(o, p)) == 1

    def test_validate_structural_change(self):
        o = {"n": {"type": "X", "id": "a", "slots": {}}}
        p = {"n": {"type": "X", "id": "b", "slots": {}}}
        assert len(validate_only_prompts_changed(o, p)) >= 1

    def test_validate_node_count_change(self):
        o = {"n1": {"type": "X", "slots": {}}}
        p = {"n1": {"type": "X", "slots": {}}, "n2": {"type": "Y", "slots": {}}}
        assert len(validate_only_prompts_changed(o, p)) >= 1

    def test_validate_non_dict_node(self):
        o = {"m": "string"}
        p = {"m": "different"}
        assert len(validate_only_prompts_changed(o, p)) >= 1

    def test_validate_non_dict_unchanged(self):
        o = {"m": "same"}
        assert validate_only_prompts_changed(o, o) == []

    def test_validate_field_changes(self):
        o = {"n": {"type": "X", "command": "a", "slots": {}}}
        p = {"n": {"type": "X", "command": "b", "slots": {}}}
        assert len(validate_only_prompts_changed(o, p)) >= 1

    def test_apply_slot_edits(self):
        surface = {"b": {"slots": {"task_prompt_b": "old"}}}
        edits = [SlotEdit(node_id="b", slot_name="task_prompt_b", new_value="new")]
        result = apply_slot_edits(surface, edits)
        assert result["b"]["slots"]["task_prompt_b"] == "new"
        assert surface["b"]["slots"]["task_prompt_b"] == "old"

    def test_apply_slot_edits_missing_node(self):
        surface = {"b": {"slots": {"task_prompt_b": "old"}}}
        edits = [SlotEdit(node_id="missing", slot_name="x", new_value="y")]
        result = apply_slot_edits(surface, edits)
        assert result == surface

    def test_compute_magnitude_zero(self):
        assert compute_prompt_change_magnitude("same", "same") == 0

    def test_compute_magnitude_change(self):
        assert compute_prompt_change_magnitude("a\nb\nc", "a\nX\nc") == 2

    def test_render_skill_from_slots_swebench(self):
        from factory.workflow.definitions import register_all
        wf = register_all()
        if "swebench" not in wf:
            return
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w") as f:
            f.write("")
            path = f.name
        try:
            slots = {"task_prompt_builder": "test prompt"}
            result = render_skill_from_slots("swebench", slots, path)
            assert "test prompt" in result
        finally:
            os.unlink(path)

    def test_yaml_to_workflow_swebench(self):
        from factory.workflow.definitions import register_all
        wf = register_all()
        if "swebench" not in wf:
            return
        # Create a temp YAML with modified slots
        surface = {
            "builder": {"type": "AgentNode", "id": "builder",
                        "slots": {"task_prompt_builder": "modified prompt"}},
        }
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            yaml.dump(surface, f)
            path = f.name
        try:
            wf2 = yaml_to_workflow(path, "swebench")
            assert wf2.nodes["builder"].prompt_template == "modified prompt"
        finally:
            os.unlink(path)

    def test_yaml_to_workflow_llmnode(self):
        from factory.workflow.definitions import register_all
        wf = register_all()
        if "mini-swebench" not in wf:
            return
        surface = {
            "solver": {"type": "LLMNode", "id": "solver",
                       "slots": {"instance_prompt_solver": "new inst",
                                 "system_prompt_solver": "new sys"}},
        }
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            yaml.dump(surface, f)
            path = f.name
        try:
            wf = yaml_to_workflow(path, "mini-swebench")
            assert wf.nodes["solver"].instance_prompt == "new inst"
            assert wf.nodes["solver"].system_prompt == "new sys"
        finally:
            os.unlink(path)

    def test_yaml_to_workflow_with_base(self):
        from factory.workflow.definitions import register_all
        wf_orig = register_all().get("swebench")
        if not wf_orig:
            return
        surface = {
            "builder": {"slots": {"task_prompt_builder": "override"}},
        }
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            yaml.dump(surface, f)
            path = f.name
        try:
            wf = yaml_to_workflow(path, "swebench", workflow=wf_orig)
            assert wf.nodes["builder"].prompt_template == "override"
        finally:
            os.unlink(path)

    def test_yaml_to_workflow_unknown_raises(self):
        surface = {"n": {"slots": {}}}
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            yaml.dump(surface, f)
            path = f.name
        try:
            import pytest
            with pytest.raises(ValueError, match="Unknown workflow"):
                yaml_to_workflow(path, "nonexistent-workflow-xyz")
        finally:
            os.unlink(path)

    def test_yaml_to_workflow_timeout_override(self):
        from factory.workflow.definitions import register_all
        if "swebench" not in register_all():
            return
        surface = {"builder": {"slots": {"timeout_builder": "9999"}}}
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            yaml.dump(surface, f)
            path = f.name
        try:
            wf = yaml_to_workflow(path, "swebench")
            assert wf.nodes["builder"].timeout == 9999
        finally:
            os.unlink(path)

    def test_yaml_to_workflow_max_turns_override(self):
        from factory.workflow.definitions import register_all
        if "mini-swebench" not in register_all():
            return
        surface = {"solver": {"slots": {"max_turns_solver": "200"}}}
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            yaml.dump(surface, f)
            path = f.name
        try:
            wf = yaml_to_workflow(path, "mini-swebench")
            assert wf.nodes["solver"].max_turns == 200
        finally:
            os.unlink(path)

    def test_render_skill_llmnode(self):
        from factory.workflow.definitions import register_all
        if "mini-swebench" not in register_all():
            return
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w") as f:
            f.write("")
            path = f.name
        try:
            slots = {"instance_prompt_solver": "test instance prompt"}
            result = render_skill_from_slots("mini-swebench", slots, path)
            assert "test instance prompt" in result
        finally:
            os.unlink(path)


# ── reflect formatting ──────────────────────────────────────────


class TestReflectFormatting:
    def test_fmt_basic(self):
        items = [RolloutResult(id="t", hard=0.0, soft=0.0, fail_reason="fail",
                               extras={"trace_dump": "[bash] ls\n[output] f.py"})]
        text = fmt_minibatch_trajectories(items)
        assert "t" in text and "fail" in text and "[bash] ls" in text

    def test_fmt_no_truncation(self):
        trace = "x" * 50000
        items = [RolloutResult(id="t", hard=0.0, soft=0.0, extras={"trace_dump": trace})]
        assert len(fmt_minibatch_trajectories(items)) > 50000

    def test_fmt_extras_surfaced(self):
        items = [RolloutResult(id="t", hard=1.0, soft=1.0,
                               extras={"prediction": "42", "gold_answers": ["42"]})]
        text = fmt_minibatch_trajectories(items)
        assert "prediction" in text and "gold_answers" in text

    def test_fmt_multiple(self):
        items = [RolloutResult(id="a", hard=0.0, soft=0.0, extras={"trace_dump": "ta"}),
                 RolloutResult(id="b", hard=1.0, soft=1.0, extras={"trace_dump": "tb"})]
        text = fmt_minibatch_trajectories(items)
        assert "1/2" in text and "2/2" in text

    def test_fmt_no_trace(self):
        items = [RolloutResult(id="t", hard=0.0, soft=0.0)]
        assert "no trace data" in fmt_minibatch_trajectories(items)

    def test_fmt_trajectory(self):
        text = fmt_trajectory({"id": "x", "fail_reason": "broke", "trace_dump": "details"})
        assert "x" in text and "broke" in text and "details" in text

    def test_fmt_trajectory_no_fail(self):
        text = fmt_trajectory({"id": "x"})
        assert "x" in text


# ── types ───────────────────────────────────────────────────────


class TestTypes:
    def test_rollout_result_defaults(self):
        r = RolloutResult(id="x", hard=0.5, soft=0.5)
        assert r.n_turns == 0 and r.fail_reason == "" and r.extras == {}

    def test_edit_defaults(self):
        e = Edit(op="append", content="text")
        assert e.target == "" and e.support_count is None

    def test_patch_model(self):
        p = Patch(edits=[Edit(op="append", content="x")], reasoning="r")
        assert len(p.edits) == 1 and "edits" in p.model_dump()

    def test_raw_patch(self):
        rp = RawPatch(
            patch=Patch(edits=[], reasoning=""),
            source_type="failure", batch_size=4,
            failure_summary=[FailureSummaryEntry(failure_type="rule_missing", count=2, description="d")],
        )
        assert rp.failure_summary[0].count == 2

    def test_gate_result(self):
        gr = GateResult(action="accept", current_skill="s", current_score=0.8,
                        best_skill="s", best_score=0.8, best_step=1)
        assert gr.action == "accept"


# ── adapter base ────────────────────────────────────────────────


class TestAdapterBase:
    def test_reflect_delegates_to_run_minibatch_reflect(self):
        class DummyAdapter(EnvAdapter):
            def build_train_env(self, batch_size, seed):
                return []
            def build_eval_env(self, env_num, split, seed):
                return []
            def rollout(self, env_manager, skill_content, out_dir):
                return []
            def get_task_types(self):
                return ["test"]

        adapter = DummyAdapter()
        with patch("factory.skillopt.reflect.run_minibatch_reflect", return_value=[]) as mock:
            result = adapter.reflect([], "skill", "/tmp", minibatch_size=2)
            mock.assert_called_once()
            assert result == []

    def test_reflect_passes_prompt_names(self):
        class DummyAdapter(EnvAdapter):
            def build_train_env(self, batch_size, seed):
                return []
            def build_eval_env(self, env_num, split, seed):
                return []
            def rollout(self, env_manager, skill_content, out_dir):
                return []
            def get_task_types(self):
                return ["test"]

        adapter = DummyAdapter()
        with patch("factory.skillopt.reflect.run_minibatch_reflect", return_value=[]) as mock:
            adapter.reflect([], "skill", "/tmp",
                            error_prompt_name="custom_error.md",
                            success_prompt_name="custom_success.md")
            call_kwargs = mock.call_args
            assert call_kwargs[1]["error_prompt_name"] == "custom_error.md"
            assert call_kwargs[1]["success_prompt_name"] == "custom_success.md"


# ── executor _run_llm ──────────────────────────────────────────


class TestExecutorRunLlm:
    def test_run_llm_node_dispatched(self):
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import DEFAULT_AGENT_POOL, LLMNode, Workflow
        from factory.workflow.llm_tools import BASH_TOOL

        wf = Workflow(
            name="test",
            nodes={"s": LLMNode(id="s", system_prompt="", instance_prompt="",
                                tools=[BASH_TOOL], timeout=5)},
            edges=[], start_node="s", terminal=True,
        )
        executor = WorkflowExecutor(wf, Path("/tmp"), agent_pool=DEFAULT_AGENT_POOL, dry_run=True)
        result = asyncio.run(executor.execute())
        assert result.success
        assert result.nodes_executed == 1


# ── cli --from-yaml ──────────────────────────────────────────


class TestCliFromYaml:
    def test_from_yaml_flag_registered(self):
        import argparse
        from factory.workflow.cli import add_workflow_parser
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        add_workflow_parser(sub)
        args = parser.parse_args(["workflow", "run", "swebench", "/tmp", "--from-yaml", "/tmp/x.yaml"])
        assert args.from_yaml == "/tmp/x.yaml"


class TestYamlSurfaceMoreBranches:
    def test_yaml_to_workflow_gate_prompt(self):
        from factory.workflow.definitions import register_all
        if "swebench" not in register_all():
            return
        import tempfile
        surface = {"gate_verify": {"slots": {"gate_prompt_gate_verify": "custom gate"}}}
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            yaml.dump(surface, f)
            path = f.name
        try:
            yaml_to_workflow(path, "swebench")
            # gate_verify might not have gate_prompt field if it's fn type
        finally:
            os.unlink(path)

    def test_yaml_to_workflow_max_iterations(self):
        from factory.workflow.definitions import register_all
        if "swebench" not in register_all():
            return
        import tempfile
        surface = {"builder": {"slots": {"max_iterations_builder": "5"}}}
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            yaml.dump(surface, f)
            path = f.name
        try:
            wf = yaml_to_workflow(path, "swebench")
            assert wf.nodes["builder"].max_iterations == 5
        finally:
            os.unlink(path)

    def test_yaml_to_workflow_no_slots(self):
        import tempfile
        surface = {"builder": {"type": "AgentNode"}}
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            yaml.dump(surface, f)
            path = f.name
        try:
            yaml_to_workflow(path, "swebench")
            # Should not crash — just skip nodes without slots
        finally:
            os.unlink(path)

    def test_yaml_to_workflow_non_dict_node(self):
        import tempfile
        surface = {"metadata": "just a string", "builder": {"slots": {"task_prompt_builder": "p"}}}
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            yaml.dump(surface, f)
            path = f.name
        try:
            yaml_to_workflow(path, "swebench")
        finally:
            os.unlink(path)

    def test_render_skill_unknown_workflow(self):
        import pytest
        with pytest.raises(ValueError):
            render_skill_from_slots("nonexistent-xyz", {}, "/tmp/x.md")

    def test_validate_edges_change(self):
        orig = {"n": {"type": "X", "edges_out": [{"target": "a"}], "slots": {}}}
        prop = {"n": {"type": "X", "edges_out": [{"target": "b"}], "slots": {}}}
        violations = validate_only_prompts_changed(orig, prop)
        assert len(violations) >= 1

    def test_validate_type_change(self):
        orig = {"n": {"type": "A", "slots": {}}}
        prop = {"n": {"type": "B", "slots": {}}}
        violations = validate_only_prompts_changed(orig, prop)
        assert len(violations) >= 1

    def test_validate_reads_change(self):
        orig = {"n": {"type": "X", "reads": ["a.md"], "slots": {}}}
        prop = {"n": {"type": "X", "reads": ["b.md"], "slots": {}}}
        violations = validate_only_prompts_changed(orig, prop)
        assert len(violations) >= 1

    def test_validate_writes_change(self):
        orig = {"n": {"type": "X", "writes": ["a.md"], "slots": {}}}
        prop = {"n": {"type": "X", "writes": ["b.md"], "slots": {}}}
        violations = validate_only_prompts_changed(orig, prop)
        assert len(violations) >= 1

    def test_validate_evaluator_command_change(self):
        orig = {"n": {"type": "X", "evaluator_command": "a", "slots": {}}}
        prop = {"n": {"type": "X", "evaluator_command": "b", "slots": {}}}
        violations = validate_only_prompts_changed(orig, prop)
        assert len(violations) >= 1

    def test_validate_evaluator_type_change(self):
        orig = {"n": {"type": "X", "evaluator_type": "fn", "slots": {}}}
        prop = {"n": {"type": "X", "evaluator_type": "agent", "slots": {}}}
        violations = validate_only_prompts_changed(orig, prop)
        assert len(violations) >= 1

    def test_validate_role_change(self):
        orig = {"n": {"type": "X", "role": "builder", "slots": {}}}
        prop = {"n": {"type": "X", "role": "researcher", "slots": {}}}
        violations = validate_only_prompts_changed(orig, prop)
        assert len(violations) >= 1

    def test_validate_blocking_change(self):
        orig = {"n": {"type": "X", "blocking": True, "slots": {}}}
        prop = {"n": {"type": "X", "blocking": False, "slots": {}}}
        violations = validate_only_prompts_changed(orig, prop)
        assert len(violations) >= 1
