"""Tests for the FeatureBench workflow (portable or contributed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.models import ProjectState
from factory.workflow.executor import WorkflowExecutor
from factory.workflow.primitives import AgentNode, AgentRole, FnNode, GateNode, VerdictType
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
        assert len(featurebench_wf.nodes) == 6
        assert set(featurebench_wf.nodes.keys()) == {
            "scan_stubs",
            "builder",
            "adversarial_tester",
            "gate_tests",
            "builder_fix",
            "archivist",
        }

    def test_start_node(self, featurebench_wf) -> None:
        assert featurebench_wf.start_node == "scan_stubs"

    def test_edge_count(self, featurebench_wf) -> None:
        assert len(featurebench_wf.edges) == 6

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
    def test_scan_stubs_is_fn(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["scan_stubs"]
        assert isinstance(node, FnNode)

    def test_builder_is_agent(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER

    def test_adversarial_tester_is_agent(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["adversarial_tester"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.ADVERSARIAL_TESTER

    def test_gate_tests_is_fn_gate(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["gate_tests"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"


class TestNodeProperties:
    def test_builder_fix_is_agent(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["builder_fix"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER

    def test_builder_fix_max_iterations(self, featurebench_wf) -> None:
        assert featurebench_wf.nodes["builder_fix"].max_iterations == 2

    def test_builder_fix_timeout(self, featurebench_wf) -> None:
        assert featurebench_wf.nodes["builder_fix"].timeout == 3600

    def test_builder_max_iterations(self, featurebench_wf) -> None:
        assert featurebench_wf.nodes["builder"].max_iterations == 3

    def test_builder_timeout(self, featurebench_wf) -> None:
        assert featurebench_wf.nodes["builder"].timeout == 3600

    def test_adversarial_tester_timeout(self, featurebench_wf) -> None:
        assert featurebench_wf.nodes["adversarial_tester"].timeout == 1800

    def test_no_user_gates(self, featurebench_wf) -> None:
        """Workflow is fully autonomous — no user approval gates."""
        for node in featurebench_wf.nodes.values():
            if isinstance(node, GateNode):
                assert node.evaluator_type != "user"

    def test_workflow_is_terminal(self, featurebench_wf) -> None:
        assert featurebench_wf.terminal is True


class TestContainerMetadata:
    def test_all_agent_nodes_run_on_host(self, featurebench_wf) -> None:
        """All agent nodes run on host — gate handles container via docker exec."""
        for node_id, node in featurebench_wf.nodes.items():
            if isinstance(node, AgentNode):
                assert node.metadata.get("execution_context") is None, (
                    f"Node {node_id} should run on host"
                )


class TestEdgeCoverage:
    def test_reloop_edges(self, featurebench_wf) -> None:
        """One reloop gate: gate_tests→builder_fix."""
        reloop_edges = [e for e in featurebench_wf.edges if e.condition == VerdictType.RELOOP]
        reloop_pairs = {(e.source, e.target) for e in reloop_edges}
        assert ("gate_tests", "builder_fix") in reloop_pairs
        assert len(reloop_edges) == 1

    def test_builder_fix_goes_to_gate(self, featurebench_wf) -> None:
        """builder_fix routes directly to gate_tests, skipping adversarial_tester."""
        builder_fix_edges = [e for e in featurebench_wf.edges if e.source == "builder_fix"]
        assert len(builder_fix_edges) == 1
        assert builder_fix_edges[0].target == "gate_tests"
        assert builder_fix_edges[0].condition is None

    def test_proceed_edges(self, featurebench_wf) -> None:
        """One proceed gate: gate_tests→archivist."""
        proceed = [
            e for e in featurebench_wf.edges
            if e.condition == VerdictType.PROCEED
        ]
        proceed_pairs = {(e.source, e.target) for e in proceed}
        assert ("gate_tests", "archivist") in proceed_pairs
        assert len(proceed) == 1

    def test_unconditional_pipeline(self, featurebench_wf) -> None:
        """Unconditional edges form the main pipeline spine."""
        unconditional = [e for e in featurebench_wf.edges if e.condition is None]
        pairs = {(e.source, e.target) for e in unconditional}
        assert ("scan_stubs", "builder") in pairs
        assert ("builder", "adversarial_tester") in pairs
        assert ("adversarial_tester", "gate_tests") in pairs


class TestMeta:
    def test_meta_has_name(self, featurebench_meta) -> None:
        assert featurebench_meta["name"] == "featurebench"

    def test_meta_has_description(self, featurebench_meta) -> None:
        assert "description" in featurebench_meta
        assert "FeatureBench" in featurebench_meta["description"]

    def test_meta_mentions_gate(self, featurebench_meta) -> None:
        assert "gate" in featurebench_meta["description"].lower()


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


class TestBuilderPrompt:
    """Builder prompt instructs reading existing type definitions."""

    def test_builder_reads_types_before_implementing(self, featurebench_wf) -> None:
        prompt = featurebench_wf.nodes["builder"].prompt_template
        assert "BEFORE IMPLEMENTING ANY FUNCTION" in prompt
        assert "class definitions" in prompt

    def test_builder_implements_from_problem_statement(self, featurebench_wf) -> None:
        prompt = featurebench_wf.nodes["builder"].prompt_template
        assert "problem_statement.md" in prompt


class TestAdversarialTesterPrompt:
    """Adversarial tester is a skeptical spec-compliance auditor."""

    def test_adversarial_reads_existing_types(self, featurebench_wf) -> None:
        prompt = featurebench_wf.nodes["adversarial_tester"].prompt_template
        assert "READ THE EXISTING TYPES" in prompt

    def test_adversarial_checks_field_mapping(self, featurebench_wf) -> None:
        prompt = featurebench_wf.nodes["adversarial_tester"].prompt_template
        assert "WRONG FIELD MAPPING" in prompt

    def test_adversarial_checks_paraphrased_strings(self, featurebench_wf) -> None:
        prompt = featurebench_wf.nodes["adversarial_tester"].prompt_template
        assert "PARAPHRASED STRINGS" in prompt

    def test_adversarial_no_privileged_info(self, featurebench_wf) -> None:
        """Prompt must not contain task-specific class/field names."""
        prompt = featurebench_wf.nodes["adversarial_tester"].prompt_template
        for term in ["mlflow", "seggpt", "astropy", "metaflow", "trl",
                     "lightning", "liger", "Assessment", "JudgeTool",
                     "source_type", "source_id", "_PRECISION"]:
            assert term not in prompt, f"Privileged term {term!r} found in adversarial prompt"


class TestBuilderFixStateful:
    """Builder_fix has git diff context and writes to separate file."""

    def test_builder_fix_writes_separate_file(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["builder_fix"]
        assert ".factory/reviews/builder-fix-latest.md" in node.writes

    def test_builder_fix_does_not_overwrite_builder(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["builder_fix"]
        assert ".factory/reviews/builder-latest.md" not in node.writes

    def test_builder_fix_prompt_has_git_log(self, featurebench_wf) -> None:
        prompt = featurebench_wf.nodes["builder_fix"].prompt_template
        assert "git log" in prompt

    def test_builder_fix_prompt_has_git_diff(self, featurebench_wf) -> None:
        prompt = featurebench_wf.nodes["builder_fix"].prompt_template
        assert "git diff" in prompt

    def test_builder_fix_no_restructure_rule(self, featurebench_wf) -> None:
        prompt = featurebench_wf.nodes["builder_fix"].prompt_template
        assert "Do NOT create new directory structures" in prompt

    def test_builder_fix_records_failures(self, featurebench_wf) -> None:
        """Output file should capture which failures were addressed."""
        prompt = featurebench_wf.nodes["builder_fix"].prompt_template
        assert "Which test failures you addressed" in prompt

    def test_builder_fix_post_check_correct_file(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["builder_fix"]
        assert any(
            c.path == ".factory/reviews/builder-fix-latest.md"
            for c in node.post_checks
        )


class TestGateFeedback:
    """Gate reloop feedback includes actual failure details."""

    def test_gate_includes_grep_failures(self, featurebench_wf) -> None:
        cmd = featurebench_wf.nodes["gate_tests"].evaluator_command
        assert "grep" in cmd
        assert "FAILED" in cmd

    def test_gate_feedback_not_just_pointer(self, featurebench_wf) -> None:
        """Reloop message should include failure text, not just a file path."""
        cmd = featurebench_wf.nodes["gate_tests"].evaluator_command
        assert "Failures:" in cmd or "failures:" in cmd.lower()

    def test_gate_failures_single_line(self, featurebench_wf) -> None:
        """FAILS must be collapsed to one line so 'reloop:' stays the last line prefix."""
        cmd = featurebench_wf.nodes["gate_tests"].evaluator_command
        assert "tr '\\n'" in cmd or 'tr "\\n"' in cmd


class TestScanStubsL2Fallback:
    """scan_stubs script has L2 structural template fallback."""

    def test_scan_stubs_has_l2_section(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["scan_stubs"]
        assert "L2 EXPECTED SOURCE PATHS" in node.command

    def test_scan_stubs_detects_test_imports(self, featurebench_wf) -> None:
        node = featurebench_wf.nodes["scan_stubs"]
        assert "imported by" in node.command.lower() or "MISSING" in node.command


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

    def test_adapter_has_file_sync_methods(self) -> None:
        adapter_path = PROJECT_ROOT / "factory" / "featurebench" / "agent.py"
        content = adapter_path.read_text()
        assert "_sync_workspace_to_container" in content
        assert "_sync_from_container" in content

    def test_adapter_has_problem_statement(self) -> None:
        adapter_path = PROJECT_ROOT / "factory" / "featurebench" / "agent.py"
        content = adapter_path.read_text()
        assert "problem_statement.md" in content


class TestExecutorContainerRouting:
    """Test executor routing logic for container nodes."""

    def test_executor_accepts_context(self, featurebench_wf) -> None:
        """Executor constructor accepts a context dict."""
        executor = WorkflowExecutor(
            featurebench_wf,
            Path("/tmp/test"),
            dry_run=True,
            context={"container_name": "test-container"},
        )
        assert executor.context["container_name"] == "test-container"

    def test_executor_default_context_empty(self, featurebench_wf) -> None:
        executor = WorkflowExecutor(
            featurebench_wf,
            Path("/tmp/test"),
            dry_run=True,
        )
        assert executor.context == {}

    def test_executor_accepts_hooks(self, featurebench_wf) -> None:
        """Executor constructor accepts pre/post node hooks."""
        async def hook(node_id, node):
            pass
        executor = WorkflowExecutor(
            featurebench_wf,
            Path("/tmp/test"),
            dry_run=True,
            pre_node_hook=hook,
            post_node_hook=hook,
        )
        assert executor.pre_node_hook is hook
        assert executor.post_node_hook is hook

    async def test_agent_container_routing_requires_container_name(self, featurebench_wf) -> None:
        """Container agent node raises when no container_name in context."""
        executor = WorkflowExecutor(
            featurebench_wf,
            Path("/tmp/test"),
            context={},
        )
        container_node = AgentNode(
            id="test_container",
            role=AgentRole.BUILDER,
            metadata={"execution_context": "container"},
        )
        with pytest.raises(RuntimeError, match="container_name"):
            await executor._run_agent_in_container(container_node)

    async def test_fn_container_routing_requires_container_name(self, featurebench_wf) -> None:
        """Container fn node raises when no container_name in context."""
        fn_node = FnNode(
            id="test_fn",
            command="echo hello",
            metadata={"execution_context": "container"},
        )
        executor = WorkflowExecutor(
            featurebench_wf,
            Path("/tmp/test"),
            context={},
        )
        with pytest.raises(RuntimeError, match="container_name"):
            await executor._run_fn_in_container(fn_node)

    def test_host_agent_uses_invoke_agent(self, featurebench_wf) -> None:
        """Host nodes (no container metadata) use standard invoke_agent path."""
        builder = featurebench_wf.nodes["builder"]
        assert builder.metadata.get("execution_context") is None
