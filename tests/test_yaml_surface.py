"""Tests for the YAML annotation surface (prompt slots as optimization target)."""
from __future__ import annotations

import os
import tempfile

import yaml

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
