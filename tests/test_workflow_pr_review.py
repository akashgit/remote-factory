"""Tests for the pr-review project-local workflow."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


# Load the workflow module from .factory/workflows/pr_review.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW_FILE = _PROJECT_ROOT / ".factory" / "workflows" / "pr_review.py"


def _load_pr_review_module():
    """Import pr_review.py from .factory/workflows/ as a module."""
    spec = importlib.util.spec_from_file_location("pr_review_workflow", _WORKFLOW_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_pr_review_module()
meta = _mod.meta
workflow = _mod.workflow


class TestPRReviewWorkflowStructure:
    """Tests for graph structure and node configuration."""

    def test_workflow_name(self) -> None:
        """Workflow name matches meta dict."""
        wf = workflow()
        assert wf.name == "pr-review"

    def test_node_count(self) -> None:
        """Workflow has exactly 5 nodes."""
        wf = workflow()
        assert len(wf.nodes) == 5
        assert set(wf.nodes.keys()) == {
            "fetch_pr",
            "researcher_pr",
            "reviewer_pr",
            "gate_review",
            "finalize_pr",
        }

    def test_start_node(self) -> None:
        """Start node is fetch_pr."""
        wf = workflow()
        assert wf.start_node == "fetch_pr"

    def test_workflow_is_terminal(self) -> None:
        """PR review is a terminal workflow — no chaining."""
        wf = workflow()
        assert wf.terminal is True

    def test_graph_validates(self) -> None:
        """Graph passes structural validation (DAG check, edge consistency)."""
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow has validation issues: {issues}"

    def test_edge_count(self) -> None:
        """6 edges: 3 unconditional + 3 conditional (PROCEED, RELOOP, HALT)."""
        wf = workflow()
        assert len(wf.edges) == 6


class TestPRReviewNodes:
    """Tests for individual node configuration."""

    def test_fetch_pr_is_agent_node(self) -> None:
        """fetch_pr is an AgentNode (researcher, haiku) that fetches PR data via gh CLI."""
        from factory.workflow.primitives import AgentNode, AgentRole

        wf = workflow()
        node = wf.nodes["fetch_pr"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER
        assert node.model == "haiku"
        assert node.timeout == 120
        assert "gh pr view" in node.prompt_template
        assert "gh pr diff" in node.prompt_template
        assert ".factory/reviews/pr-context.md" in node.writes
        assert len(node.post_checks) >= 1
        check = node.post_checks[0]
        assert check.path == ".factory/reviews/pr-context.md"
        assert check.must_exist is True
        assert check.min_size == 100
        assert "PR Metadata" in check.must_contain
        assert "PR Diff" in check.must_contain

    def test_researcher_pr_node(self) -> None:
        """researcher_pr is an AgentNode with researcher role."""
        from factory.workflow.primitives import AgentNode, AgentRole

        wf = workflow()
        node = wf.nodes["researcher_pr"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER
        assert node.model == "sonnet"
        assert node.timeout == 600
        assert ".factory/reviews/pr-context.md" in node.reads
        assert ".factory/reviews/pr-research.md" in node.writes
        assert len(node.post_checks) >= 1

    def test_reviewer_pr_node(self) -> None:
        """reviewer_pr is an AgentNode with code_reviewer role."""
        from factory.workflow.primitives import AgentNode, AgentRole

        wf = workflow()
        node = wf.nodes["reviewer_pr"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.CODE_REVIEWER
        assert node.timeout == 900
        assert ".factory/reviews/pr-context.md" in node.reads
        assert ".factory/reviews/pr-research.md" in node.reads
        assert ".factory/reviews/reviewer-draft.md" in node.writes
        assert len(node.post_checks) >= 1
        # Verify prompt contains both analysis criteria
        assert "Root Cause" in node.prompt_template
        assert "Solution Validation" in node.prompt_template

    def test_gate_review_node(self) -> None:
        """gate_review is a GateNode with CEO evaluator."""
        from factory.workflow.primitives import AgentRole, GateNode

        wf = workflow()
        node = wf.nodes["gate_review"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "agent"
        assert node.evaluator_role == AgentRole.CEO
        assert node.max_iterations == 2
        assert ".factory/reviews/reviewer-draft.md" in node.reads

    def test_finalize_pr_node(self) -> None:
        """finalize_pr is an AgentNode (archivist, haiku, blocking)."""
        from factory.workflow.primitives import AgentNode, AgentRole

        wf = workflow()
        node = wf.nodes["finalize_pr"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.ARCHIVIST
        assert node.model == "haiku"
        assert node.blocking is True
        assert ".factory/reviews/pr-review.md" in node.writes


class TestPRReviewEdges:
    """Tests for edge wiring."""

    def test_proceed_edge_to_finalize(self) -> None:
        """gate_review PROCEED → finalize_pr."""
        from factory.workflow.primitives import VerdictType

        wf = workflow()
        proceed = [
            e
            for e in wf.edges
            if e.source == "gate_review"
            and e.target == "finalize_pr"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed) == 1

    def test_reloop_edge_to_reviewer(self) -> None:
        """gate_review RELOOP → reviewer_pr."""
        from factory.workflow.primitives import VerdictType

        wf = workflow()
        reloop = [
            e
            for e in wf.edges
            if e.source == "gate_review"
            and e.target == "reviewer_pr"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop) == 1

    def test_halt_edge_to_finalize(self) -> None:
        """gate_review HALT → finalize_pr (graceful degradation)."""
        from factory.workflow.primitives import VerdictType

        wf = workflow()
        halt = [
            e
            for e in wf.edges
            if e.source == "gate_review"
            and e.target == "finalize_pr"
            and e.condition == VerdictType.HALT
        ]
        assert len(halt) == 1

    def test_linear_flow(self) -> None:
        """Unconditional edges form: fetch_pr → researcher_pr → reviewer_pr → gate_review."""
        wf = workflow()
        unconditional = [e for e in wf.edges if e.condition is None]
        assert len(unconditional) == 3
        sources_targets = {(e.source, e.target) for e in unconditional}
        assert ("fetch_pr", "researcher_pr") in sources_targets
        assert ("researcher_pr", "reviewer_pr") in sources_targets
        assert ("reviewer_pr", "gate_review") in sources_targets


class TestPRReviewTrigger:
    """Tests for the trigger function."""

    def test_trigger_matches_pr_review_mode(self) -> None:
        """Trigger fires for mode='pr-review' with numeric focus."""
        from factory.workflow.primitives import ProjectState

        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "pr-review", "focus": "1234"})

    def test_trigger_accepts_integer_focus(self) -> None:
        """Trigger accepts integer PR numbers."""
        from factory.workflow.primitives import ProjectState

        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "pr-review", "focus": 42})

    def test_trigger_accepts_hash_prefix(self) -> None:
        """Trigger accepts '#1234' format."""
        from factory.workflow.primitives import ProjectState

        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "pr-review", "focus": "#1234"})

    def test_trigger_rejects_no_focus(self) -> None:
        """Trigger rejects when focus is missing."""
        from factory.workflow.primitives import ProjectState

        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "pr-review"})

    def test_trigger_rejects_non_numeric_focus(self) -> None:
        """Trigger rejects non-numeric focus values."""
        from factory.workflow.primitives import ProjectState

        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(
            ProjectState.HAS_FACTORY, {"mode": "pr-review", "focus": "main"}
        )

    def test_trigger_rejects_other_modes(self) -> None:
        """Trigger rejects non-pr-review modes."""
        from factory.workflow.primitives import ProjectState

        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "build", "focus": "42"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve", "focus": "42"})

    def test_trigger_works_any_project_state(self) -> None:
        """Trigger fires regardless of project state (PR review works anywhere)."""
        from factory.workflow.primitives import ProjectState

        wf = workflow()
        assert wf.trigger is not None
        ctx = {"mode": "pr-review", "focus": "99"}
        assert wf.trigger(ProjectState.NO_REPO, ctx)
        assert wf.trigger(ProjectState.NO_FACTORY, ctx)
        assert wf.trigger(ProjectState.HAS_FACTORY, ctx)


class TestPRReviewMeta:
    """Tests for the module-level meta dict."""

    def test_meta_has_name(self) -> None:
        assert meta["name"] == "pr-review"

    def test_meta_has_description(self) -> None:
        assert "root cause" in meta["description"].lower()
        assert "pr" in meta["description"].lower()

    def test_meta_has_argument_hint(self) -> None:
        assert "--focus" in meta["argument_hint"]
