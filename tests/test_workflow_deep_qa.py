"""Tests for deep-qa workflow — decomposed QA with 3 sequential specialists."""

from __future__ import annotations

from collections import defaultdict, deque

from factory.models import ProjectState
from factory.workflow.definitions import deep_qa_workflow, register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
    VerdictType,
)


class TestDeepQaValid:
    """Graph validation — no structural issues."""

    def test_graph_validates(self) -> None:
        wf = deep_qa_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"deep-qa workflow has issues: {issues}"

    def test_no_cycles_except_reloop(self) -> None:
        """Deep-qa has no RELOOP edges, so the graph must be a DAG."""
        wf = deep_qa_workflow()
        reloop_edges = [e for e in wf.edges if e.condition == VerdictType.RELOOP]
        assert reloop_edges == [], "deep-qa must have no RELOOP edges"

        # Verify DAG property via topological sort attempt
        adj: dict[str, list[str]] = defaultdict(list)
        in_degree: dict[str, int] = {nid: 0 for nid in wf.nodes}
        for edge in wf.edges:
            adj[edge.source].append(edge.target)
            in_degree[edge.target] += 1

        queue: deque[str] = deque(nid for nid, d in in_degree.items() if d == 0)
        visited = 0
        while queue:
            nid = queue.popleft()
            visited += 1
            for neighbor in adj[nid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        assert visited == len(wf.nodes), "deep-qa graph has unexpected cycles"


class TestDeepQaTrigger:
    def test_trigger_deep_qa_mode(self) -> None:
        wf = deep_qa_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "deep-qa"})

    def test_trigger_rejects_other_modes(self) -> None:
        wf = deep_qa_workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "qa"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})


class TestDeepQaStructure:
    def test_name(self) -> None:
        wf = deep_qa_workflow()
        assert wf.name == "deep-qa"

    def test_start_node(self) -> None:
        wf = deep_qa_workflow()
        assert wf.start_node == "health_checker"

    def test_has_9_nodes(self) -> None:
        wf = deep_qa_workflow()
        assert len(wf.nodes) == 9
        expected = {
            "health_checker", "gate_health",
            "code_reviewer", "gate_review",
            "adversarial_tester", "gate_adversarial",
            "join_verdict", "gate_precheck", "post_review",
        }
        assert set(wf.nodes.keys()) == expected

    def test_has_12_edges(self) -> None:
        wf = deep_qa_workflow()
        assert len(wf.edges) == 12

    def test_3_qa_agent_nodes(self) -> None:
        wf = deep_qa_workflow()
        qa_agents = [
            nid for nid, n in wf.nodes.items()
            if isinstance(n, AgentNode) and n.role == AgentRole.QA
        ]
        assert set(qa_agents) == {"health_checker", "code_reviewer", "adversarial_tester"}

    def test_gate_types(self) -> None:
        wf = deep_qa_workflow()
        gate_health = wf.nodes["gate_health"]
        assert isinstance(gate_health, GateNode)
        assert gate_health.evaluator_type == "fn"

        gate_review = wf.nodes["gate_review"]
        assert isinstance(gate_review, GateNode)
        assert gate_review.evaluator_type == "agent"
        assert gate_review.evaluator_role == AgentRole.QA

        gate_adversarial = wf.nodes["gate_adversarial"]
        assert isinstance(gate_adversarial, GateNode)
        assert gate_adversarial.evaluator_type == "agent"
        assert gate_adversarial.evaluator_role == AgentRole.QA

    def test_join_verdict_is_fn_node(self) -> None:
        wf = deep_qa_workflow()
        assert isinstance(wf.nodes["join_verdict"], FnNode)

    def test_gate_precheck_is_fn_gate(self) -> None:
        wf = deep_qa_workflow()
        gate = wf.nodes["gate_precheck"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "fn"

    def test_post_review_is_fn_node(self) -> None:
        wf = deep_qa_workflow()
        assert isinstance(wf.nodes["post_review"], FnNode)

    def test_sequential_pipeline_order(self) -> None:
        """Verify the sequential chain: health → review → adversarial → join."""
        wf = deep_qa_workflow()
        expected_chain = [
            ("health_checker", "gate_health", None),
            ("gate_health", "code_reviewer", VerdictType.PROCEED),
            ("code_reviewer", "gate_review", None),
            ("gate_review", "adversarial_tester", VerdictType.PROCEED),
            ("adversarial_tester", "gate_adversarial", None),
            ("gate_adversarial", "join_verdict", VerdictType.PROCEED),
            ("join_verdict", "gate_precheck", None),
        ]
        for source, target, condition in expected_chain:
            matching = [
                e for e in wf.edges
                if e.source == source and e.target == target and e.condition == condition
            ]
            assert len(matching) == 1, (
                f"Missing edge: {source} → {target} (condition={condition})"
            )

    def test_specialist_writes_separate_files(self) -> None:
        wf = deep_qa_workflow()
        health = wf.nodes["health_checker"]
        review = wf.nodes["code_reviewer"]
        adversarial = wf.nodes["adversarial_tester"]
        assert isinstance(health, AgentNode)
        assert isinstance(review, AgentNode)
        assert isinstance(adversarial, AgentNode)
        assert ".factory/reviews/health-check.md" in health.writes
        assert ".factory/reviews/code-review.md" in review.writes
        assert ".factory/reviews/adversarial-qa.md" in adversarial.writes


class TestDeepQaHaltRouting:
    def test_all_halt_edges_target_post_review(self) -> None:
        wf = deep_qa_workflow()
        halt_edges = [e for e in wf.edges if e.condition == VerdictType.HALT]
        for e in halt_edges:
            assert e.target == "post_review", (
                f"HALT from {e.source} goes to {e.target}, expected post_review"
            )

    def test_4_halt_edges(self) -> None:
        """3 CEO gates + gate_precheck = 4 HALT edges."""
        wf = deep_qa_workflow()
        halt_edges = [e for e in wf.edges if e.condition == VerdictType.HALT]
        assert len(halt_edges) == 4
        halt_sources = {e.source for e in halt_edges}
        assert halt_sources == {"gate_health", "gate_review", "gate_adversarial", "gate_precheck"}

    def test_post_review_reachable_from_all_gates(self) -> None:
        """post_review must be reachable from every gate via HALT edge."""
        wf = deep_qa_workflow()
        adj: dict[str, list[str]] = defaultdict(list)
        for edge in wf.edges:
            adj[edge.source].append(edge.target)

        gates = ["gate_health", "gate_review", "gate_adversarial", "gate_precheck"]
        for gate in gates:
            visited: set[str] = set()
            queue: deque[str] = deque([gate])
            found = False
            while queue:
                nid = queue.popleft()
                if nid == "post_review":
                    found = True
                    break
                if nid in visited:
                    continue
                visited.add(nid)
                queue.extend(adj.get(nid, []))
            assert found, f"post_review not reachable from {gate}"

    def test_no_reloop_edges(self) -> None:
        wf = deep_qa_workflow()
        reloop = [e for e in wf.edges if e.condition == VerdictType.RELOOP]
        assert reloop == [], f"deep-qa must have zero RELOOP edges, found {len(reloop)}"


class TestDeepQaSkillExport:
    def test_skill_md_generates(self) -> None:
        from factory.workflow.skill_export import validate_skill, workflow_to_skill_md

        wf = deep_qa_workflow()
        skill_md = workflow_to_skill_md(wf)
        issues = validate_skill(skill_md)
        assert issues == [], f"deep-qa skill has issues: {issues}"
        assert "workflow-deep-qa" in skill_md

    def test_in_register_all(self) -> None:
        all_wf = register_all()
        assert "deep-qa" in all_wf
        issues = all_wf["deep-qa"].validate_graph()
        assert issues == [], f"deep-qa has validation issues: {issues}"


class TestDeepQaCli:
    def test_cli_accepts_deep_qa_mode(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["ceo", "/tmp/project", "--mode", "deep-qa"])
        assert args.mode == "deep-qa"
