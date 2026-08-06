"""Tests for plan_workflow — W₁₅: Plan Mode."""

from __future__ import annotations

import subprocess

import pytest

from factory.workflow.definitions import plan_workflow
from factory.workflow.primitives import (
    AgentNode,
    FnNode,
    GateNode,
    VerdictType,
)


@pytest.fixture()
def wf():
    return plan_workflow()


# ── Structure tests ──────────────────────────────────────────────


def test_plan_workflow_structure(wf):
    """Verify node and edge counts match the expected topology."""
    assert len(wf.nodes) == 12
    assert len(wf.edges) == 16
    assert wf.name == "plan"
    assert wf.start_node == "check_prior_plans"
    assert wf.terminal is True


def test_plan_workflow_no_archivist_in_build_path(wf):
    """Verify no archivist node exists — replaced by GitHub publishing."""
    assert "archivist_plan" not in wf.nodes
    for node in wf.nodes.values():
        if isinstance(node, AgentNode):
            assert node.role.value != "archivist"


def test_plan_workflow_edge_coverage(wf):
    """Verify all expected edges exist with correct conditions."""
    edge_tuples = [
        (e.source, e.target, e.condition)
        for e in wf.edges
    ]
    expected = [
        ("check_prior_plans", "gate_prior_plans", VerdictType.PROCEED),
        ("check_prior_plans", "fork_research", VerdictType.HALT),
        ("gate_prior_plans", "fork_research", VerdictType.PROCEED),
        ("fork_research", "researcher_domain", None),
        ("fork_research", "researcher_practices", None),
        ("fork_research", "researcher_constraints", None),
        ("researcher_domain", "join_research", None),
        ("researcher_practices", "join_research", None),
        ("researcher_constraints", "join_research", None),
        ("join_research", "gate_research", None),
        ("gate_research", "strategist", VerdictType.PROCEED),
        ("gate_research", "fork_research", VerdictType.RELOOP),
        ("strategist", "gate_keep_plan", None),
        ("gate_keep_plan", "publish_github", VerdictType.PROCEED),
        ("gate_keep_plan", "strategist", VerdictType.RELOOP),
        ("publish_github", "seed_backlog", None),
    ]
    assert edge_tuples == expected


# ── Node-specific tests ─────────────────────────────────────────


def test_plan_publish_github_node_exists(wf):
    """Verify publish_github FnNode exists with correct reads/writes."""
    node = wf.nodes["publish_github"]
    assert isinstance(node, FnNode)
    assert ".factory/strategy/current.md" in node.reads
    assert ".factory/strategy/github-issue-ref.txt" in node.writes


def test_plan_single_gate_prompt_includes_github_warning(wf):
    """Verify gate_keep_plan prompt warns about GitHub publishing."""
    node = wf.nodes["gate_keep_plan"]
    assert isinstance(node, GateNode)
    assert node.evaluator_type == "user"
    assert "GitHub issue" in node.gate_prompt
    assert "backlog" in node.gate_prompt


def test_plan_no_archivist_node(wf):
    """Verify archivist_plan is NOT in workflow nodes."""
    assert "archivist_plan" not in wf.nodes


def test_plan_publish_directly_wired_after_gate(wf):
    """Verify publish_github and seed_backlog are directly wired with no gates between."""
    edges_from_keep = [
        (e.target, e.condition) for e in wf.edges if e.source == "gate_keep_plan"
    ]
    assert ("publish_github", VerdictType.PROCEED) in edges_from_keep
    assert ("strategist", VerdictType.RELOOP) in edges_from_keep

    edges_from_publish = [
        (e.target, e.condition) for e in wf.edges if e.source == "publish_github"
    ]
    assert ("seed_backlog", None) in edges_from_publish

    # Removed gate nodes must not exist
    assert "gate_publish_github" not in wf.nodes
    assert "gate_seed_backlog" not in wf.nodes


def test_plan_seed_backlog_no_archive_ref(wf):
    """Verify seed_backlog references github-issue-ref.txt, not .factory/archive/."""
    node = wf.nodes["seed_backlog"]
    assert isinstance(node, FnNode)
    assert "github-issue-ref.txt" in node.command
    assert ".factory/archive/" not in node.command


def test_plan_check_prior_plans_github_search(wf):
    """Verify check_prior_plans searches GitHub issues first."""
    node = wf.nodes["check_prior_plans"]
    assert isinstance(node, GateNode)
    assert "gh issue list --label plan" in node.evaluator_command


def test_plan_check_prior_plans_local_fallback(wf):
    """Verify check_prior_plans falls back to local grep."""
    node = wf.nodes["check_prior_plans"]
    assert isinstance(node, GateNode)
    assert "grep -Frl" in node.evaluator_command


def test_plan_publish_github_graceful_degradation(wf):
    """Verify publish_github checks gh auth status for graceful degradation."""
    node = wf.nodes["publish_github"]
    assert isinstance(node, FnNode)
    assert "gh auth status" in node.command


def test_plan_publish_github_auto_creates_repo(wf):
    """Verify publish_github contains gh repo create for auto-creating repos."""
    node = wf.nodes["publish_github"]
    assert isinstance(node, FnNode)
    assert "gh repo create" in node.command


def test_plan_publish_github_creates_private_repo(wf):
    """Verify publish_github creates private repos by default."""
    node = wf.nodes["publish_github"]
    assert isinstance(node, FnNode)
    assert "--private" in node.command


def test_plan_publish_github_handles_existing_repo(wf):
    """Verify publish_github handles 'already exists' case."""
    node = wf.nodes["publish_github"]
    assert isinstance(node, FnNode)
    assert "already exists" in node.command
    assert "git remote add origin" in node.command


def test_plan_publish_github_checks_git_worktree(wf):
    """Verify publish_github checks git rev-parse --is-inside-work-tree."""
    node = wf.nodes["publish_github"]
    assert isinstance(node, FnNode)
    assert "git rev-parse --is-inside-work-tree" in node.command


def test_plan_publish_github_exits_zero_on_all_failures(wf):
    """Verify publish_github exits 0 on all failure paths."""
    node = wf.nodes["publish_github"]
    assert isinstance(node, FnNode)
    assert node.command.count("exit 0") >= 3


def test_plan_publish_github_user_facing_messages(wf):
    """Verify publish_github echoes clear user-facing messages."""
    node = wf.nodes["publish_github"]
    assert isinstance(node, FnNode)
    assert "Creating private GitHub repository:" in node.command
    assert "GitHub repository created:" in node.command
    assert "plan saved locally only" in node.command
    assert "already exists on GitHub, linking as remote" in node.command


def test_plan_publish_github_body_file(wf):
    """Verify publish_github uses --body-file, not --body."""
    node = wf.nodes["publish_github"]
    assert isinstance(node, FnNode)
    assert "--body-file" in node.command


def test_plan_workflow_validates():
    """Run factory workflow validate plan and assert no errors."""
    result = subprocess.run(
        ["factory", "workflow", "validate", "plan"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Validation failed: {result.stderr}"


def test_plan_skill_export(wf):
    """Verify skill export produces valid SKILL.md content."""
    from factory.workflow.skill_export import workflow_to_skill_md

    skill = workflow_to_skill_md(wf)
    assert "workflow-plan" in skill
    assert "Publish" in skill
    assert "archivist" not in skill.lower() or "archivist_plan" not in skill
    assert "single" in skill.lower() or "Single" in skill
