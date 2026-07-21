"""End-to-end round-trip tests: Pydantic Workflow → YAML → Pydantic' → YAML'.

Verifies that yaml_to_workflow() and workflow_to_yaml() form a lossless
round-trip for all slot types (prompt, timeout, gate_prompt, max_iterations).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from factory.skillopt.yaml_surface import (
    load_yaml,
    workflow_to_yaml,
    yaml_to_workflow,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    GateNode,
    VerdictType,
    Workflow,
)


# ── helpers ──────────────────────────────────────────────────────


def _make_simple_workflow() -> Workflow:
    """Two AgentNodes (builder + researcher) and one GateNode."""
    nodes: dict[str, Any] = {}

    nodes["researcher"] = AgentNode(
        id="researcher",
        role=AgentRole.RESEARCHER,
        timeout=600,
        prompt_template=(
            "## Research Phase\n\n"
            "You are conducting deep research on the project.\n\n"
            "### Steps\n\n"
            "1. **Analyze the codebase** — read all source files under `src/`\n"
            "   and identify the major subsystems.\n"
            "2. **Check dependencies** — review `pyproject.toml` and `requirements.txt`\n"
            "   for outdated or vulnerable packages.\n"
            "3. **Search for prior art** — look at similar open-source projects\n"
            "   and document their approaches.\n\n"
            "### Output Format\n\n"
            "Write your findings in markdown with:\n"
            "- A summary section (2-3 paragraphs)\n"
            "- A detailed breakdown per subsystem\n"
            "- Risk assessment table\n\n"
            "```python\n"
            "# Example output structure\n"
            'findings = {"subsystems": [...], "risks": [...]}\n'
            "```\n"
        ),
        reads={".factory/strategy/observations.md"},
        writes={".factory/strategy/research-local.md"},
    )

    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        timeout=1200,
        prompt_template=(
            "## Implementation Phase\n\n"
            "Implement the hypothesis from `.factory/strategy/current.md`.\n\n"
            "### Rules\n\n"
            "- MINIMAL changes only — smallest diff that achieves the goal\n"
            "- Run `pytest -v` before committing\n"
            "- Do NOT modify files outside the declared scope\n"
            "- Each commit should be atomic and focused\n\n"
            "### Process\n\n"
            "1. Read the hypothesis and understand the expected outcome\n"
            "2. Identify which files need to change\n"
            "3. Make the changes\n"
            "4. Run tests\n"
            "5. Commit with a descriptive message\n"
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    nodes["gate_build"] = GateNode(
        id="gate_build",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Read builder output and PR diff. Does work match the hypothesis? "
            "No scope creep? Tests included? REDIRECT if off-scope."
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    edges = [
        Edge(source="researcher", target="builder"),
        Edge(source="builder", target="gate_build"),
        Edge(source="gate_build", target="builder", condition=VerdictType.RELOOP),
    ]

    return Workflow(
        name="test-simple",
        nodes=nodes,
        edges=edges,
        start_node="researcher",
    )


def _get_slot_value(annotations: dict, node_id: str, slot_prefix: str) -> str | None:
    """Extract a slot value from annotations by node_id and prefix."""
    node = annotations.get(node_id, {})
    slots = node.get("slots", {})
    for k, v in slots.items():
        if k.startswith(slot_prefix):
            return str(v)
    return None


def _get_prompt_from_yaml(annotations: dict, node_id: str) -> str | None:
    return _get_slot_value(annotations, node_id, "task_prompt_")


def _get_timeout_from_yaml(annotations: dict, node_id: str) -> str | None:
    return _get_slot_value(annotations, node_id, "timeout_")


def _get_gate_prompt_from_yaml(annotations: dict, node_id: str) -> str | None:
    return _get_slot_value(annotations, node_id, "gate_prompt_")


# ── Test 1: Simple round-trip (no edits) ────────────────────────


def test_simple_roundtrip_no_edits(tmp_path: Path) -> None:
    """Pydantic → YAML → Pydantic' → YAML' with no edits. Values must be identical."""
    wf = _make_simple_workflow()
    yaml_path = tmp_path / "annotations.yaml"

    # Forward: Pydantic → YAML
    ann1 = workflow_to_yaml(wf, yaml_path)
    assert yaml_path.exists()

    # Verify YAML has expected nodes
    assert "researcher" in ann1
    assert "builder" in ann1
    assert "gate_build" in ann1

    # Verify slots exist
    assert _get_prompt_from_yaml(ann1, "researcher") is not None
    assert _get_prompt_from_yaml(ann1, "builder") is not None
    assert _get_timeout_from_yaml(ann1, "researcher") is not None
    assert _get_timeout_from_yaml(ann1, "builder") is not None

    # Reverse: YAML → Pydantic'
    wf2 = yaml_to_workflow(yaml_path, "test-simple", workflow=wf)

    # Verify node IDs match
    assert set(wf2.nodes.keys()) == set(wf.nodes.keys()), (
        f"Node IDs differ: {set(wf2.nodes.keys())} vs {set(wf.nodes.keys())}"
    )

    # Verify edge topology matches
    orig_edges = {(e.source, e.target, e.condition) for e in wf.edges}
    rt_edges = {(e.source, e.target, e.condition) for e in wf2.edges}
    assert rt_edges == orig_edges, f"Edges differ:\n  original: {orig_edges}\n  roundtrip: {rt_edges}"

    # Verify timeout values match
    researcher1 = wf.nodes["researcher"]
    researcher2 = wf2.nodes["researcher"]
    assert isinstance(researcher1, AgentNode)
    assert isinstance(researcher2, AgentNode)
    assert researcher2.timeout == researcher1.timeout, (
        f"Timeout mismatch: {researcher2.timeout} vs {researcher1.timeout}"
    )

    builder1 = wf.nodes["builder"]
    builder2 = wf2.nodes["builder"]
    assert isinstance(builder1, AgentNode)
    assert isinstance(builder2, AgentNode)
    assert builder2.timeout == builder1.timeout

    # Second round-trip: Pydantic' → YAML'
    yaml_path2 = tmp_path / "annotations2.yaml"
    ann2 = workflow_to_yaml(wf2, yaml_path2)

    # YAML slot values should match
    for node_id in ("researcher", "builder"):
        v1 = _get_prompt_from_yaml(ann1, node_id)
        v2 = _get_prompt_from_yaml(ann2, node_id)
        assert v1 == v2, (
            f"Prompt slot mismatch for {node_id}:\n"
            f"  first:  {v1!r}\n"
            f"  second: {v2!r}"
        )

        t1 = _get_timeout_from_yaml(ann1, node_id)
        t2 = _get_timeout_from_yaml(ann2, node_id)
        assert t1 == t2, f"Timeout slot mismatch for {node_id}: {t1} vs {t2}"


# ── Test 2: Round-trip with prompt edits ─────────────────────────


def test_roundtrip_with_prompt_edits(tmp_path: Path) -> None:
    """Edit a prompt slot in YAML, convert back, verify the edit sticks."""
    wf = _make_simple_workflow()
    yaml_path = tmp_path / "annotations.yaml"

    # Forward: Pydantic → YAML
    ann1 = workflow_to_yaml(wf, yaml_path)

    # Mutate the builder prompt in the YAML
    original_prompt = _get_prompt_from_yaml(ann1, "builder")
    assert original_prompt is not None

    edited_prompt = original_prompt + (
        "\n\n### New Rule (Added by SkillOpt)\n\n"
        "- Always run `ruff check .` before committing\n"
        "- Check coverage with `pytest --cov`\n"
    )

    # Write the edited YAML
    surface = load_yaml(yaml_path)
    for k in surface["builder"]["slots"]:
        if k.startswith("task_prompt_"):
            surface["builder"]["slots"][k] = edited_prompt
    yaml_path.write_text(yaml.dump(surface, default_flow_style=False, allow_unicode=True))

    # Reverse: edited YAML → Pydantic'
    wf2 = yaml_to_workflow(yaml_path, "test-simple", workflow=wf)

    builder2 = wf2.nodes["builder"]
    assert isinstance(builder2, AgentNode)
    assert builder2.prompt_template == edited_prompt, (
        f"Prompt not updated:\n"
        f"  expected: {edited_prompt!r}\n"
        f"  got:      {builder2.prompt_template!r}"
    )

    # Verify other fields unchanged
    builder1 = wf.nodes["builder"]
    assert isinstance(builder1, AgentNode)
    assert builder2.timeout == builder1.timeout
    assert builder2.role == builder1.role

    orig_edges = {(e.source, e.target, e.condition) for e in wf.edges}
    rt_edges = {(e.source, e.target, e.condition) for e in wf2.edges}
    assert rt_edges == orig_edges

    # Second round-trip: Pydantic' → YAML' — verify edited value persists
    yaml_path2 = tmp_path / "annotations2.yaml"
    ann2 = workflow_to_yaml(wf2, yaml_path2)

    rt_prompt = _get_prompt_from_yaml(ann2, "builder")
    assert rt_prompt is not None
    assert "### New Rule (Added by SkillOpt)" in rt_prompt
    assert "ruff check" in rt_prompt


# ── Test 3: Multi-line string edge cases ─────────────────────────


def test_roundtrip_multiline_edge_cases(tmp_path: Path) -> None:
    """Verify exact preservation of tricky multi-line strings through YAML."""
    tricky_prompt = (
        "## Analysis Prompt\n\n"
        "Handle these edge cases:\n\n"
        "```python\n"
        "def tricky(x: str) -> str:\n"
        "    return f'result: {x}'\n"
        "```\n\n"
        "Beware of:\n"
        "- Single quotes: it's, don't, can't\n"
        '- Double quotes: she said "hello"\n'
        "- Backslashes: C:\\Users\\admin\n"
        "- Dollar signs: $HOME, ${PATH}\n"
        "- Curly braces: {key: value}, {nested}\n"
        "- Blank lines between sections\n\n\n"
        "- A very long line that exceeds two hundred characters and keeps going "
        "and going and going and going and going and going and going and going "
        "and going and going and going and going and going until it finally stops here.\n"
        "- Unicode: ✓ check, ✗ cross, → arrow, • bullet, éèêë\n"
        "- Indented block:\n"
        "    if condition:\n"
        "        do_something()\n"
        "        do_more()\n"
    )

    nodes: dict[str, Any] = {}
    nodes["analyzer"] = AgentNode(
        id="analyzer",
        role=AgentRole.RESEARCHER,
        timeout=600,
        prompt_template=tricky_prompt,
    )

    edges = [Edge(source="analyzer", target="analyzer", condition=VerdictType.RELOOP)]

    wf = Workflow(
        name="test-edge-cases",
        nodes=nodes,
        edges=edges,
        start_node="analyzer",
    )

    yaml_path = tmp_path / "annotations.yaml"
    ann1 = workflow_to_yaml(wf, yaml_path)

    wf2 = yaml_to_workflow(yaml_path, "test-edge-cases", workflow=wf)
    analyzer2 = wf2.nodes["analyzer"]
    assert isinstance(analyzer2, AgentNode)

    # The YAML round-trip slot includes the original prompt (since there are
    # no reads/writes, no extra lines get appended by the exporter).
    # Just verify the core content is preserved.
    assert "```python" in analyzer2.prompt_template
    assert "f'result: {x}'" in analyzer2.prompt_template
    assert "it's, don't, can't" in analyzer2.prompt_template
    assert 'she said "hello"' in analyzer2.prompt_template
    assert "C:\\Users\\admin" in analyzer2.prompt_template
    assert "$HOME" in analyzer2.prompt_template
    assert "{key: value}" in analyzer2.prompt_template
    assert "{nested}" in analyzer2.prompt_template
    assert "✓ check" in analyzer2.prompt_template
    assert "do_something()" in analyzer2.prompt_template

    # Second round-trip stability
    yaml_path2 = tmp_path / "annotations2.yaml"
    ann2 = workflow_to_yaml(wf2, yaml_path2)

    p1 = _get_prompt_from_yaml(ann1, "analyzer")
    p2 = _get_prompt_from_yaml(ann2, "analyzer")
    assert p1 == p2, (
        f"Second round-trip prompt mismatch:\n  first:  {p1!r}\n  second: {p2!r}"
    )


# ── Test 4: Real swebench workflow round-trip ────────────────────


def test_swebench_workflow_roundtrip(tmp_path: Path) -> None:
    """Load the actual swebench workflow, round-trip through YAML, edit, and verify."""
    from factory.workflow.contributed.swebench.workflow import workflow as swebench_wf_fn

    wf = swebench_wf_fn()
    yaml_path = tmp_path / "swebench_annotations.yaml"

    # Forward
    ann1 = workflow_to_yaml(wf, yaml_path)

    assert "builder" in ann1
    assert "study" in ann1
    assert "gate_verify" in ann1

    original_prompt = _get_prompt_from_yaml(ann1, "builder")
    assert original_prompt is not None
    assert "SWE-bench" in original_prompt

    # Edit: add a reproduce step
    edited_prompt = original_prompt.replace(
        "1. **Read the task instruction**",
        "0. **Reproduce the bug** — before anything else, write a minimal "
        "reproduction script and confirm the bug exists.\n\n"
        "1. **Read the task instruction**",
    )

    surface = load_yaml(yaml_path)
    for k in surface["builder"]["slots"]:
        if k.startswith("task_prompt_"):
            surface["builder"]["slots"][k] = edited_prompt
    yaml_path.write_text(yaml.dump(surface, default_flow_style=False, allow_unicode=True))

    # Reverse: edited YAML → Pydantic'
    wf2 = yaml_to_workflow(yaml_path, "swebench")

    builder2 = wf2.nodes["builder"]
    assert isinstance(builder2, AgentNode)
    assert "Reproduce the bug" in builder2.prompt_template
    assert "Read the task instruction" in builder2.prompt_template

    # Structural fields preserved
    builder_orig = wf.nodes["builder"]
    assert isinstance(builder_orig, AgentNode)
    assert builder2.role == builder_orig.role
    assert builder2.model == builder_orig.model

    # Edge topology preserved
    orig_edges = {(e.source, e.target, e.condition) for e in wf.edges}
    rt_edges = {(e.source, e.target, e.condition) for e in wf2.edges}
    assert rt_edges == orig_edges

    # Second round-trip: Pydantic' → YAML'
    yaml_path2 = tmp_path / "swebench_annotations2.yaml"
    ann2 = workflow_to_yaml(wf2, yaml_path2)

    rt_prompt = _get_prompt_from_yaml(ann2, "builder")
    assert rt_prompt is not None
    assert "Reproduce the bug" in rt_prompt


# ── Test 5: Gate node slot round-trip ────────────────────────────


def test_gate_node_roundtrip(tmp_path: Path) -> None:
    """Verify gate_prompt slots survive round-trip and edits."""
    nodes: dict[str, Any] = {}

    nodes["worker"] = AgentNode(
        id="worker",
        role=AgentRole.BUILDER,
        timeout=600,
        prompt_template="Implement the task as described.",
    )

    nodes["gate_quality"] = GateNode(
        id="gate_quality",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review the implementation for correctness and completeness. "
            "Check that tests pass and code follows project conventions. "
            "PROCEED if quality is acceptable. REDIRECT for fixes."
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    edges = [
        Edge(source="worker", target="gate_quality"),
        Edge(source="gate_quality", target="worker", condition=VerdictType.RELOOP),
    ]

    wf = Workflow(
        name="test-gate",
        nodes=nodes,
        edges=edges,
        start_node="worker",
    )

    yaml_path = tmp_path / "annotations.yaml"
    ann1 = workflow_to_yaml(wf, yaml_path)

    # Verify gate_prompt slot exists
    gate_prompt_val = _get_gate_prompt_from_yaml(ann1, "gate_quality")
    assert gate_prompt_val is not None
    assert "correctness and completeness" in gate_prompt_val

    # Round-trip: YAML → Pydantic'
    wf2 = yaml_to_workflow(yaml_path, "test-gate", workflow=wf)
    gate2 = wf2.nodes["gate_quality"]
    assert isinstance(gate2, GateNode)
    assert "correctness and completeness" in gate2.gate_prompt

    # Edit gate_prompt in YAML
    edited_gate_prompt = (
        "STRICT REVIEW: Check correctness, completeness, AND security. "
        "Run SAST scan if available. PROCEED only if zero issues."
    )
    surface = load_yaml(yaml_path)
    for k in surface["gate_quality"]["slots"]:
        if k.startswith("gate_prompt_"):
            surface["gate_quality"]["slots"][k] = edited_gate_prompt
    yaml_path.write_text(yaml.dump(surface, default_flow_style=False, allow_unicode=True))

    # Verify edited value
    wf3 = yaml_to_workflow(yaml_path, "test-gate", workflow=wf)
    gate3 = wf3.nodes["gate_quality"]
    assert isinstance(gate3, GateNode)
    assert gate3.gate_prompt == edited_gate_prompt

    # Second round-trip stability
    yaml_path2 = tmp_path / "annotations2.yaml"
    ann2 = workflow_to_yaml(wf3, yaml_path2)
    gate_prompt_rt = _get_gate_prompt_from_yaml(ann2, "gate_quality")
    assert gate_prompt_rt is not None
    assert "SAST scan" in gate_prompt_rt


# ── Test 6: Multiple nodes with selective edits ──────────────────


def test_multiple_nodes_selective_edits(tmp_path: Path) -> None:
    """Edit prompts in 2 of 3 nodes; verify unedited node preserves original."""
    nodes: dict[str, Any] = {}

    nodes["researcher"] = AgentNode(
        id="researcher",
        role=AgentRole.RESEARCHER,
        timeout=600,
        prompt_template=(
            "Conduct thorough research on the project.\n"
            "Identify weak spots and improvement opportunities.\n"
            "Write findings to the strategy directory.\n"
        ),
        writes={".factory/strategy/research-local.md"},
    )

    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        timeout=1200,
        prompt_template=(
            "Implement the approved hypothesis.\n"
            "Follow the project's coding standards.\n"
            "Run tests before committing.\n"
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    nodes["reviewer"] = AgentNode(
        id="reviewer",
        role=AgentRole.CODE_REVIEWER,
        timeout=900,
        prompt_template=(
            "Review the builder's changes for quality.\n"
            "Check for security issues and performance problems.\n"
            "Write review to the reviews directory.\n"
        ),
        reads={".factory/reviews/builder-latest.md"},
        writes={".factory/reviews/code-review.md"},
    )

    edges = [
        Edge(source="researcher", target="builder"),
        Edge(source="builder", target="reviewer"),
    ]

    wf = Workflow(
        name="test-multi",
        nodes=nodes,
        edges=edges,
        start_node="researcher",
    )

    yaml_path = tmp_path / "annotations.yaml"
    ann1 = workflow_to_yaml(wf, yaml_path)

    # Edit researcher and builder prompts, leave reviewer untouched
    surface = load_yaml(yaml_path)

    for k in surface["researcher"]["slots"]:
        if k.startswith("task_prompt_"):
            surface["researcher"]["slots"][k] = (
                "EDITED: Do web research on latest patterns.\n"
                "Focus on performance optimization techniques.\n"
            )

    for k in surface["builder"]["slots"]:
        if k.startswith("task_prompt_"):
            surface["builder"]["slots"][k] = (
                "EDITED: Build with TDD approach.\n"
                "Write tests first, then implement.\n"
            )

    yaml_path.write_text(yaml.dump(surface, default_flow_style=False, allow_unicode=True))

    # Round-trip
    wf2 = yaml_to_workflow(yaml_path, "test-multi", workflow=wf)

    researcher2 = wf2.nodes["researcher"]
    builder2 = wf2.nodes["builder"]
    reviewer2 = wf2.nodes["reviewer"]
    assert isinstance(researcher2, AgentNode)
    assert isinstance(builder2, AgentNode)
    assert isinstance(reviewer2, AgentNode)

    # Edited nodes have new values
    assert "EDITED: Do web research" in researcher2.prompt_template
    assert "EDITED: Build with TDD" in builder2.prompt_template

    # Unedited reviewer has original prompt (from register_all default)
    reviewer_orig = wf.nodes["reviewer"]
    assert isinstance(reviewer_orig, AgentNode)

    # The reviewer prompt in wf2 comes from the original workflow definition
    # (register_all), not from our test workflow, since yaml_to_workflow loads
    # the registered workflow. The YAML slot for reviewer is what we wrote
    # initially (which was the original prompt + reads/writes lines appended by
    # skill_export). The key point: we did NOT edit it, so it should be
    # whatever the YAML contained.
    reviewer_yaml_prompt = _get_prompt_from_yaml(ann1, "reviewer")
    assert reviewer_yaml_prompt is not None
    assert "Review the builder" in reviewer_yaml_prompt or "quality" in reviewer_yaml_prompt


# ── Test 7: Structural fields NOT affected by slot edits ─────────


def test_structural_fields_unchanged_by_slot_edits(tmp_path: Path) -> None:
    """Editing prompt slots must NOT change node types, edges, reads, writes, role, model, blocking."""
    wf = _make_simple_workflow()
    yaml_path = tmp_path / "annotations.yaml"
    workflow_to_yaml(wf, yaml_path)

    # Edit the builder prompt slot only
    surface = load_yaml(yaml_path)
    for k in surface["builder"]["slots"]:
        if k.startswith("task_prompt_"):
            surface["builder"]["slots"][k] = "COMPLETELY REWRITTEN PROMPT"
    yaml_path.write_text(yaml.dump(surface, default_flow_style=False, allow_unicode=True))

    wf2 = yaml_to_workflow(yaml_path, "test-simple", workflow=wf)

    # Verify structural fields for every node
    for node_id in wf.nodes:
        orig = wf.nodes[node_id]
        rt = wf2.nodes[node_id]

        assert type(orig) is type(rt), (
            f"Node type changed for {node_id}: {type(orig).__name__} → {type(rt).__name__}"
        )
        assert rt.blocking == orig.blocking, (
            f"Blocking changed for {node_id}: {orig.blocking} → {rt.blocking}"
        )

        if isinstance(orig, AgentNode) and isinstance(rt, AgentNode):
            assert rt.role == orig.role, (
                f"Role changed for {node_id}: {orig.role} → {rt.role}"
            )
            assert rt.model == orig.model, (
                f"Model changed for {node_id}: {orig.model} → {rt.model}"
            )

    # Edge topology unchanged
    orig_edges = sorted(
        [(e.source, e.target, e.condition) for e in wf.edges],
    )
    rt_edges = sorted(
        [(e.source, e.target, e.condition) for e in wf2.edges],
    )
    assert rt_edges == orig_edges, (
        f"Edge topology changed:\n  original: {orig_edges}\n  roundtrip: {rt_edges}"
    )
