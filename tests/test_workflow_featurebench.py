"""Tests for the FeatureBench workflow (portable or contributed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.models import ProjectState
from factory.workflow.primitives import AgentNode, AgentRole, GateNode, VerdictType
from factory.workflow.registry import WorkflowRegistry, _load_workflow_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_FILE = PROJECT_ROOT / ".factory" / "workflows" / "featurebench.py"


def _load_featurebench():
    """Load the featurebench workflow from portable location or contributed fallback."""
    if WORKFLOW_FILE.exists():
        return _load_workflow_file(WORKFLOW_FILE)
    from factory.workflow.contributed.featurebench import meta, workflow
    return meta, workflow


@pytest.fixture()
def featurebench_meta_and_fn():
    return _load_featurebench()


@pytest.fixture()
def featurebench_wf(featurebench_meta_and_fn):
    _, workflow_fn = featurebench_meta_and_fn
    return workflow_fn()


@pytest.fixture()
def featurebench_meta(featurebench_meta_and_fn):
    meta, _ = featurebench_meta_and_fn
    return meta


class TestGraphValidation:
    def test_graph_valid(self, featurebench_wf) -> None:
        """All edges reference valid nodes, no orphans, start_node exists."""
        issues = featurebench_wf.validate_graph()
        assert issues == [], f"Graph validation errors: {issues}"

    def test_node_count(self, featurebench_wf) -> None:
        assert len(featurebench_wf.nodes) == 10
        assert set(featurebench_wf.nodes.keys()) == {
            "researcher",
            "strategist",
            "builder",
            "code_reviewer",
            "gate_review",
            "adversarial_tester",
            "gate_qa",
            "health_checker",
            "gate_tests",
            "archivist",
        }

    def test_start_node(self, featurebench_wf) -> None:
        assert featurebench_wf.start_node == "researcher"

    def test_edge_count(self, featurebench_wf) -> None:
        assert len(featurebench_wf.edges) == 12

    def test_workflow_name(self, featurebench_wf) -> None:
        assert featurebench_wf.name == "featurebench"


class TestTriggerFunction:
    def test_trigger_true_has_factory(self, featurebench_wf) -> None:
        assert featurebench_wf.trigger is not None
        assert featurebench_wf.trigger(ProjectState.HAS_FACTORY, {"mode": "featurebench"}) is True

    def test_trigger_true_no_factory(self, featurebench_wf) -> None:
        assert featurebench_wf.trigger is not None
        assert featurebench_wf.trigger(ProjectState.NO_FACTORY, {"mode": "featurebench"}) is True

    def test_trigger_false_other_mode(self, featurebench_wf) -> None:
        assert featurebench_wf.trigger is not None
        assert featurebench_wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"}) is False

    def test_trigger_false_empty_ctx(self, featurebench_wf) -> None:
        assert featurebench_wf.trigger is not None
        assert featurebench_wf.trigger(ProjectState.HAS_FACTORY, {}) is False


class TestNodeTypes:
    def test_researcher_is_agent(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["researcher"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER

    def test_strategist_is_agent(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["strategist"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_builder_is_agent(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER

    def test_code_reviewer_is_agent(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["code_reviewer"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.CODE_REVIEWER

    def test_gate_review_is_fn_gate(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["gate_review"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"

    def test_adversarial_tester_is_agent(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["adversarial_tester"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.ADVERSARIAL_TESTER

    def test_gate_qa_is_agent_gate(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["gate_qa"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "agent"
        assert node.evaluator_role == AgentRole.CEO

    def test_health_checker_is_agent(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["health_checker"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.HEALTH_CHECKER

    def test_gate_tests_is_fn_gate(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["gate_tests"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"

    def test_archivist_is_agent(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["archivist"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.ARCHIVIST


class TestNodeProperties:
    def test_builder_max_iterations(self, featurebench_wf) -> None:
        assert featurebench_wf.nodes["builder"].max_iterations == 3

    def test_builder_timeout(self, featurebench_wf) -> None:
        assert featurebench_wf.nodes["builder"].timeout == 1200

    def test_code_reviewer_timeout(self, featurebench_wf) -> None:
        assert featurebench_wf.nodes["code_reviewer"].timeout == 900

    def test_adversarial_tester_timeout(self, featurebench_wf) -> None:
        assert featurebench_wf.nodes["adversarial_tester"].timeout == 1800

    def test_archivist_non_blocking(self, featurebench_wf) -> None:
        assert featurebench_wf.nodes["archivist"].blocking is False

    def test_archivist_model(self, featurebench_wf) -> None:
        assert featurebench_wf.nodes["archivist"].model == "haiku"

    def test_no_user_gates(self, featurebench_wf) -> None:
        """Workflow is fully autonomous — no user approval gates."""
        for node in featurebench_wf.nodes.values():
            if isinstance(node, GateNode):
                assert node.evaluator_type != "user"

    def test_workflow_is_terminal(self, featurebench_wf) -> None:
        assert featurebench_wf.terminal is True


class TestEdgeCoverage:
    def test_reloop_edges(self, featurebench_wf) -> None:
        """Both QA and test loops have RELOOP edges back to builder."""
        reloop_edges = [e for e in featurebench_wf.edges if e.condition == VerdictType.RELOOP]
        reloop_pairs = {(e.source, e.target) for e in reloop_edges}
        assert ("gate_qa", "builder") in reloop_pairs
        assert ("gate_tests", "builder") in reloop_pairs
        assert len(reloop_edges) == 2

    def test_proceed_edge_gate_review(self, featurebench_wf) -> None:
        proceed = [
            e
            for e in featurebench_wf.edges
            if e.source == "gate_review"
            and e.target == "adversarial_tester"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed) == 1

    def test_halt_edge_gate_review(self, featurebench_wf) -> None:
        halt = [
            e
            for e in featurebench_wf.edges
            if e.source == "gate_review"
            and e.target == "health_checker"
            and e.condition == VerdictType.HALT
        ]
        assert len(halt) == 1

    def test_proceed_edge_gate_qa(self, featurebench_wf) -> None:
        proceed = [
            e
            for e in featurebench_wf.edges
            if e.source == "gate_qa"
            and e.target == "health_checker"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed) == 1

    def test_proceed_edge_gate_tests(self, featurebench_wf) -> None:
        proceed = [
            e
            for e in featurebench_wf.edges
            if e.source == "gate_tests"
            and e.target == "archivist"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed) == 1

    def test_unconditional_pipeline(self, featurebench_wf) -> None:
        """Unconditional edges form the main pipeline spine."""
        unconditional = [e for e in featurebench_wf.edges if e.condition is None]
        pairs = {(e.source, e.target) for e in unconditional}
        assert ("researcher", "strategist") in pairs
        assert ("strategist", "builder") in pairs
        assert ("builder", "code_reviewer") in pairs
        assert ("code_reviewer", "gate_review") in pairs
        assert ("adversarial_tester", "gate_qa") in pairs
        assert ("health_checker", "gate_tests") in pairs


class TestMeta:
    def test_meta_has_name(self, featurebench_meta) -> None:
        assert featurebench_meta["name"] == "featurebench"

    def test_meta_has_description(self, featurebench_meta) -> None:
        assert "description" in featurebench_meta
        assert "FeatureBench" in featurebench_meta["description"]

    def test_meta_mentions_two_loop(self, featurebench_meta) -> None:
        assert "two-loop" in featurebench_meta["description"]


class TestRegistryDiscovery:
    def test_discovered_via_registry(self) -> None:
        """Registry discovers the portable workflow when given the project path."""
        WorkflowRegistry.reset()
        entries = WorkflowRegistry.discover(project_path=PROJECT_ROOT)
        assert "featurebench" in entries
        assert entries["featurebench"].source == "project"

    def test_registry_workflow_validates(self) -> None:
        WorkflowRegistry.reset()
        wf = WorkflowRegistry.get_workflow("featurebench", project_path=PROJECT_ROOT)
        assert wf is not None
        issues = wf.validate_graph()
        assert issues == [], f"Registry-loaded workflow has issues: {issues}"


class TestNoFactoryInfrastructure:
    def test_no_factory_eval_nodes(self, featurebench_wf) -> None:
        """No factory experiment tracking nodes."""
        node_ids = set(featurebench_wf.nodes.keys())
        assert "begin" not in node_ids
        assert "finalize" not in node_ids
        assert "gate_precheck" not in node_ids


class TestAdapterModule:
    """Test the adapter module structure (without importing featurebench dependency)."""

    def test_adapter_file_exists(self) -> None:
        adapter_path = PROJECT_ROOT / "factory" / "featurebench" / "agent.py"
        assert adapter_path.exists()

    def test_init_file_exists(self) -> None:
        init_path = PROJECT_ROOT / "factory" / "featurebench" / "__init__.py"
        assert init_path.exists()

    def test_config_template_exists(self) -> None:
        config_path = PROJECT_ROOT / "factory" / "featurebench" / "config.toml.example"
        assert config_path.exists()

    def test_adapter_defines_factory_agent_class(self) -> None:
        adapter_path = PROJECT_ROOT / "factory" / "featurebench" / "agent.py"
        content = adapter_path.read_text()
        assert "class FactoryAgent" in content
        assert "def name(self)" in content
        assert "def install_script(self)" in content
        assert "def get_run_command(self" in content
        assert "def pre_run_hook(self" in content
        assert "def post_run_hook(self" in content

    def test_adapter_name_returns_factory(self) -> None:
        adapter_path = PROJECT_ROOT / "factory" / "featurebench" / "agent.py"
        content = adapter_path.read_text()
        assert 'return "factory"' in content

    def test_install_script_installs_dependencies(self) -> None:
        adapter_path = PROJECT_ROOT / "factory" / "featurebench" / "agent.py"
        content = adapter_path.read_text()
        assert "uv" in content
        assert "nvm" in content
        assert "claude-code" in content

    def test_run_command_writes_problem_statement(self) -> None:
        adapter_path = PROJECT_ROOT / "factory" / "featurebench" / "agent.py"
        content = adapter_path.read_text()
        assert "problem_statement.md" in content
        assert "claude -p" in content
