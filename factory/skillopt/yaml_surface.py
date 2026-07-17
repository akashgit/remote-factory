"""YAML annotation surface for SkillOpt — prompt slots as the optimization target."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class SlotEdit(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    node_id: str
    slot_name: str
    new_value: str
    rationale: str = ""


class SlotPatch(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    edits: list[SlotEdit]
    reasoning: str = ""


def load_yaml(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


def extract_prompt_slots(surface: dict) -> dict[str, str]:
    """Extract {slot_name: value} for all task_prompt_* slots across all nodes."""
    slots: dict[str, str] = {}
    for node_id, node in surface.items():
        if not isinstance(node, dict):
            continue
        for k, v in node.get("slots", {}).items():
            if k.startswith("task_prompt_"):
                slots[k] = v
    return slots


def validate_only_prompts_changed(original: dict, proposed: dict) -> list[str]:
    """Return violations if anything other than task_prompt_* slots changed."""
    violations: list[str] = []
    if set(original.keys()) != set(proposed.keys()):
        violations.append(f"Node IDs changed: {set(original.keys())} vs {set(proposed.keys())}")
        return violations
    for node_id in original:
        orig = original[node_id]
        prop = proposed[node_id]
        if not isinstance(orig, dict) or not isinstance(prop, dict):
            if orig != prop:
                violations.append(f"{node_id} changed (non-dict node)")
            continue
        for field in ("type", "id", "edges_out", "reads", "writes"):
            if orig.get(field) != prop.get(field):
                violations.append(f"{node_id}.{field} changed")
        orig_slots = orig.get("slots", {})
        prop_slots = prop.get("slots", {})
        for k in set(orig_slots) | set(prop_slots):
            if not k.startswith("task_prompt_"):
                if orig_slots.get(k) != prop_slots.get(k):
                    violations.append(f"{node_id}.slots.{k} changed (not a prompt slot)")
        for field in ("evaluator_command", "command", "evaluator_type", "role", "blocking"):
            if orig.get(field) != prop.get(field):
                violations.append(f"{node_id}.{field} changed")
    return violations


def apply_slot_edits(surface: dict, edits: list[SlotEdit]) -> dict:
    """Apply prompt slot edits to the YAML surface. Returns a deep copy with updates."""
    updated = copy.deepcopy(surface)
    for edit in edits:
        node = updated.get(edit.node_id)
        if node and isinstance(node, dict) and "slots" in node and edit.slot_name in node["slots"]:
            node["slots"][edit.slot_name] = edit.new_value
    return updated


def render_skill_from_slots(
    workflow_name: str,
    prompt_slots: dict[str, str],
    skill_path: str | Path,
) -> str:
    """Re-render SKILL.md by loading the workflow, overriding prompt_template slots, and running the renderer."""
    from factory.workflow.definitions import register_all
    from factory.workflow.skill_export import workflow_to_skill_md
    from factory.workflow.splitter import split_skill

    workflows = register_all()
    wf = workflows.get(workflow_name)
    if not wf:
        raise ValueError(f"Unknown workflow: {workflow_name}")

    from factory.workflow.primitives import AgentNode

    for slot_name, slot_value in prompt_slots.items():
        node_id = slot_name.replace("task_prompt_", "")
        node = wf.nodes.get(node_id)
        if isinstance(node, AgentNode):
            wf.nodes[node_id] = node.model_copy(update={"prompt_template": slot_value})

    templatized = workflow_to_skill_md(wf)
    clean_md, _ = split_skill(templatized)

    Path(skill_path).write_text(clean_md)
    return clean_md


def format_prompt_slots_for_llm(surface: dict) -> str:
    """Format prompt slots as readable text for the LLM analyst."""
    sections: list[str] = []
    for node_id, node in surface.items():
        if not isinstance(node, dict):
            continue
        slots = node.get("slots", {})
        prompt_slots = {k: v for k, v in slots.items() if k.startswith("task_prompt_")}
        if not prompt_slots:
            continue
        for slot_name, slot_value in prompt_slots.items():
            sections.append(
                f"--- node_id: {node_id} | slot_name: {slot_name} ---\n{slot_value}"
            )
    return "\n\n".join(sections)
