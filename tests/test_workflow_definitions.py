"""Tier 3: Workflow definition tests — verify all 5 workflows pass validation."""

from __future__ import annotations


from factory.models import ProjectState
from factory.workflow.definitions import (
    build_workflow,
    design_workflow,
    improve_workflow,
    meta_workflow,
    register_all,
    research_workflow,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    ForkNode,
    GateNode,
)


# ── All workflows pass validation ────────────────────────────────


class TestAllWorkflowsValid:
    def test_build_valid(self) -> None:
        wf = build_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"build workflow has issues: {issues}"

    def test_design_valid(self) -> None:
        wf = design_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"design workflow has issues: {issues}"

    def test_improve_valid(self) -> None:
        wf = improve_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"improve workflow has issues: {issues}"

    def test_research_valid(self) -> None:
        wf = research_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"research workflow has issues: {issues}"

    def test_meta_valid(self) -> None:
        wf = meta_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"meta workflow has issues: {issues}"


# ── Triggers ─────────────────────────────────────────────────────


class TestTriggers:
    def test_build_trigger(self) -> None:
        wf = build_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.NO_REPO, {})
        assert wf.trigger(ProjectState.REPO_INCOMPLETE, {})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})

    def test_design_trigger(self) -> None:
        wf = design_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.NO_REPO, {"interactive": True})
        assert not wf.trigger(ProjectState.NO_REPO, {"interactive": False})
        assert not wf.trigger(ProjectState.NO_REPO, {})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"interactive": True})

    def test_improve_trigger(self) -> None:
        wf = improve_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {})
        assert not wf.trigger(ProjectState.NO_REPO, {})

    def test_research_trigger(self) -> None:
        wf = research_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"research_target": "accuracy"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})

    def test_meta_trigger(self) -> None:
        wf = meta_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "meta"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


# ── W₂ = W₁[gate_strategy ← user] ──────────────────────────────


class TestDesignIsBuiltWithUserGate:
    def test_design_strategy_gate_is_user(self) -> None:
        """W₂ differs from W₁ only at the strategy gate."""
        w1 = build_workflow()
        w2 = design_workflow()

        gate_w1 = w1.nodes.get("gate_strategy")
        gate_w2 = w2.nodes.get("gate_strategy")

        assert isinstance(gate_w1, GateNode)
        assert isinstance(gate_w2, GateNode)

        assert gate_w1.evaluator_type == "agent"
        assert gate_w2.evaluator_type == "user"

    def test_design_shares_other_nodes(self) -> None:
        """W₂ shares all other node IDs with W₁."""
        w1 = build_workflow()
        w2 = design_workflow()

        w1_ids = set(w1.nodes.keys())
        w2_ids = set(w2.nodes.keys())

        assert w1_ids == w2_ids

    def test_design_name(self) -> None:
        wf = design_workflow()
        assert wf.name == "design"


# ── W₄ structural delta from W₃ ─────────────────────────────────


class TestResearchExtendsImprove:
    def test_research_has_baseline(self) -> None:
        """W₄ replaces study with baseline measurement."""
        wf = research_workflow()
        assert "baseline" in wf.nodes
        assert "study" not in wf.nodes

    def test_research_has_failure_analyst(self) -> None:
        """W₄ has failure_analyst between baseline and researcher."""
        wf = research_workflow()
        assert "failure_analyst" in wf.nodes
        node = wf.nodes["failure_analyst"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.FAILURE_ANALYST

    def test_research_has_plateau_gate(self) -> None:
        """W₄ has plateau detection gate."""
        wf = research_workflow()
        assert "plateau_gate" in wf.nodes
        assert isinstance(wf.nodes["plateau_gate"], GateNode)

    def test_research_start_node(self) -> None:
        wf = research_workflow()
        assert wf.start_node == "baseline"


# ── W₅ Meta structure ────────────────────────────────────────────


class TestMetaStructure:
    def test_meta_has_insights(self) -> None:
        wf = meta_workflow()
        assert "insights" in wf.nodes
        assert isinstance(wf.nodes["insights"], FnNode)

    def test_meta_has_fork(self) -> None:
        wf = meta_workflow()
        assert "fork_post" in wf.nodes
        assert isinstance(wf.nodes["fork_post"], ForkNode)

    def test_meta_has_test_pruning(self) -> None:
        wf = meta_workflow()
        assert "test_collect" in wf.nodes
        assert "test_researcher" in wf.nodes
        assert "gate_test_prune" in wf.nodes
        assert "test_builder" in wf.nodes

    def test_meta_has_user_gates(self) -> None:
        wf = meta_workflow()
        gate_user = wf.nodes.get("gate_user")
        gate_test = wf.nodes.get("gate_test_prune")
        assert isinstance(gate_user, GateNode)
        assert isinstance(gate_test, GateNode)
        assert gate_user.evaluator_type == "user"
        assert gate_test.evaluator_type == "user"

    def test_meta_archivist_nonblocking(self) -> None:
        wf = meta_workflow()
        archivist = wf.nodes.get("archivist")
        assert archivist is not None
        assert archivist.blocking is False


# ── Agent pool assignments ───────────────────────────────────────


class TestAgentPool:
    def test_default_pool_models(self) -> None:
        from factory.workflow.primitives import DEFAULT_AGENT_POOL

        expected = {
            "researcher": "sonnet",
            "strategist": "opus",
            "builder": "opus",
            "reviewer": "opus",
            "evaluator": "opus",
            "failure_analyst": "opus",
            "ceo": "opus",
            "archivist": "haiku",
        }

        for role, model in expected.items():
            assert role in DEFAULT_AGENT_POOL, f"missing role: {role}"
            assert DEFAULT_AGENT_POOL[role].model == model, (
                f"wrong model for {role}: expected {model}, got {DEFAULT_AGENT_POOL[role].model}"
            )


# ── Register all ─────────────────────────────────────────────────


class TestRegisterAll:
    def test_all_five_workflows(self) -> None:
        all_wf = register_all()
        assert len(all_wf) == 5
        assert set(all_wf.keys()) == {"build", "design", "improve", "research", "meta"}

    def test_all_validate(self) -> None:
        all_wf = register_all()
        for name, wf in all_wf.items():
            issues = wf.validate_graph()
            assert issues == [], f"{name} has validation issues: {issues}"
