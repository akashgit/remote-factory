from factory.models import ProjectState
from factory.workflow.definitions import optimize_workflow, register_all
from factory.workflow.primitives import AgentNode, AgentRole, FnNode, GateNode, Study
from factory.workflow.validation import validate_workflow


def test_optimize_workflow_validates():
    """optimize workflow graph passes structural validation."""
    wf = optimize_workflow()
    errors = validate_workflow(wf)
    assert errors == [], f"Validation errors: {errors}"


def test_optimize_trigger_correct_conditions():
    """optimize triggers only for mode=optimize + HAS_FACTORY."""
    wf = optimize_workflow()

    # Should trigger
    assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "optimize"}) is True

    # Should NOT trigger — wrong mode
    assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"}) is False
    assert wf.trigger(ProjectState.HAS_FACTORY, {}) is False

    # Should NOT trigger — wrong state
    assert wf.trigger(ProjectState.NO_FACTORY, {"mode": "optimize"}) is False
    assert wf.trigger(ProjectState.NO_REPO, {"mode": "optimize"}) is False
    assert wf.trigger(ProjectState.REPO_INCOMPLETE, {"mode": "optimize"}) is False
    assert wf.trigger(ProjectState.EVALS_PENDING_REVIEW, {"mode": "optimize"}) is False


def test_optimize_registered():
    """optimize workflow is registered in register_all."""
    workflows = register_all()
    assert "optimize" in workflows
    assert workflows["optimize"].name == "optimize"


def test_optimize_node_structure():
    """optimize workflow has exactly the expected 8 nodes."""
    wf = optimize_workflow()
    expected = {
        "study", "researcher", "gate_research",
        "strategist", "gate_strategy",
        "archivist_plan", "delegate_create", "archivist_outcome",
    }
    assert set(wf.nodes.keys()) == expected


def test_optimize_node_types():
    """Each node has the correct type and role."""
    wf = optimize_workflow()

    assert isinstance(wf.nodes["study"], Study)
    assert isinstance(wf.nodes["researcher"], AgentNode)
    assert wf.nodes["researcher"].role == AgentRole.RESEARCHER
    assert isinstance(wf.nodes["gate_research"], GateNode)
    assert wf.nodes["gate_research"].evaluator_role == AgentRole.CEO
    assert isinstance(wf.nodes["strategist"], AgentNode)
    assert wf.nodes["strategist"].role == AgentRole.STRATEGIST
    assert isinstance(wf.nodes["gate_strategy"], GateNode)
    assert wf.nodes["gate_strategy"].evaluator_type == "user"
    assert isinstance(wf.nodes["archivist_plan"], AgentNode)
    assert wf.nodes["archivist_plan"].role == AgentRole.ARCHIVIST
    assert isinstance(wf.nodes["delegate_create"], FnNode)
    assert isinstance(wf.nodes["archivist_outcome"], AgentNode)
    assert wf.nodes["archivist_outcome"].role == AgentRole.ARCHIVIST


def test_optimize_archivist_non_blocking():
    """Archivist nodes are non-blocking (fire-and-forget)."""
    wf = optimize_workflow()
    assert wf.nodes["archivist_plan"].blocking is False
    assert wf.nodes["archivist_outcome"].blocking is False


def test_optimize_start_node():
    """optimize workflow starts at study."""
    wf = optimize_workflow()
    assert wf.start_node == "study"


def test_optimize_edge_count():
    """optimize workflow has exactly 9 edges."""
    wf = optimize_workflow()
    assert len(wf.edges) == 9


def test_optimize_user_gate_present():
    """optimize workflow has a user approval gate (not just CEO/fn)."""
    wf = optimize_workflow()
    user_gates = [
        n for n in wf.nodes.values()
        if isinstance(n, GateNode) and n.evaluator_type == "user"
    ]
    assert len(user_gates) == 1
    assert user_gates[0].id == "gate_strategy"


def test_optimize_delegate_command():
    """delegate_create FnNode invokes create mode with --focus."""
    wf = optimize_workflow()
    node = wf.nodes["delegate_create"]
    assert isinstance(node, FnNode)
    assert "--mode create" in node.command
    assert "--focus" in node.command


def test_optimize_in_ceo_modes():
    """optimize is listed in CEO_MODES."""
    from factory.cli._helpers import CEO_MODES
    assert "optimize" in CEO_MODES


def test_optimize_skill_export():
    """optimize workflow produces a SKILL.md via skill export."""
    from factory.workflow.skill_export import workflow_to_skill_md

    wf = optimize_workflow()
    skill_md = workflow_to_skill_md(wf)
    assert len(skill_md) > 100
    assert "study" in skill_md.lower()
    assert "researcher" in skill_md.lower()
    assert "strategist" in skill_md.lower()
