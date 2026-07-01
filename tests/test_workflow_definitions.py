"""Tier 3: Workflow definition tests — verify all workflows pass validation."""

from __future__ import annotations

from collections import defaultdict, deque

import pytest

from factory.models import ProjectState
from factory.workflow.definitions import (
    build_workflow,
    create_workflow,
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
    JoinNode,
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

    def test_meta_archivist_chains_to_test(self) -> None:
        """Archivist (non-blocking) chains directly to test_collect."""
        wf = meta_workflow()
        edges_from_archivist = [e for e in wf.edges if e.source == "archivist"]
        assert any(e.target == "test_collect" for e in edges_from_archivist)

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
            "qa": "opus",
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
    def test_all_workflows_registered(self) -> None:
        all_wf = register_all()
        assert len(all_wf) >= 11, f"Expected at least 11 workflows, got {len(all_wf)}"
        required = {"build", "design", "improve", "qa", "research", "meta",
                     "discover", "review", "refine", "create", "skill-refine"}
        assert required.issubset(set(all_wf.keys())), f"Missing: {required - set(all_wf.keys())}"

    def test_all_validate(self) -> None:
        all_wf = register_all()
        for name, wf in all_wf.items():
            issues = wf.validate_graph()
            assert issues == [], f"{name} has validation issues: {issues}"


# ── W₉ Create structure ────────────────────────────────────────


class TestCreateStructure:
    def test_create_valid(self) -> None:
        wf = create_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"create workflow has issues: {issues}"

    def test_create_trigger(self) -> None:
        wf = create_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "create"})
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "create"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})

    def test_create_name(self) -> None:
        wf = create_workflow()
        assert wf.name == "create"

    def test_create_has_parallel_research(self) -> None:
        wf = create_workflow()
        assert "fork_research" in wf.nodes
        assert "join_research" in wf.nodes
        fork = wf.nodes["fork_research"]
        assert isinstance(fork, ForkNode)
        assert len(fork.targets) == 3
        join = wf.nodes["join_research"]
        assert isinstance(join, JoinNode)
        assert len(join.sources) == 3

    def test_create_has_user_gate(self) -> None:
        """Create mode has a user approval gate at strategy."""
        wf = create_workflow()
        gate = wf.nodes.get("gate_strategy")
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "user"

    def test_create_has_builder_qa_loop(self) -> None:
        """Create mode has the standard builder → QA → gate loop."""
        wf = create_workflow()
        assert "builder" in wf.nodes
        assert "qa" in wf.nodes
        assert "gate_qa" in wf.nodes
        assert "gate_build" in wf.nodes
        reloop_edges = [e for e in wf.edges if e.source == "gate_qa" and e.target == "builder"]
        assert len(reloop_edges) == 1

    def test_create_has_precheck(self) -> None:
        wf = create_workflow()
        assert "gate_precheck" in wf.nodes
        precheck = wf.nodes["gate_precheck"]
        assert isinstance(precheck, GateNode)
        assert precheck.evaluator_type == "fn"

    def test_create_archivists_nonblocking(self) -> None:
        wf = create_workflow()
        for nid in ("archivist_plan", "archivist_build"):
            node = wf.nodes.get(nid)
            assert node is not None, f"missing {nid}"
            assert node.blocking is False

    def test_create_start_node(self) -> None:
        wf = create_workflow()
        assert wf.start_node == "fork_research"

    def test_create_skill_export(self) -> None:
        from factory.workflow.skill_export import validate_skill, workflow_to_skill_md

        wf = create_workflow()
        skill_md = workflow_to_skill_md(wf)
        issues = validate_skill(skill_md)
        assert issues == [], f"create skill has issues: {issues}"
        assert "workflow-create" in skill_md
        assert "User Approval" in skill_md


# ── Builder → QA reachability audit ────────────────────────────


def _workflows_with_builder() -> list[str]:
    """Return names of workflows containing a Builder AgentNode."""
    names = []
    for name, wf in register_all().items():
        has_builder = any(
            isinstance(n, AgentNode) and n.role == AgentRole.BUILDER
            for n in wf.nodes.values()
        )
        if has_builder:
            names.append(name)
    return sorted(names)


def _is_reachable(workflow_name: str, source_id: str, target_id: str) -> bool:
    """Check if target_id is reachable from source_id via forward edges."""
    wf = register_all()[workflow_name]
    adj: dict[str, list[str]] = defaultdict(list)
    for edge in wf.edges:
        adj[edge.source].append(edge.target)

    visited: set[str] = set()
    queue: deque[str] = deque([source_id])
    while queue:
        nid = queue.popleft()
        if nid == target_id:
            return True
        if nid in visited:
            continue
        visited.add(nid)
        queue.extend(adj.get(nid, []))
    return False


class TestBuilderQaReachability:
    """Every workflow with a Builder must also have a QA node reachable from it."""

    @pytest.mark.parametrize("workflow_name", _workflows_with_builder())
    def test_builder_has_qa_node(self, workflow_name: str) -> None:
        wf = register_all()[workflow_name]
        qa_nodes = [
            nid for nid, n in wf.nodes.items()
            if isinstance(n, AgentNode) and n.role == AgentRole.QA
        ]
        assert qa_nodes, (
            f"workflow '{workflow_name}' has a Builder but no QA AgentNode"
        )

    @pytest.mark.parametrize("workflow_name", _workflows_with_builder())
    def test_qa_reachable_from_builder(self, workflow_name: str) -> None:
        wf = register_all()[workflow_name]
        builder_ids = [
            nid for nid, n in wf.nodes.items()
            if isinstance(n, AgentNode) and n.role == AgentRole.BUILDER
        ]
        qa_ids = [
            nid for nid, n in wf.nodes.items()
            if isinstance(n, AgentNode) and n.role == AgentRole.QA
        ]
        for bid in builder_ids:
            reachable = any(
                _is_reachable(workflow_name, bid, qid) for qid in qa_ids
            )
            assert reachable, (
                f"workflow '{workflow_name}': QA node is not reachable from "
                f"Builder node '{bid}' via edges"
            )
