"""Splitter for verified skill generation — produces SKILL.md + annotations YAML.

Input: validated refined markdown (guard-approved templatized skill).

Output:
- SKILL.md: annotations stripped, {{slot::value}} resolved to bare values
- SKILL.annotations.yaml: structured metadata per node keyed by node ID
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from factory.workflow.templates import extract, resolve

_ANNOTATION_PATTERN = re.compile(r"<!--\s*(.*?)\s*-->", re.DOTALL)


def split_skill(templatized: str) -> tuple[str, dict[str, Any]]:
    """Split templatized markdown into clean prose and annotations.

    Returns (clean_skill_md, annotations_dict).
    """
    annotations = extract_annotations(templatized)
    slots = dict(extract(templatized))
    for node_id, meta in annotations.items():
        node_slots = {k: v for k, v in slots.items() if k.endswith(f"_{node_id}")}
        if node_slots:
            meta["slots"] = node_slots

    clean = resolve_to_clean(templatized)

    return clean, annotations


def resolve_to_clean(templatized: str) -> str:
    """Strip annotation comments and resolve slot markers to bare values."""
    lines = templatized.split("\n")
    clean_lines: list[str] = []
    prev_blank = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        is_blank = stripped == ""
        if is_blank and prev_blank:
            continue
        clean_lines.append(line)
        prev_blank = is_blank

    text = "\n".join(clean_lines)
    resolved = resolve(text)
    while "\n\n\n" in resolved:
        resolved = resolved.replace("\n\n\n", "\n\n")
    return resolved


def extract_annotations(templatized: str) -> dict[str, Any]:
    """Parse <!-- --> annotation comments into structured metadata keyed by node ID."""
    annotations: dict[str, Any] = {}
    current_id: str | None = None

    for match in _ANNOTATION_PATTERN.finditer(templatized):
        content = match.group(1).strip()

        node_info = _parse_node_annotation(content)
        if node_info:
            current_id = node_info["id"]
            if current_id not in annotations:
                annotations[current_id] = {}
            annotations[current_id].update(node_info)
            continue

        gate_info = _parse_gate_annotation(content)
        if gate_info:
            current_id = gate_info["id"]
            if current_id not in annotations:
                annotations[current_id] = {}
            annotations[current_id].update(gate_info)
            continue

        if current_id:
            _parse_metadata_line(content, annotations[current_id])

    return annotations


def _parse_node_annotation(content: str) -> dict[str, Any] | None:
    """Parse 'node: Type id=X ...' annotations."""
    m = re.match(r"node:\s+(\w+)\s+id=(\S+)(.*)", content)
    if not m:
        return None
    result: dict[str, Any] = {"type": m.group(1), "id": m.group(2)}
    rest = m.group(3).strip()
    for kv in re.findall(r"(\w+)=(\S+)", rest):
        result[kv[0]] = kv[1]
    return result


def _parse_gate_annotation(content: str) -> dict[str, Any] | None:
    """Parse 'gate: GateNode id=X ...' annotations."""
    m = re.match(r"gate:\s+(\w+)\s+id=(\S+)(.*)", content)
    if not m:
        return None
    result: dict[str, Any] = {"type": m.group(1), "id": m.group(2)}
    rest = m.group(3).strip()
    for kv in re.findall(r"(\w+)=(\S+)", rest):
        result[kv[0]] = kv[1]
    return result


def _parse_metadata_line(content: str, meta: dict[str, Any]) -> None:
    """Parse key: value lines from annotation comments."""
    if content.startswith("NOTE:"):
        return

    m = re.match(r"(\w+):\s*(.*)", content)
    if not m:
        return
    key = m.group(1)
    value = m.group(2).strip()

    if key in ("reads", "writes"):
        if value and value != "none":
            meta[key] = [v.strip() for v in value.split(",")]
        else:
            meta[key] = []
    elif key == "edges":
        meta["edges_out"] = _parse_edges(value)
    elif key == "command":
        meta[key] = value
    elif key == "evaluator_command":
        meta[key] = value
    elif key == "targets":
        meta[key] = [t.strip() for t in value.split(",")]
    elif key == "sources":
        meta[key] = [s.strip() for s in value.split(",")]


def _parse_edges(edges_str: str) -> list[dict[str, str | None]]:
    """Parse edge strings like 'unconditional → target, proceed → target2'."""
    if not edges_str or edges_str == "none":
        return []
    edges = []
    for part in edges_str.split(","):
        part = part.strip()
        m = re.match(r"(\w+)\s*→\s*(\S+)", part)
        if m:
            condition = m.group(1)
            target = m.group(2)
            edges.append({
                "target": target,
                "condition": None if condition == "unconditional" else condition.upper(),
            })
    return edges


def annotations_to_yaml(annotations: dict[str, Any]) -> str:
    """Serialize annotations dict to YAML string."""
    return yaml.dump(annotations, default_flow_style=False, sort_keys=False, allow_unicode=True)
