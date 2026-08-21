"""Tier 3: Workflow definition tests — verify all workflows pass validation."""

from __future__ import annotations

from collections import defaultdict, deque

import pytest

from factory.models import ProjectState
from factory.workflow.definitions import (
    DOC_FRESHNESS_GATE_PROMPT,
    _GRAPH_EXPLORER_PROMPT,  # noqa: F401
    _graph_explorer_prompt,  # noqa: F401
    _study_subgraph,  # noqa: F401
    build_workflow,
    create_workflow,
    design_workflow,
    register_all,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Study,
    VerdictType,
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
        # HAS_FACTORY now fires for design mode
        assert wf.trigger(ProjectState.HAS_FACTORY, {"interactive": True})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"interactive": False})


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
        """W₂ shares all build node IDs with W₁, plus gate_has_factory, discover, and study subgraph."""
        w1 = build_workflow()
        w2 = design_workflow()

        w1_ids = set(w1.nodes.keys())
        w2_ids = set(w2.nodes.keys())

        # Design has extra nodes: gate_has_factory, discover, bootstrap, and study subgraph
        assert w2_ids == w1_ids | {
            "gate_has_factory",
            "discover",
            "gate_factory_md_exists",
            "create_factory_md",
            "factory_init",
            "graph_update",
            "study",
            "graph_explorer",
            "concat_study",
        }

    def test_design_name(self) -> None:
        wf = design_workflow()
        assert wf.name == "design"


# ── Design study node tests ──────────────────────────────────────


class TestDesignStudyNode:
    """Verify design mode's conditional study path for existing projects."""

    def test_design_has_study_node(self) -> None:
        """Design workflow must contain a study node."""
        wf = design_workflow()
        assert "study" in wf.nodes
        assert isinstance(wf.nodes["study"], Study)

    def test_design_has_gate_has_factory(self) -> None:
        """Design workflow must contain the gate_has_factory conditional gate."""
        wf = design_workflow()
        assert "gate_has_factory" in wf.nodes
        gate = wf.nodes["gate_has_factory"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "fn"

    def test_design_study_writes_observations(self) -> None:
        """Study node must write observations.md."""
        wf = design_workflow()
        study = wf.nodes["study"]
        assert ".factory/strategy/observations.md" in study.writes

    def test_design_concat_study_to_fork_research_edge(self) -> None:
        """There must be an unconditional edge from concat_study to fork_research."""
        wf = design_workflow()
        assert any(
            e.source == "concat_study" and e.target == "fork_research" and e.condition is None
            for e in wf.edges
        )

    def test_design_gate_routes_to_graph_update(self) -> None:
        """gate_has_factory PROCEED must route to graph_update."""
        wf = design_workflow()
        assert any(
            e.source == "gate_has_factory"
            and e.target == "graph_update"
            and e.condition == VerdictType.PROCEED
            for e in wf.edges
        )

    def test_design_gate_routes_to_discover(self) -> None:
        """gate_has_factory HALT must route to discover (not fork_research)."""
        wf = design_workflow()
        assert any(
            e.source == "gate_has_factory"
            and e.target == "discover"
            and e.condition == VerdictType.HALT
            for e in wf.edges
        )

    def test_design_has_discover_node(self) -> None:
        """Design workflow must contain a discover FnNode."""
        wf = design_workflow()
        assert "discover" in wf.nodes
        node = wf.nodes["discover"]
        assert isinstance(node, FnNode)
        assert node.command == "factory discover {project_path}"
        assert ".factory/eval_profile.json" in node.writes

    def test_design_discover_to_bootstrap_edge(self) -> None:
        """Discover chains through bootstrap (factory.md gate + init) before graph_update."""
        wf = design_workflow()
        assert any(
            e.source == "discover" and e.target == "gate_factory_md_exists" and e.condition is None
            for e in wf.edges
        )
        assert any(
            e.source == "factory_init" and e.target == "graph_update" and e.condition is None
            for e in wf.edges
        )


# ── Agent pool assignments ───────────────────────────────────────


class TestAgentPool:
    def test_default_pool_models(self) -> None:
        from factory.workflow.primitives import DEFAULT_AGENT_POOL

        expected = {
            "researcher": "sonnet",
            "strategist": "opus",
            "builder": "opus",
            "health_checker": "opus",
            "code_reviewer": "opus",
            "adversarial_tester": "opus",
            "failure_analyst": "opus",
            "ceo": "opus",
            "archivist": "haiku",
            "refiner": "opus",
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
        required = {"design", "create", "spec-generate"}
        assert required.issubset(set(all_wf.keys())), f"Missing: {required - set(all_wf.keys())}"

    def test_all_validate(self) -> None:
        all_wf = register_all()
        for name, wf in all_wf.items():
            issues = wf.validate_graph()
            assert issues == [], f"{name} has validation issues: {issues}"


class TestDesignStudySubgraph:
    def test_graph_nodes_exist(self) -> None:
        wf = design_workflow()
        assert "graph_update" in wf.nodes
        assert "study" in wf.nodes
        assert "graph_explorer" in wf.nodes
        assert "concat_study" in wf.nodes

    def test_edge_wiring(self) -> None:
        wf = design_workflow()
        assert any(e.source == "graph_update" and e.target == "study" for e in wf.edges)
        assert any(e.source == "study" and e.target == "graph_explorer" for e in wf.edges)
        assert any(e.source == "graph_explorer" and e.target == "concat_study" for e in wf.edges)
        assert any(e.source == "concat_study" and e.target == "fork_research" for e in wf.edges)

    def test_graph_update_is_fn_node(self) -> None:
        wf = design_workflow()
        node = wf.nodes["graph_update"]
        assert isinstance(node, FnNode)
        assert "factory graph update" in node.command

    def test_graph_explorer_writes_context(self) -> None:
        wf = design_workflow()
        node = wf.nodes["graph_explorer"]
        assert ".factory/strategy/graph-context.md" in node.writes

    def test_concat_study_writes_combined(self) -> None:
        wf = design_workflow()
        node = wf.nodes["concat_study"]
        assert ".factory/strategy/study-combined.md" in node.writes


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
        """Create mode has the builder → deep-qa → gate loop."""
        wf = create_workflow()
        assert "builder" in wf.nodes
        assert "health_checker" in wf.nodes
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


# ── gate_doc_freshness ──────────────────────────────────────────


class TestDocFreshnessGate:
    @pytest.mark.parametrize(
        "workflow_fn",
        [build_workflow, create_workflow],
        ids=["build", "create"],
    )
    def test_gate_exists_as_gate_node(self, workflow_fn) -> None:
        wf = workflow_fn()
        assert "gate_doc_freshness" in wf.nodes
        gate = wf.nodes["gate_doc_freshness"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "agent"
        assert gate.evaluator_role == AgentRole.CEO

    @pytest.mark.parametrize(
        "workflow_fn",
        [build_workflow, create_workflow],
        ids=["build", "create"],
    )
    def test_gate_uses_shared_prompt(self, workflow_fn) -> None:
        wf = workflow_fn()
        gate = wf.nodes["gate_doc_freshness"]
        assert isinstance(gate, GateNode)
        assert gate.gate_prompt is DOC_FRESHNESS_GATE_PROMPT

    def test_design_inherits_gate(self) -> None:
        wf = design_workflow()
        assert "gate_doc_freshness" in wf.nodes
        assert isinstance(wf.nodes["gate_doc_freshness"], GateNode)

    @pytest.mark.parametrize(
        "workflow_fn",
        [build_workflow, create_workflow],
        ids=["build", "create"],
    )
    def test_edge_wiring(self, workflow_fn) -> None:
        wf = workflow_fn()
        edges = wf.edges
        assert any(
            e.source == "gate_qa"
            and e.target == "gate_doc_freshness"
            and e.condition == VerdictType.PROCEED
            for e in edges
        ), "missing gate_qa -> gate_doc_freshness PROCEED edge"
        assert any(
            e.source == "gate_doc_freshness"
            and e.target == "gate_precheck"
            and e.condition == VerdictType.PROCEED
            for e in edges
        ), "missing gate_doc_freshness -> gate_precheck PROCEED edge"
        assert any(
            e.source == "gate_doc_freshness"
            and e.target == "builder"
            and e.condition == VerdictType.RELOOP
            for e in edges
        ), "missing gate_doc_freshness -> builder RELOOP edge"


# ── Builder → QA reachability audit ────────────────────────────


def _workflows_with_builder() -> list[str]:
    """Return names of workflows containing a Builder AgentNode."""
    names = []
    for name, wf in register_all().items():
        if wf.terminal:
            continue
        has_builder = any(
            isinstance(n, AgentNode) and n.role == AgentRole.BUILDER for n in wf.nodes.values()
        )
        if has_builder:
            names.append(name)
    return sorted(names)


def _is_reachable(workflow_name: str, source_id: str, target_id: str) -> bool:
    """Check if target_id is reachable from source_id via forward edges + fork targets."""
    wf = register_all()[workflow_name]
    adj: dict[str, list[str]] = defaultdict(list)
    for edge in wf.edges:
        adj[edge.source].append(edge.target)
    for nid, node in wf.nodes.items():
        if isinstance(node, ForkNode):
            adj[nid].extend(node.targets)

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


DEEP_QA_ROLES = {AgentRole.HEALTH_CHECKER, AgentRole.CODE_REVIEWER, AgentRole.ADVERSARIAL_TESTER}


class TestBuilderQaReachability:
    """Every workflow with a Builder must also have a deep-qa specialist reachable from it."""

    @pytest.mark.parametrize("workflow_name", _workflows_with_builder())
    def test_builder_has_qa_node(self, workflow_name: str) -> None:
        wf = register_all()[workflow_name]
        qa_nodes = [
            nid
            for nid, n in wf.nodes.items()
            if isinstance(n, AgentNode) and n.role in DEEP_QA_ROLES
        ]
        assert qa_nodes, (
            f"workflow '{workflow_name}' has a Builder but no deep-qa specialist AgentNode"
        )

    @pytest.mark.parametrize("workflow_name", _workflows_with_builder())
    def test_qa_reachable_from_builder(self, workflow_name: str) -> None:
        wf = register_all()[workflow_name]
        builder_ids = [
            nid
            for nid, n in wf.nodes.items()
            if isinstance(n, AgentNode) and n.role == AgentRole.BUILDER
        ]
        qa_ids = [
            nid
            for nid, n in wf.nodes.items()
            if isinstance(n, AgentNode) and n.role in DEEP_QA_ROLES
        ]
        for bid in builder_ids:
            reachable = any(_is_reachable(workflow_name, bid, qid) for qid in qa_ids)
            assert reachable, (
                f"workflow '{workflow_name}': deep-qa specialist is not reachable from "
                f"Builder node '{bid}' via edges"
            )


# ── Deep-QA subgraph tests ────────────────────────────────────


DEEP_QA_NODE_IDS = {
    "fork_qa",
    "health_checker",
    "code_reviewer",
    "adversarial_tester",
    "join_qa",
}

DEEP_QA_WORKFLOWS = ["build", "create"]


def _get_workflow(name: str):
    return {
        "build": build_workflow,
        "create": create_workflow,
    }[name]()


class TestDeepQaSubgraph:
    """Verify the parallel deep-QA subgraph is correctly wired in surviving workflows."""

    @pytest.mark.parametrize("wf_name", DEEP_QA_WORKFLOWS)
    def test_deep_qa_present_in_all_workflows(self, wf_name: str) -> None:
        wf = _get_workflow(wf_name)
        for node_id in DEEP_QA_NODE_IDS:
            assert node_id in wf.nodes, f"workflow '{wf_name}' missing deep-qa node '{node_id}'"

    @pytest.mark.parametrize("wf_name", DEEP_QA_WORKFLOWS)
    def test_deep_qa_internal_edges(self, wf_name: str) -> None:
        wf = _get_workflow(wf_name)
        expected_edges = [
            ("fork_qa", "join_qa", None),
        ]
        edge_set = {(e.source, e.target, e.condition) for e in wf.edges}
        for src, tgt, cond in expected_edges:
            assert (src, tgt, cond) in edge_set, (
                f"workflow '{wf_name}' missing edge {src} → {tgt} ({cond})"
            )

    @pytest.mark.parametrize("wf_name", DEEP_QA_WORKFLOWS)
    def test_deep_qa_fork_targets(self, wf_name: str) -> None:
        wf = _get_workflow(wf_name)
        fork = wf.nodes["fork_qa"]
        assert isinstance(fork, ForkNode)
        assert set(fork.targets) == {"health_checker", "code_reviewer", "adversarial_tester"}

    @pytest.mark.parametrize("wf_name", DEEP_QA_WORKFLOWS)
    def test_deep_qa_no_redundant_nodes(self, wf_name: str) -> None:
        wf = _get_workflow(wf_name)
        for removed in ("gate_health", "gate_adversarial", "join_verdict"):
            assert removed not in wf.nodes, (
                f"workflow '{wf_name}' still has removed node '{removed}'"
            )

    @pytest.mark.parametrize("wf_name", DEEP_QA_WORKFLOWS)
    def test_gate_qa_reloop_preserved(self, wf_name: str) -> None:
        wf = _get_workflow(wf_name)
        reloop_edges = [
            e
            for e in wf.edges
            if e.source == "gate_qa" and e.target == "builder" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1, f"workflow '{wf_name}' missing gate_qa → builder RELOOP edge"

    @pytest.mark.parametrize("wf_name", DEEP_QA_WORKFLOWS)
    def test_no_monolithic_qa_node(self, wf_name: str) -> None:
        """Verify the old monolithic 'qa' AgentNode was removed."""
        wf = _get_workflow(wf_name)
        assert "qa" not in wf.nodes or not isinstance(wf.nodes.get("qa"), AgentNode), (
            f"workflow '{wf_name}' still has monolithic 'qa' AgentNode"
        )


class TestContributedWorkflows:
    def test_register_all_includes_contributed(self) -> None:
        """register_all() returns contributed benchmark workflows."""
        workflows = register_all()
        assert "swebench" in workflows
        assert "legacybench" in workflows

    def test_contributed_workflows_valid(self) -> None:
        workflows = register_all()
        for name in ("swebench", "legacybench"):
            wf = workflows[name]
            issues = wf.validate_graph()
            assert issues == [], f"{name} workflow has issues: {issues}"


# ── Terminal flag defaults ──────────────────────────────────────


class TestTerminalFlagDefaults:
    """Standard workflows default to terminal=False."""

    def test_build_not_terminal(self) -> None:
        assert build_workflow().terminal is False

    def test_design_is_terminal(self) -> None:
        assert design_workflow().terminal is True


# ── _study_subgraph focus threading ─────────────────────────────


class TestStudySubgraphFocus:
    def test_focus_sets_study_node(self) -> None:
        nodes, _ = _study_subgraph(focus="auth")
        assert nodes["study"].focus == "auth"

    def test_focus_sets_graph_explorer_prompt(self) -> None:
        nodes, _ = _study_subgraph(focus="auth")
        assert "auth" in nodes["graph_explorer"].prompt_template

    def test_no_focus_backward_compatible(self) -> None:
        nodes, _ = _study_subgraph()
        assert nodes["study"].focus is None
        assert nodes["graph_explorer"].prompt_template == _GRAPH_EXPLORER_PROMPT

    def test_graph_explorer_prompt_with_focus(self) -> None:
        prompt = _graph_explorer_prompt("auth flow")
        assert "Focus your exploration on: auth flow" in prompt
        assert 'factory graph query "auth flow"' in prompt

    def test_graph_explorer_prompt_without_focus(self) -> None:
        assert _graph_explorer_prompt() == _GRAPH_EXPLORER_PROMPT
        assert _graph_explorer_prompt(None) == _GRAPH_EXPLORER_PROMPT
