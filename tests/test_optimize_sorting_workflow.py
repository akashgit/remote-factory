"""Tests for the optimize-sorting workflow definition."""

import sys

import pytest

# The workflow lives in .factory/workflows/ which is not a Python package on sys.path.
# We insert it so we can import the module directly.
sys.path.insert(0, ".factory/workflows")


def _load_workflow():
    from optimize_sorting import workflow

    return workflow()


def _load_meta():
    from optimize_sorting import meta

    return meta


# ── Structure tests ──────────────────────────────────────────────


def test_workflow_loads():
    """Verify workflow() returns a valid Workflow object."""
    w = _load_workflow()
    assert w.name == "optimize-sorting"
    assert w.terminal is True
    assert w.start_node == "lock_baseline"


def test_meta():
    """Verify meta dict has correct name."""
    m = _load_meta()
    assert m["name"] == "optimize-sorting"
    assert "description" in m


def test_node_count():
    """Verify all 31 nodes are defined."""
    w = _load_workflow()
    assert len(w.nodes) == 31


def test_edge_count():
    """Verify all 39 edges are defined."""
    w = _load_workflow()
    assert len(w.edges) == 39


def test_trigger_accepts_optimize_sorting():
    """Trigger returns True for mode=optimize-sorting."""
    w = _load_workflow()
    assert w.trigger(None, {"mode": "optimize-sorting"}) is True


def test_trigger_rejects_other_modes():
    """Trigger returns False for other modes."""
    w = _load_workflow()
    assert w.trigger(None, {"mode": "featurebench"}) is False
    assert w.trigger(None, {}) is False


def test_all_edge_targets_exist():
    """Every edge target must reference an existing node."""
    w = _load_workflow()
    node_ids = set(w.nodes.keys())
    for edge in w.edges:
        assert edge.source in node_ids, f"Edge source {edge.source} not in nodes"
        assert edge.target in node_ids, f"Edge target {edge.target} not in nodes"


# ── Node type tests ──────────────────────────────────────────────


def test_node_type_counts():
    """Verify the breakdown: 8 FnNode, 11 GateNode, 12 AgentNode."""
    from factory.workflow.primitives import AgentNode, FnNode, GateNode

    w = _load_workflow()
    fn = sum(1 for n in w.nodes.values() if isinstance(n, FnNode))
    gate = sum(1 for n in w.nodes.values() if isinstance(n, GateNode))
    agent = sum(1 for n in w.nodes.values() if isinstance(n, AgentNode))
    assert fn == 8, f"Expected 8 FnNodes, got {fn}"
    assert gate == 11, f"Expected 11 GateNodes, got {gate}"
    assert agent == 12, f"Expected 12 AgentNodes, got {agent}"


def test_gate_nodes_have_evaluator_commands():
    """Every GateNode must have evaluator_command set."""
    from factory.workflow.primitives import GateNode

    w = _load_workflow()
    for node_id, node in w.nodes.items():
        if isinstance(node, GateNode):
            assert node.evaluator_command, (
                f"GateNode {node_id} missing evaluator_command"
            )


def test_agent_nodes_have_post_checks():
    """Every AgentNode must have at least one post_check."""
    from factory.workflow.primitives import AgentNode

    w = _load_workflow()
    for node_id, node in w.nodes.items():
        if isinstance(node, AgentNode):
            assert len(node.post_checks) > 0, (
                f"AgentNode {node_id} missing post_checks"
            )


# ── Key node existence tests ─────────────────────────────────────


@pytest.mark.parametrize(
    "node_id",
    [
        "lock_baseline",
        "select_tier",
        "run_benchmark_t1",
        "run_benchmark_t2",
        "run_benchmark_t3",
        "confirm_benchmark_t1",
        "confirm_benchmark_t2",
        "confirm_benchmark_t3",
        "gate_is_tier1",
        "gate_is_tier2",
        "gate_is_tier3",
        "gate_no_code_changes",
        "gate_catastrophic_t1",
        "gate_catastrophic_t2",
        "gate_catastrophic_t3",
        "gate_accuracy_t1",
        "gate_accuracy_t2",
        "gate_accuracy_t3",
        "gate_per_unit_accuracy",
        "researcher_discover_params",
        "researcher_profile_pipeline",
        "researcher_explore_alternatives",
        "strategist_t1",
        "strategist_t2",
        "strategist_t3",
        "builder_config_change",
        "builder_optimize_hotpath",
        "builder_implement_alternative",
        "archive_result_t1",
        "archive_result_t2",
        "archive_result_t3",
    ],
)
def test_node_exists(node_id):
    """Verify each expected node exists in the workflow."""
    w = _load_workflow()
    assert node_id in w.nodes, f"Node {node_id} not found in workflow"


# ── Change 1: Gap Detection tests ────────────────────────────────


def test_researcher_profile_prompt_has_gap_detection():
    """researcher_profile_pipeline prompt contains Gap Detection section."""
    w = _load_workflow()
    prompt = w.nodes["researcher_profile_pipeline"].prompt_template
    assert "Gap Detection Analysis" in prompt
    assert "gap_analysis" in prompt
    assert "gap_seconds" in prompt
    assert "gap_pct" in prompt
    assert "gap_threshold_exceeded" in prompt
    assert "10.0" in prompt


def test_researcher_profile_prompt_has_io_analysis():
    """researcher_profile_pipeline prompt contains I/O Analysis section."""
    w = _load_workflow()
    prompt = w.nodes["researcher_profile_pipeline"].prompt_template
    assert "I/O Analysis" in prompt
    assert "io_profile" in prompt
    assert "read_bytes" in prompt
    assert "write_bytes" in prompt


# ── Change 2: I/O Profiling tests ────────────────────────────────


def test_lock_baseline_has_io_capture():
    """lock_baseline command contains capture_io and io_profile."""
    w = _load_workflow()
    cmd = w.nodes["lock_baseline"].command
    assert "capture_io" in cmd, "lock_baseline missing capture_io"
    assert "io_profile" in cmd, "lock_baseline missing io_profile"
    assert "/proc/self/io" in cmd, "lock_baseline missing /proc/self/io"
    assert "available" in cmd, "lock_baseline missing available flag"


@pytest.mark.parametrize("tier_id", ["t1", "t2", "t3"])
def test_run_benchmark_has_io_capture(tier_id):
    """run_benchmark_t* commands contain I/O capture."""
    w = _load_workflow()
    cmd = w.nodes[f"run_benchmark_{tier_id}"].command
    assert "capture_io" in cmd, f"run_benchmark_{tier_id} missing capture_io"
    assert "io_profile" in cmd, f"run_benchmark_{tier_id} missing io_profile"


@pytest.mark.parametrize("tier_id", ["t1", "t2", "t3"])
def test_confirm_benchmark_has_io_capture(tier_id):
    """confirm_benchmark_t* commands contain I/O capture."""
    w = _load_workflow()
    cmd = w.nodes[f"confirm_benchmark_{tier_id}"].command
    assert "capture_io" in cmd, f"confirm_benchmark_{tier_id} missing capture_io"
    assert "io_profile" in cmd, f"confirm_benchmark_{tier_id} missing io_profile"
    assert "io_samples" in cmd, f"confirm_benchmark_{tier_id} missing io_samples"


def test_io_capture_graceful_fallback():
    """I/O capture has graceful fallback for non-Linux systems."""
    w = _load_workflow()
    cmd = w.nodes["lock_baseline"].command
    assert "FileNotFoundError" in cmd or "except" in cmd, (
        "Missing error handling for /proc/self/io"
    )
    assert "'available': False" in cmd or "available" in cmd


# ── Archivist tests ──────────────────────────────────────────────


@pytest.mark.parametrize("tier_id", ["t1", "t2", "t3"])
def test_archivist_model_is_haiku(tier_id):
    """Archivist nodes use haiku model."""
    w = _load_workflow()
    node = w.nodes[f"archive_result_{tier_id}"]
    assert node.model == "haiku"


@pytest.mark.parametrize("tier_id", ["t1", "t2", "t3"])
def test_archivist_timeout(tier_id):
    """Archivist nodes have 300s timeout."""
    w = _load_workflow()
    node = w.nodes[f"archive_result_{tier_id}"]
    assert node.timeout == 300


# ── Graph validation test ────────────────────────────────────────


def test_workflow_validates():
    """Workflow validates with no graph issues."""
    w = _load_workflow()
    issues = w.validate_graph()
    assert issues == [], f"Graph validation issues: {issues}"


# ── Gap detection arithmetic tests ───────────────────────────────


def test_gap_calculation():
    """Gap = total_elapsed - sum_stage_times."""
    total_elapsed = 960.0
    stage_times = [120.0, 450.0, 158.0]
    gap_seconds = total_elapsed - sum(stage_times)
    gap_pct = (gap_seconds / total_elapsed) * 100
    assert abs(gap_seconds - 232.0) < 0.01
    assert abs(gap_pct - 24.17) < 0.1


def test_gap_below_threshold():
    """Gap < 10% should not be flagged."""
    total_elapsed = 100.0
    stage_times = [45.0, 30.0, 20.0]
    gap_pct = (total_elapsed - sum(stage_times)) / total_elapsed * 100
    assert gap_pct < 10.0
    assert gap_pct == 5.0


def test_gap_above_threshold():
    """Gap > 10% should be flagged."""
    total_elapsed = 100.0
    stage_times = [30.0, 25.0, 20.0]
    gap_pct = (total_elapsed - sum(stage_times)) / total_elapsed * 100
    assert gap_pct > 10.0
    assert gap_pct == 25.0
