"""Regression test: annotations extracted from exported skills must match source workflow graphs.

This catches:
- Workflow definition changed but pipeline not re-run
- Bug in templatize that produces wrong annotations
- Bug in splitter that loses information
"""

import pytest

from factory.workflow.definitions import register_all
from factory.workflow.primitives import AgentNode, GateNode
from factory.workflow.skill_export import workflow_to_skill_md
from factory.workflow.splitter import split_skill


def _all_workflow_names() -> list[str]:
    return sorted(register_all().keys())


def _edge_in_annotations(
    annotations: dict,
    source: str,
    target: str,
    condition: str | None,
) -> bool:
    """Check if an edge exists in the annotations for a given source node."""
    source_meta = annotations.get(source)
    if not source_meta:
        return False
    edges_out = source_meta.get("edges_out", [])
    for edge in edges_out:
        if edge["target"] == target:
            expected_cond = condition.upper() if condition else None
            if edge.get("condition") == expected_cond:
                return True
    return False


@pytest.mark.parametrize("workflow_name", _all_workflow_names())
def test_annotations_match_source(workflow_name: str) -> None:
    """Verify that annotations extracted from templatized skills match the source workflow."""
    workflows = register_all()
    wf = workflows[workflow_name]

    templatized = workflow_to_skill_md(wf)
    _, annotations = split_skill(templatized)

    for node_id, meta in annotations.items():
        source_node = wf.nodes.get(node_id)
        assert source_node is not None, (
            f"Annotation references node '{node_id}' not found in workflow '{workflow_name}'"
        )

        assert meta["type"] == type(source_node).__name__, (
            f"Type mismatch for node '{node_id}' in workflow '{workflow_name}': "
            f"annotation={meta['type']}, source={type(source_node).__name__}"
        )

        if isinstance(source_node, AgentNode):
            assert meta.get("role") == source_node.role.value, (
                f"Role mismatch for node '{node_id}': "
                f"annotation={meta.get('role')}, source={source_node.role.value}"
            )

        if isinstance(source_node, GateNode):
            assert meta.get("evaluator_type") == source_node.evaluator_type, (
                f"Evaluator type mismatch for node '{node_id}': "
                f"annotation={meta.get('evaluator_type')}, source={source_node.evaluator_type}"
            )


@pytest.mark.parametrize("workflow_name", _all_workflow_names())
def test_all_nodes_have_annotations(workflow_name: str) -> None:
    """Verify that every non-fork-target node in the workflow has annotations."""
    workflows = register_all()
    wf = workflows[workflow_name]

    templatized = workflow_to_skill_md(wf)
    _, annotations = split_skill(templatized)

    from factory.workflow.primitives import ForkNode
    fork_targets: set[str] = set()
    for node in wf.nodes.values():
        if isinstance(node, ForkNode):
            fork_targets.update(node.targets)

    deep_qa_node_ids = {
        "health_checker", "gate_health", "code_reviewer", "gate_review",
        "adversarial_tester", "gate_adversarial", "join_verdict",
    }
    has_deep_qa = deep_qa_node_ids.issubset(set(wf.nodes))

    for node_id in wf.nodes:
        if node_id in fork_targets:
            continue
        if has_deep_qa and node_id in deep_qa_node_ids:
            continue
        assert node_id in annotations, (
            f"Node '{node_id}' in workflow '{workflow_name}' has no annotations"
        )


@pytest.mark.parametrize("workflow_name", _all_workflow_names())
def test_templatized_skill_validates(workflow_name: str) -> None:
    """Verify that templatized skills still pass basic validation after resolving."""
    from factory.workflow.skill_export import validate_skill
    from factory.workflow.templates import resolve

    workflows = register_all()
    wf = workflows[workflow_name]
    templatized = workflow_to_skill_md(wf)
    resolved = resolve(templatized)
    issues = validate_skill(resolved)
    assert issues == [], (
        f"Validation issues for workflow '{workflow_name}': {issues}"
    )
