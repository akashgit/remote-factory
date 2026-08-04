"""Tests for W₁₅: Plan Mode workflow — prior plan check + research + strategy + archive."""

from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.definitions import plan_workflow, register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
    VerdictType,
)
from factory.workflow.skill_export import WORKFLOW_META, workflow_to_skill_md


# ── 8a. Graph Validation ──────────────────────────────────────────


def test_plan_workflow_valid():
    """plan_workflow() produces a valid graph."""
    wf = plan_workflow()
    errors = wf.validate_graph()
    assert errors == [], f"Validation errors: {errors}"


# ── 8b. Skill Export — No Builder ─────────────────────────────────


def test_plan_skill_has_no_builder():
    """Plan mode SKILL.md must not reference any builder agent."""
    wf = plan_workflow()
    skill_md = workflow_to_skill_md(wf)
    assert "factory agent builder" not in skill_md
    assert "factory agent health_checker" not in skill_md
    assert "factory agent code_reviewer" not in skill_md
    assert "factory agent adversarial_tester" not in skill_md


# ── 8c. Skill Export — Has Research and Strategy ──────────────────


def test_plan_skill_has_research_and_strategy():
    """Plan mode SKILL.md must include research fork and strategist."""
    wf = plan_workflow()
    skill_md = workflow_to_skill_md(wf)
    assert "factory agent researcher" in skill_md
    assert "factory agent strategist" in skill_md
    assert "factory agent archivist" in skill_md
    assert "Research (Parallel)" in skill_md


# ── 8d. Trigger Function ─────────────────────────────────────────


def test_plan_trigger_mode_plan():
    """Plan trigger fires only when ctx mode is 'plan'."""
    wf = plan_workflow()
    assert wf.trigger is not None

    for state in ProjectState:
        assert wf.trigger(state, {"mode": "plan"}) is True
        assert wf.trigger(state, {"mode": "improve"}) is False
        assert wf.trigger(state, {}) is False


# ── 8e. Registration ─────────────────────────────────────────────


def test_plan_registered():
    """Plan workflow is registered in register_all()."""
    registry = register_all()
    assert "plan" in registry
    wf = registry["plan"]
    assert wf.name == "plan"
    assert wf.terminal is True


# ── 8f. Terminal Flag ─────────────────────────────────────────────


def test_plan_is_terminal():
    """Plan mode must be terminal — does not chain to other modes."""
    wf = plan_workflow()
    assert wf.terminal is True


# ── 8g. Node Count and Types ─────────────────────────────────────


def test_plan_node_structure():
    """Plan mode has exactly the right nodes — no implementation agents."""
    wf = plan_workflow()

    assert len(wf.nodes) == 13

    agent_roles = {
        n.role for n in wf.nodes.values() if isinstance(n, AgentNode)
    }
    assert AgentRole.BUILDER not in agent_roles
    assert AgentRole.HEALTH_CHECKER not in agent_roles
    assert AgentRole.CODE_REVIEWER not in agent_roles
    assert AgentRole.ADVERSARIAL_TESTER not in agent_roles

    assert AgentRole.RESEARCHER in agent_roles
    assert AgentRole.STRATEGIST in agent_roles
    assert AgentRole.ARCHIVIST in agent_roles


# ── 8h. WORKFLOW_META Entry ──────────────────────────────────────


def test_plan_workflow_meta():
    """Plan mode has a WORKFLOW_META entry."""
    assert "plan" in WORKFLOW_META
    assert "description" in WORKFLOW_META["plan"]
    assert "argument_hint" in WORKFLOW_META["plan"]
    assert "plan" in WORKFLOW_META["plan"]["description"].lower()


# ── 8i. Prior Plan Detection — Node Exists ────────────────────────


def test_plan_has_prior_plan_detection():
    """Plan mode has check_prior_plans and gate_prior_plans nodes."""
    wf = plan_workflow()

    assert "check_prior_plans" in wf.nodes
    assert "gate_prior_plans" in wf.nodes

    check_node = wf.nodes["check_prior_plans"]
    assert isinstance(check_node, GateNode)
    assert check_node.evaluator_type == "fn"

    gate_node = wf.nodes["gate_prior_plans"]
    assert isinstance(gate_node, GateNode)
    assert gate_node.evaluator_type == "user"


# ── 8j. Prior Plan Detection — Routing ────────────────────────────


def test_plan_prior_plan_routing():
    """check_prior_plans routes to gate_prior_plans (matches) or fork_research (no matches)."""
    wf = plan_workflow()

    check_edges = [e for e in wf.edges if e.source == "check_prior_plans"]
    assert len(check_edges) == 2

    targets_by_condition = {e.condition: e.target for e in check_edges}
    assert targets_by_condition[VerdictType.PROCEED] == "gate_prior_plans"
    assert targets_by_condition[VerdictType.HALT] == "fork_research"


# ── 8k. Gate Keep Plan — Keep/Discard Paths ──────────────────────


def test_plan_gate_keep_plan():
    """gate_keep_plan is a user gate with PROCEED → gate_seed_backlog, HALT → implicit termination."""
    wf = plan_workflow()

    assert "gate_keep_plan" in wf.nodes
    gate_node = wf.nodes["gate_keep_plan"]
    assert isinstance(gate_node, GateNode)
    assert gate_node.evaluator_type == "user"

    gate_edges = [e for e in wf.edges if e.source == "gate_keep_plan"]
    assert len(gate_edges) == 1

    targets_by_condition = {e.condition: e.target for e in gate_edges}
    assert targets_by_condition[VerdictType.PROCEED] == "gate_seed_backlog"
    assert VerdictType.HALT not in targets_by_condition


# ── 8k2. Gate Seed Backlog — Seed/Archive-Only Paths ─────────────


def test_plan_gate_seed_backlog():
    """gate_seed_backlog is a user gate with PROCEED → seed_backlog, HALT → archivist_plan."""
    wf = plan_workflow()

    assert "gate_seed_backlog" in wf.nodes
    gate_node = wf.nodes["gate_seed_backlog"]
    assert isinstance(gate_node, GateNode)
    assert gate_node.evaluator_type == "user"

    gate_edges = [e for e in wf.edges if e.source == "gate_seed_backlog"]
    assert len(gate_edges) == 2

    targets_by_condition = {e.condition: e.target for e in gate_edges}
    assert targets_by_condition[VerdictType.PROCEED] == "seed_backlog"
    assert targets_by_condition[VerdictType.HALT] == "archivist_plan"


# ── 8l. Backlog Seeding — Node Exists ─────────────────────────────


def test_plan_has_backlog_seeding():
    """Plan mode has seed_backlog node that writes to backlog.md."""
    wf = plan_workflow()

    assert "seed_backlog" in wf.nodes
    seed_node = wf.nodes["seed_backlog"]
    assert isinstance(seed_node, FnNode)
    assert ".factory/strategy/backlog.md" in seed_node.writes
    assert ".factory/strategy/current.md" in seed_node.reads


# ── 8m. Backlog Seeding — Path ────────────────────────────────────


def test_plan_seed_backlog_path():
    """seed_backlog is only reached via Seed (PROCEED) from gate_seed_backlog."""
    wf = plan_workflow()

    to_seed = [e for e in wf.edges if e.target == "seed_backlog"]
    assert len(to_seed) == 1
    assert to_seed[0].source == "gate_seed_backlog"
    assert to_seed[0].condition == VerdictType.PROCEED

    from_seed = [e for e in wf.edges if e.source == "seed_backlog"]
    assert len(from_seed) == 1
    assert from_seed[0].target == "archivist_plan"


# ── 8n. Archive Naming — Archivist Prompt ─────────────────────────


def test_plan_archive_naming_convention():
    """Archivist prompt includes naming convention with topic slug and date."""
    wf = plan_workflow()

    archivist = wf.nodes["archivist_plan"]
    assert isinstance(archivist, AgentNode)
    assert "plan-<topic-slug>-<YYYY-MM-DD>.md" in archivist.prompt_template
    assert "collision" in archivist.prompt_template.lower()
    assert "suffix" in archivist.prompt_template.lower()


# ── 8o. Start Node ────────────────────────────────────────────────


def test_plan_start_node():
    """Plan mode starts at check_prior_plans, not fork_research."""
    wf = plan_workflow()
    assert wf.start_node == "check_prior_plans"
