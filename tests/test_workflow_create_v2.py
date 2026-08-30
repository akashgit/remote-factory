"""Tests for the create-v2 workflow — inference-time scaling for workflow creation."""

from __future__ import annotations

import pytest

from factory.workflow.contributed.create_v2 import meta as create_v2_meta
from factory.workflow.contributed.create_v2 import workflow as create_v2_workflow
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    VerdictType,
)


@pytest.fixture(scope="module")
def create_v2_module():
    class _Module:
        meta = create_v2_meta
        workflow = staticmethod(create_v2_workflow)
    return _Module()


@pytest.fixture(scope="module")
def create_v2_wf():
    return create_v2_workflow()


# ── Module-level metadata ──────────────────────────────────────


class TestMeta:
    def test_meta_has_name(self, create_v2_module) -> None:
        assert "name" in create_v2_module.meta
        assert create_v2_module.meta["name"] == "create-v2"

    def test_meta_has_description(self, create_v2_module) -> None:
        assert "description" in create_v2_module.meta
        assert len(create_v2_module.meta["description"]) > 0


# ── Graph structure ────────────────────────────────────────────


class TestGraphStructure:
    def test_node_count(self, create_v2_wf) -> None:
        assert len(create_v2_wf.nodes) == 29

    def test_edge_count(self, create_v2_wf) -> None:
        assert len(create_v2_wf.edges) == 33

    def test_workflow_name(self, create_v2_wf) -> None:
        assert create_v2_wf.name == "create-v2"

    def test_start_node(self, create_v2_wf) -> None:
        assert create_v2_wf.start_node == "init_user_intent"

    def test_terminal(self, create_v2_wf) -> None:
        assert create_v2_wf.terminal is True

    def test_validates(self, create_v2_wf) -> None:
        issues = create_v2_wf.validate_graph()
        assert issues == [], f"create-v2 workflow has issues: {issues}"


# ── Key nodes present ──────────────────────────────────────────


class TestKeyNodesPresent:
    @pytest.mark.parametrize(
        "node_id",
        [
            "init_user_intent",
            "gate_has_factory",
            "discover",
            "gate_factory_md_exists",
            "create_factory_md",
            "factory_init",
            "graph_update",
            "study",
            "graph_explorer",
            "concat_study",
            "research_director",
            "strategy_director",
            "synthesize_strategy",
            "gate_strategy",
            "archivist_plan",
            "builder",
            "gate_build",
            "fork_qa",
            "health_checker",
            "code_reviewer",
            "qa_director",
            "join_qa",
            "synthesize_qa",
            "gate_qa",
            "overwatch",
            "gate_overwatch",
            "gate_doc_freshness",
            "gate_precheck",
            "archivist_build",
        ],
    )
    def test_node_exists(self, create_v2_wf, node_id: str) -> None:
        assert node_id in create_v2_wf.nodes, f"missing node: {node_id}"


# ── Removed nodes absent ──────────────────────────────────────


class TestRemovedNodesAbsent:
    @pytest.mark.parametrize(
        "node_id",
        [
            "fork_research",
            "researcher_existing",
            "researcher_intent",
            "researcher_practices",
            "join_research",
            "gate_research",
            "strategist",
            "adversarial_tester",
        ],
    )
    def test_node_removed(self, create_v2_wf, node_id: str) -> None:
        assert node_id not in create_v2_wf.nodes, f"node should be removed: {node_id}"


# ── Node types and properties ─────────────────────────────────


class TestNodeProperties:
    # ── Directors ──

    def test_research_director_is_ceo(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["research_director"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.CEO
        assert node.timeout == 3600

    def test_strategy_director_is_ceo(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["strategy_director"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.CEO
        assert node.timeout == 3600

    def test_qa_director_is_ceo(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["qa_director"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.CEO
        assert node.timeout == 3600

    def test_overwatch_is_ceo(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["overwatch"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.CEO
        assert node.timeout == 1800

    # ── Synthesizers ──

    def test_synthesize_strategy_is_strategist(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["synthesize_strategy"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_synthesize_qa_is_fn_node(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["synthesize_qa"]
        assert isinstance(node, FnNode)
        assert ".factory/reviews/qa-synthesized.md" in node.writes

    def test_init_user_intent_is_fn_node(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["init_user_intent"]
        assert isinstance(node, FnNode)
        assert ".factory/strategy/user-intent.md" in node.writes

    # ── Gates ──

    def test_gate_strategy_is_user(self, create_v2_wf) -> None:
        gate = create_v2_wf.nodes["gate_strategy"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "user"

    def test_gate_strategy_reads_user_intent(self, create_v2_wf) -> None:
        gate = create_v2_wf.nodes["gate_strategy"]
        assert ".factory/strategy/user-intent.md" in gate.reads

    def test_gate_strategy_has_gate_prompt(self, create_v2_wf) -> None:
        gate = create_v2_wf.nodes["gate_strategy"]
        assert len(gate.gate_prompt) > 0

    def test_gate_qa_is_agent(self, create_v2_wf) -> None:
        gate = create_v2_wf.nodes["gate_qa"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "agent"
        assert gate.evaluator_role == AgentRole.CEO

    def test_gate_overwatch_is_agent(self, create_v2_wf) -> None:
        gate = create_v2_wf.nodes["gate_overwatch"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "agent"
        assert gate.evaluator_role == AgentRole.CEO

    # ── Fork/Join ──

    def test_fork_qa_targets(self, create_v2_wf) -> None:
        fork = create_v2_wf.nodes["fork_qa"]
        assert isinstance(fork, ForkNode)
        assert set(fork.targets) == {"health_checker", "code_reviewer", "qa_director"}

    def test_join_qa_sources(self, create_v2_wf) -> None:
        join = create_v2_wf.nodes["join_qa"]
        assert isinstance(join, JoinNode)
        assert set(join.sources) == {"health_checker", "code_reviewer", "qa_director"}

    def test_fork_qa_targets_equal_join_qa_sources(self, create_v2_wf) -> None:
        fork = create_v2_wf.nodes["fork_qa"]
        join = create_v2_wf.nodes["join_qa"]
        assert set(fork.targets) == set(join.sources)

    # ── Archivists ──

    def test_archivist_plan_non_blocking(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["archivist_plan"]
        assert isinstance(node, AgentNode)
        assert node.blocking is False

    def test_archivist_build_non_blocking(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["archivist_build"]
        assert isinstance(node, AgentNode)
        assert node.blocking is False

    # ── Builder (inherited) ──

    def test_builder_has_3mode_prompt(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert "Plugin" in node.prompt_template or "plugin" in node.prompt_template.lower()
        assert "update" in node.prompt_template.lower() or "Update" in node.prompt_template

    def test_gate_build_has_3mode_validation(self, create_v2_wf) -> None:
        gate = create_v2_wf.nodes["gate_build"]
        assert isinstance(gate, GateNode)
        assert "plugin" in gate.gate_prompt.lower() or "Plugin" in gate.gate_prompt


# ── Reads/Writes ──────────────────────────────────────────────


class TestReadsWrites:
    def test_research_director_reads(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["research_director"]
        assert ".factory/strategy/study-combined.md" in node.reads
        assert ".factory/strategy/user-intent.md" in node.reads

    def test_research_director_writes(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["research_director"]
        assert ".factory/strategy/research-plan.json" in node.writes

    def test_strategy_director_reads(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["strategy_director"]
        assert ".factory/strategy/research-plan.json" in node.reads
        assert ".factory/strategy/user-intent.md" in node.reads
        assert ".factory/strategy/study-combined.md" in node.reads

    def test_strategy_director_writes(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["strategy_director"]
        assert ".factory/strategy/strategy-plan.json" in node.writes

    def test_synthesize_strategy_reads(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["synthesize_strategy"]
        assert ".factory/strategy/user-intent.md" in node.reads
        assert ".factory/strategy/strategy-plan.json" in node.reads

    def test_synthesize_strategy_writes(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["synthesize_strategy"]
        assert ".factory/strategy/current.md" in node.writes

    def test_qa_director_reads(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["qa_director"]
        assert ".factory/strategy/current.md" in node.reads
        assert ".factory/strategy/user-intent.md" in node.reads
        assert ".factory/reviews/builder-latest.md" in node.reads

    def test_qa_director_writes(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["qa_director"]
        assert ".factory/reviews/qa-plan.json" in node.writes

    def test_overwatch_reads(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["overwatch"]
        expected = {
            ".factory/strategy/user-intent.md",
            ".factory/strategy/current.md",
            ".factory/reviews/builder-latest.md",
            ".factory/reviews/qa-synthesized.md",
            ".factory/reviews/health-check.md",
            ".factory/reviews/code-review.md",
        }
        assert node.reads == expected

    def test_overwatch_writes(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["overwatch"]
        assert ".factory/reviews/overwatch-latest.md" in node.writes

    def test_gate_qa_reads_user_intent(self, create_v2_wf) -> None:
        gate = create_v2_wf.nodes["gate_qa"]
        assert ".factory/strategy/user-intent.md" in gate.reads
        assert ".factory/reviews/qa-synthesized.md" in gate.reads

    def test_gate_overwatch_reads(self, create_v2_wf) -> None:
        gate = create_v2_wf.nodes["gate_overwatch"]
        assert ".factory/reviews/overwatch-latest.md" in gate.reads
        assert ".factory/strategy/user-intent.md" in gate.reads


# ── Intent fidelity chain ─────────────────────────────────────


class TestIntentFidelity:
    @pytest.mark.parametrize(
        "node_id",
        [
            "strategy_director",
            "synthesize_strategy",
            "gate_strategy",
            "gate_qa",
            "overwatch",
        ],
    )
    def test_downstream_reads_user_intent(self, create_v2_wf, node_id: str) -> None:
        node = create_v2_wf.nodes[node_id]
        assert ".factory/strategy/user-intent.md" in node.reads, (
            f"{node_id} must read user-intent.md for intent fidelity"
        )

    def test_init_user_intent_writes_ledger(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["init_user_intent"]
        assert ".factory/strategy/user-intent.md" in node.writes

    def test_init_user_intent_is_start_node(self, create_v2_wf) -> None:
        assert create_v2_wf.start_node == "init_user_intent"


# ── Edge wiring ────────────────────────────────────────────────


class TestEdgeWiring:
    def _edge_set(self, wf):
        return {(e.source, e.target, e.condition) for e in wf.edges}

    # ── Init → bootstrap ──

    def test_init_user_intent_to_gate_has_factory(self, create_v2_wf) -> None:
        assert ("init_user_intent", "gate_has_factory", None) in self._edge_set(create_v2_wf)

    def test_gate_has_factory_proceed_to_graph_update(self, create_v2_wf) -> None:
        assert (
            "gate_has_factory", "graph_update", VerdictType.PROCEED
        ) in self._edge_set(create_v2_wf)

    def test_gate_has_factory_halt_to_discover(self, create_v2_wf) -> None:
        assert (
            "gate_has_factory", "discover", VerdictType.HALT
        ) in self._edge_set(create_v2_wf)

    def test_discover_to_gate_factory_md_exists(self, create_v2_wf) -> None:
        assert ("discover", "gate_factory_md_exists", None) in self._edge_set(create_v2_wf)

    def test_gate_factory_md_proceed_to_factory_init(self, create_v2_wf) -> None:
        assert (
            "gate_factory_md_exists", "factory_init", VerdictType.PROCEED
        ) in self._edge_set(create_v2_wf)

    def test_gate_factory_md_halt_to_create_factory_md(self, create_v2_wf) -> None:
        assert (
            "gate_factory_md_exists", "create_factory_md", VerdictType.HALT
        ) in self._edge_set(create_v2_wf)

    def test_create_factory_md_to_factory_init(self, create_v2_wf) -> None:
        assert ("create_factory_md", "factory_init", None) in self._edge_set(create_v2_wf)

    def test_factory_init_to_graph_update(self, create_v2_wf) -> None:
        assert ("factory_init", "graph_update", None) in self._edge_set(create_v2_wf)

    # ── Study subgraph ──

    def test_graph_update_to_study(self, create_v2_wf) -> None:
        assert ("graph_update", "study", None) in self._edge_set(create_v2_wf)

    def test_study_to_graph_explorer(self, create_v2_wf) -> None:
        assert ("study", "graph_explorer", None) in self._edge_set(create_v2_wf)

    def test_graph_explorer_to_concat_study(self, create_v2_wf) -> None:
        assert ("graph_explorer", "concat_study", None) in self._edge_set(create_v2_wf)

    # ── Research → Strategy → Gate ──

    def test_concat_study_to_research_director(self, create_v2_wf) -> None:
        assert ("concat_study", "research_director", None) in self._edge_set(create_v2_wf)

    def test_research_director_to_strategy_director(self, create_v2_wf) -> None:
        assert ("research_director", "strategy_director", None) in self._edge_set(create_v2_wf)

    def test_strategy_director_to_synthesize_strategy(self, create_v2_wf) -> None:
        assert ("strategy_director", "synthesize_strategy", None) in self._edge_set(create_v2_wf)

    def test_synthesize_strategy_to_gate_strategy(self, create_v2_wf) -> None:
        assert ("synthesize_strategy", "gate_strategy", None) in self._edge_set(create_v2_wf)

    def test_gate_strategy_reloop_to_strategy_director(self, create_v2_wf) -> None:
        assert (
            "gate_strategy", "strategy_director", VerdictType.RELOOP,
        ) in self._edge_set(create_v2_wf)

    def test_gate_strategy_proceed_to_archivist_plan(self, create_v2_wf) -> None:
        assert (
            "gate_strategy", "archivist_plan", VerdictType.PROCEED,
        ) in self._edge_set(create_v2_wf)

    # ── Build ──

    def test_archivist_plan_to_builder(self, create_v2_wf) -> None:
        assert ("archivist_plan", "builder", None) in self._edge_set(create_v2_wf)

    def test_builder_to_gate_build(self, create_v2_wf) -> None:
        assert ("builder", "gate_build", None) in self._edge_set(create_v2_wf)

    def test_gate_build_proceed_to_fork_qa(self, create_v2_wf) -> None:
        assert (
            "gate_build", "fork_qa", VerdictType.PROCEED,
        ) in self._edge_set(create_v2_wf)

    def test_gate_build_reloop_to_builder(self, create_v2_wf) -> None:
        assert (
            "gate_build", "builder", VerdictType.RELOOP,
        ) in self._edge_set(create_v2_wf)

    # ── QA ──

    def test_fork_qa_to_join_qa(self, create_v2_wf) -> None:
        assert ("fork_qa", "join_qa", None) in self._edge_set(create_v2_wf)

    def test_join_qa_to_synthesize_qa(self, create_v2_wf) -> None:
        assert ("join_qa", "synthesize_qa", None) in self._edge_set(create_v2_wf)

    def test_synthesize_qa_to_gate_qa(self, create_v2_wf) -> None:
        assert ("synthesize_qa", "gate_qa", None) in self._edge_set(create_v2_wf)

    def test_gate_qa_proceed_to_overwatch(self, create_v2_wf) -> None:
        assert (
            "gate_qa", "overwatch", VerdictType.PROCEED,
        ) in self._edge_set(create_v2_wf)

    def test_gate_qa_reloop_to_builder(self, create_v2_wf) -> None:
        assert (
            "gate_qa", "builder", VerdictType.RELOOP,
        ) in self._edge_set(create_v2_wf)

    # ── Overwatch ──

    def test_overwatch_to_gate_overwatch(self, create_v2_wf) -> None:
        assert ("overwatch", "gate_overwatch", None) in self._edge_set(create_v2_wf)

    def test_gate_overwatch_proceed_to_doc_freshness(self, create_v2_wf) -> None:
        assert (
            "gate_overwatch", "gate_doc_freshness", VerdictType.PROCEED,
        ) in self._edge_set(create_v2_wf)

    def test_gate_overwatch_reloop_to_builder(self, create_v2_wf) -> None:
        assert (
            "gate_overwatch", "builder", VerdictType.RELOOP,
        ) in self._edge_set(create_v2_wf)

    # ── Finalization ──

    def test_gate_doc_freshness_proceed_to_precheck(self, create_v2_wf) -> None:
        assert (
            "gate_doc_freshness", "gate_precheck", VerdictType.PROCEED,
        ) in self._edge_set(create_v2_wf)

    def test_gate_doc_freshness_reloop_to_builder(self, create_v2_wf) -> None:
        assert (
            "gate_doc_freshness", "builder", VerdictType.RELOOP,
        ) in self._edge_set(create_v2_wf)

    def test_gate_precheck_proceed_to_archivist_build(self, create_v2_wf) -> None:
        assert (
            "gate_precheck", "archivist_build", VerdictType.PROCEED,
        ) in self._edge_set(create_v2_wf)

    def test_gate_precheck_halt_to_archivist_build(self, create_v2_wf) -> None:
        assert (
            "gate_precheck", "archivist_build", VerdictType.HALT,
        ) in self._edge_set(create_v2_wf)

    # ── Negative: old edges must not exist ──

    def test_no_old_join_qa_to_gate_qa_edge(self, create_v2_wf) -> None:
        direct = [
            e for e in create_v2_wf.edges
            if e.source == "join_qa" and e.target == "gate_qa"
        ]
        assert direct == [], "old direct join_qa -> gate_qa edge should be removed"

    def test_no_old_gate_qa_to_doc_freshness_edge(self, create_v2_wf) -> None:
        direct = [
            e for e in create_v2_wf.edges
            if e.source == "gate_qa" and e.target == "gate_doc_freshness"
        ]
        assert direct == [], "old gate_qa -> gate_doc_freshness edge should be removed"

    def test_no_edges_referencing_removed_nodes(self, create_v2_wf) -> None:
        removed = {
            "fork_research", "researcher_existing", "researcher_intent",
            "researcher_practices", "join_research", "gate_research",
            "strategist", "adversarial_tester",
        }
        for e in create_v2_wf.edges:
            assert e.source not in removed, f"edge source references removed node: {e.source}"
            assert e.target not in removed, f"edge target references removed node: {e.target}"


# ── Post checks on director nodes ─────────────────────────────


class TestPostChecks:
    def test_research_director_post_check(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["research_director"]
        assert len(node.post_checks) == 1
        assert node.post_checks[0].path == ".factory/strategy/research-plan.json"
        assert node.post_checks[0].must_exist is True
        assert node.post_checks[0].min_size == 20

    def test_strategy_director_post_check(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["strategy_director"]
        assert len(node.post_checks) == 1
        assert node.post_checks[0].path == ".factory/strategy/strategy-plan.json"
        assert node.post_checks[0].must_exist is True
        assert node.post_checks[0].min_size == 20

    def test_synthesize_strategy_post_check(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["synthesize_strategy"]
        checks = node.post_checks
        assert len(checks) == 1
        assert checks[0].path == ".factory/strategy/current.md"
        assert checks[0].min_size == 200
        assert "### Graph Topology" in checks[0].must_contain
        assert "### Node Definitions" in checks[0].must_contain

    def test_qa_director_post_check(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["qa_director"]
        assert len(node.post_checks) == 1
        assert node.post_checks[0].path == ".factory/reviews/qa-plan.json"
        assert node.post_checks[0].must_exist is True
        assert node.post_checks[0].min_size == 20

    def test_overwatch_post_check(self, create_v2_wf) -> None:
        node = create_v2_wf.nodes["overwatch"]
        assert len(node.post_checks) == 1
        assert node.post_checks[0].path == ".factory/reviews/overwatch-latest.md"
        assert node.post_checks[0].must_exist is True
        assert node.post_checks[0].min_size == 100


# ── Prompt content ─────────────────────────────────────────────


class TestPromptContent:
    def test_qa_director_has_workflow_validate(self) -> None:
        from factory.workflow.contributed.create_v2.prompts import CREATE_QA_DIRECTOR_PROMPT
        assert "workflow-validate" in CREATE_QA_DIRECTOR_PROMPT
        assert "factory workflow validate" in CREATE_QA_DIRECTOR_PROMPT

    def test_qa_director_has_cli_integration(self) -> None:
        from factory.workflow.contributed.create_v2.prompts import CREATE_QA_DIRECTOR_PROMPT
        assert "cli-integration" in CREATE_QA_DIRECTOR_PROMPT
        assert "factory workflow list" in CREATE_QA_DIRECTOR_PROMPT
        assert "factory workflow show" in CREATE_QA_DIRECTOR_PROMPT
        assert "factory workflow export-skills" in CREATE_QA_DIRECTOR_PROMPT

    def test_qa_director_has_plugin_mode_check(self) -> None:
        from factory.workflow.contributed.create_v2.prompts import CREATE_QA_DIRECTOR_PROMPT
        assert "Plugin" in CREATE_QA_DIRECTOR_PROMPT or "plugin" in CREATE_QA_DIRECTOR_PROMPT.lower()
        assert "register_plugin" in CREATE_QA_DIRECTOR_PROMPT or "pyproject.toml" in CREATE_QA_DIRECTOR_PROMPT

    def test_qa_director_has_project_local_mode_check(self) -> None:
        from factory.workflow.contributed.create_v2.prompts import CREATE_QA_DIRECTOR_PROMPT
        assert "Project-local" in CREATE_QA_DIRECTOR_PROMPT or "project-local" in CREATE_QA_DIRECTOR_PROMPT.lower()

    def test_overwatch_has_4_steps(self) -> None:
        from factory.workflow.contributed.create_v2.prompts import CREATE_OVERWATCH_PROMPT
        assert "STEP 1" in CREATE_OVERWATCH_PROMPT
        assert "STEP 2" in CREATE_OVERWATCH_PROMPT
        assert "STEP 3" in CREATE_OVERWATCH_PROMPT
        assert "STEP 4" in CREATE_OVERWATCH_PROMPT

    def test_overwatch_has_spot_check(self) -> None:
        from factory.workflow.contributed.create_v2.prompts import CREATE_OVERWATCH_PROMPT
        assert "factory workflow validate" in CREATE_OVERWATCH_PROMPT
        assert "SKILL.md" in CREATE_OVERWATCH_PROMPT
        assert "factory workflow list" in CREATE_OVERWATCH_PROMPT

    def test_overwatch_has_intent_coverage_table(self) -> None:
        from factory.workflow.contributed.create_v2.prompts import CREATE_OVERWATCH_PROMPT
        assert "Intent Coverage" in CREATE_OVERWATCH_PROMPT

    def test_synthesize_strategy_requires_graph_topology(self) -> None:
        from factory.workflow.contributed.create_v2.prompts import CREATE_SYNTHESIZE_STRATEGY_PROMPT
        assert "### Graph Topology" in CREATE_SYNTHESIZE_STRATEGY_PROMPT
        assert "### Node Definitions" in CREATE_SYNTHESIZE_STRATEGY_PROMPT

    def test_gate_strategy_prompt_has_intent_fidelity(self) -> None:
        from factory.workflow.contributed.create_v2.prompts import CREATE_GATE_STRATEGY_PROMPT
        assert "INTENT FIDELITY" in CREATE_GATE_STRATEGY_PROMPT
        assert "user-intent.md" in CREATE_GATE_STRATEGY_PROMPT

    def test_all_8_prompts_importable(self) -> None:
        from factory.workflow.contributed.create_v2 import prompts
        prompt_names = [
            "CREATE_RESEARCH_DIRECTOR_PROMPT",
            "CREATE_STRATEGY_DIRECTOR_PROMPT",
            "CREATE_SYNTHESIZE_STRATEGY_PROMPT",
            "CREATE_QA_DIRECTOR_PROMPT",
            "CREATE_OVERWATCH_PROMPT",
            "CREATE_GATE_OVERWATCH_PROMPT",
            "CREATE_GATE_QA_PROMPT",
            "CREATE_GATE_STRATEGY_PROMPT",
        ]
        for name in prompt_names:
            val = getattr(prompts, name)
            assert isinstance(val, str)
            assert len(val) > 0, f"{name} is empty"


# ── Thin wrappers ─────────────────────────────────────────────


class TestThinWrappers:
    def test_intent_init_reexports_main(self) -> None:
        from factory.workflow.contributed.create_v2.intent_init import main
        from factory.workflow.contributed.design_v2.intent_init import main as original_main
        assert main is original_main

    def test_qa_synthesis_reexports_main(self) -> None:
        from factory.workflow.contributed.create_v2.qa_synthesis import main
        from factory.workflow.contributed.design_v2.qa_synthesis import main as original_main
        assert main is original_main


# ── Registration ──────────────────────────────────────────────


class TestRegistration:
    def test_in_register_all(self) -> None:
        from factory.workflow.definitions import register_all
        workflows = register_all()
        assert "create-v2" in workflows

    def test_registered_workflow_validates(self) -> None:
        from factory.workflow.definitions import register_all
        workflows = register_all()
        wf = workflows["create-v2"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered create-v2 has issues: {issues}"

    def test_in_builtin_registry(self) -> None:
        from factory.workflow.definitions import _get_builtin_registry
        registry = _get_builtin_registry()
        assert "create-v2" in registry

    def test_in_ceo_modes(self) -> None:
        from factory.cli._helpers import CEO_MODES
        assert "create-v2" in CEO_MODES

    def test_in_workflow_meta(self) -> None:
        from factory.workflow.skill_export import WORKFLOW_META
        assert "create-v2" in WORKFLOW_META
        meta = WORKFLOW_META["create-v2"]
        assert "description" in meta
        assert len(meta["description"]) > 0
        assert "argument_hint" in meta

    def test_coexists_with_v1(self) -> None:
        from factory.workflow.definitions import register_all
        workflows = register_all()
        assert "create" in workflows
        assert "create-v2" in workflows
        assert workflows["create"].name == "create"
        assert workflows["create-v2"].name == "create-v2"


# ── Inherited node reads updated ──────────────────────────────


class TestInheritedReadsUpdated:
    @pytest.mark.parametrize(
        "node_id",
        ["gate_doc_freshness", "gate_precheck", "archivist_build"],
    )
    def test_reads_qa_synthesized_not_adversarial(self, create_v2_wf, node_id: str) -> None:
        node = create_v2_wf.nodes[node_id]
        assert ".factory/reviews/qa-synthesized.md" in node.reads
        assert ".factory/reviews/adversarial-qa.md" not in node.reads
