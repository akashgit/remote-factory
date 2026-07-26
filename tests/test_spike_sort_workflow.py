"""Tests for the spike-sort workflow definition (17-node LLM gates version)."""

from __future__ import annotations

import pytest

from factory.models import ProjectState
from factory.workflow.definitions import spike_sort_workflow
from factory.workflow.primitives import AgentNode, AgentRole, FnNode


# ── Graph validation ──────────────────────────────────────────────


def test_spike_sort_workflow_validates():
    """spike-sort graph passes structural validation."""
    wf = spike_sort_workflow()
    issues = wf.validate_graph()
    assert issues == [], f"Validation issues: {issues}"


# ── Node structure ────────────────────────────────────────────────


def test_spike_sort_has_17_nodes():
    """Spike-sort pipeline has exactly 17 nodes: 14 FnNodes + 3 AgentNodes."""
    wf = spike_sort_workflow()
    fn_nodes = [n for n in wf.nodes.values() if isinstance(n, FnNode)]
    agent_nodes = [n for n in wf.nodes.values() if isinstance(n, AgentNode)]
    assert len(fn_nodes) == 14, f"Expected 14 FnNodes, got {len(fn_nodes)}"
    assert len(agent_nodes) == 3, f"Expected 3 AgentNodes, got {len(agent_nodes)}"
    assert len(wf.nodes) == 17


def test_spike_sort_node_ids():
    """All expected node IDs are present."""
    wf = spike_sort_workflow()
    expected = {
        "preprocess",
        "detect",
        "localize",
        "cluster",
        "compute_cluster_metrics",
        "gate_post_cluster",
        "apply_cluster_actions",
        "templates",
        "compute_template_metrics",
        "gate_post_template",
        "apply_template_actions",
        "match",
        "compute_final_metrics",
        "gate_post_match",
        "apply_final_actions",
        "recover_low_snr_spikes",
        "evaluate_accuracy",
    }
    assert set(wf.nodes.keys()) == expected


def test_spike_sort_removed_nodes_absent():
    """Old parameter-picking and QA nodes are removed."""
    wf = spike_sort_workflow()
    for removed in ["detect_trial", "detect_params", "cluster_params", "qa_sorting"]:
        assert removed not in wf.nodes, f"{removed} should have been removed"


# ── Linear pipeline structure ────────────────────────────────────


def test_spike_sort_is_linear():
    """Spike-sort has exactly 16 edges forming a linear chain."""
    wf = spike_sort_workflow()
    assert len(wf.edges) == 16

    expected_chain = [
        ("preprocess", "detect"),
        ("detect", "localize"),
        ("localize", "cluster"),
        ("cluster", "compute_cluster_metrics"),
        ("compute_cluster_metrics", "gate_post_cluster"),
        ("gate_post_cluster", "apply_cluster_actions"),
        ("apply_cluster_actions", "templates"),
        ("templates", "compute_template_metrics"),
        ("compute_template_metrics", "gate_post_template"),
        ("gate_post_template", "apply_template_actions"),
        ("apply_template_actions", "match"),
        ("match", "compute_final_metrics"),
        ("compute_final_metrics", "gate_post_match"),
        ("gate_post_match", "apply_final_actions"),
        ("apply_final_actions", "recover_low_snr_spikes"),
        ("recover_low_snr_spikes", "evaluate_accuracy"),
    ]
    actual_chain = [(e.source, e.target) for e in wf.edges]
    assert actual_chain == expected_chain


def test_spike_sort_no_conditional_edges():
    """Linear pipeline has no conditional (PROCEED/RELOOP/HALT) edges."""
    wf = spike_sort_workflow()
    for edge in wf.edges:
        assert edge.condition is None, (
            f"Edge {edge.source}→{edge.target} has condition {edge.condition}"
        )


# ── Gate triplet structure ───────────────────────────────────────


def test_gate1_triplet_wiring():
    """cluster → compute_cluster_metrics → gate_post_cluster → apply_cluster_actions."""
    wf = spike_sort_workflow()
    edges = [(e.source, e.target) for e in wf.edges]
    assert ("cluster", "compute_cluster_metrics") in edges
    assert ("compute_cluster_metrics", "gate_post_cluster") in edges
    assert ("gate_post_cluster", "apply_cluster_actions") in edges


def test_gate2_triplet_wiring():
    """templates → compute_template_metrics → gate_post_template → apply_template_actions."""
    wf = spike_sort_workflow()
    edges = [(e.source, e.target) for e in wf.edges]
    assert ("templates", "compute_template_metrics") in edges
    assert ("compute_template_metrics", "gate_post_template") in edges
    assert ("gate_post_template", "apply_template_actions") in edges


def test_gate3_triplet_wiring():
    """match → compute_final_metrics → gate_post_match → apply_final_actions."""
    wf = spike_sort_workflow()
    edges = [(e.source, e.target) for e in wf.edges]
    assert ("match", "compute_final_metrics") in edges
    assert ("compute_final_metrics", "gate_post_match") in edges
    assert ("gate_post_match", "apply_final_actions") in edges


# ── Agent model assignment ───────────────────────────────────────


def test_spike_sort_gate_models():
    """Gate 1 uses sonnet; Gates 2 and 3 use haiku."""
    wf = spike_sort_workflow()

    gate1 = wf.nodes["gate_post_cluster"]
    assert isinstance(gate1, AgentNode)
    assert gate1.role == AgentRole.HEALTH_CHECKER
    assert gate1.model == "sonnet"
    assert gate1.timeout == 300

    gate2 = wf.nodes["gate_post_template"]
    assert isinstance(gate2, AgentNode)
    assert gate2.role == AgentRole.HEALTH_CHECKER
    assert gate2.model == "haiku"
    assert gate2.timeout == 120

    gate3 = wf.nodes["gate_post_match"]
    assert isinstance(gate3, AgentNode)
    assert gate3.role == AgentRole.HEALTH_CHECKER
    assert gate3.model == "haiku"
    assert gate3.timeout == 120


# ── FnNode callable names ───────────────────────────────────────


def test_spike_sort_fn_callables():
    """All FnNodes reference factory.workflow.spike_sort_stages callables."""
    wf = spike_sort_workflow()

    expected_callables = {
        "preprocess": "factory.workflow.spike_sort_stages:preprocess",
        "detect": "factory.workflow.spike_sort_stages:detect",
        "localize": "factory.workflow.spike_sort_stages:localize",
        "cluster": "factory.workflow.spike_sort_stages:cluster",
        "compute_cluster_metrics": "factory.workflow.spike_sort_stages:compute_cluster_metrics",
        "apply_cluster_actions": "factory.workflow.spike_sort_stages:apply_cluster_actions",
        "templates": "factory.workflow.spike_sort_stages:compute_templates",
        "match": "factory.workflow.spike_sort_stages:template_match",
        "compute_template_metrics": "factory.workflow.spike_sort_stages:compute_template_metrics",
        "apply_template_actions": "factory.workflow.spike_sort_stages:apply_template_actions",
        "compute_final_metrics": "factory.workflow.spike_sort_stages:compute_final_metrics",
        "apply_final_actions": "factory.workflow.spike_sort_stages:apply_final_actions",
        "recover_low_snr_spikes": "factory.workflow.spike_sort_stages:recover_low_snr_spikes",
        "evaluate_accuracy": "factory.workflow.spike_sort_stages:evaluate_accuracy",
    }
    for node_id, expected in expected_callables.items():
        node = wf.nodes[node_id]
        assert isinstance(node, FnNode), f"{node_id} should be FnNode"
        assert node.callable_name == expected, (
            f"{node_id}: expected {expected}, got {node.callable_name}"
        )


# ── Data flow connectivity ───────────────────────────────────────


def test_spike_sort_data_flow():
    """Each node's reads are a subset of predecessors' writes."""
    wf = spike_sort_workflow()

    node_order = [
        "preprocess",
        "detect",
        "localize",
        "cluster",
        "compute_cluster_metrics",
        "gate_post_cluster",
        "apply_cluster_actions",
        "templates",
        "compute_template_metrics",
        "gate_post_template",
        "apply_template_actions",
        "match",
        "compute_final_metrics",
        "gate_post_match",
        "apply_final_actions",
        "recover_low_snr_spikes",
        "evaluate_accuracy",
    ]

    available: set[str] = set()
    for nid in node_order:
        node = wf.nodes[nid]
        missing = node.reads - available
        assert not missing, f"Node '{nid}' reads {missing} but only {available} available"
        available |= node.writes


# ── Schema validation ────────────────────────────────────────────


def test_detection_params_schema():
    """DetectionParams validates within tightened bounds [3.0, 6.0]."""
    from factory.workflow.spike_sort_schemas import DetectionParams

    params = DetectionParams(
        detection_threshold=4.0,
        peak_sign="both",
        temporal_dedup_radius_samples=11,
        reasoning="Standard parameters for clean cortical recording",
    )
    assert params.detection_threshold == 4.0
    assert params.use_denoiser is True

    with pytest.raises(Exception):
        DetectionParams(
            detection_threshold=2.5,
            peak_sign="both",
            temporal_dedup_radius_samples=11,
            reasoning="below tightened lower bound",
        )
    with pytest.raises(Exception):
        DetectionParams(
            detection_threshold=7.0,
            peak_sign="both",
            temporal_dedup_radius_samples=11,
            reasoning="above tightened upper bound",
        )


def test_detection_params_use_denoiser():
    """DetectionParams use_denoiser defaults to True and accepts False."""
    from factory.workflow.spike_sort_schemas import DetectionParams

    params_default = DetectionParams(
        detection_threshold=4.0,
        peak_sign="both",
        temporal_dedup_radius_samples=11,
        reasoning="default denoiser",
    )
    assert params_default.use_denoiser is True

    params_disabled = DetectionParams(
        detection_threshold=4.0,
        peak_sign="both",
        temporal_dedup_radius_samples=11,
        use_denoiser=False,
        reasoning="denoiser disabled",
    )
    assert params_disabled.use_denoiser is False


def test_clustering_params_schema():
    """ClusteringParams validates strategy and grid bounds."""
    from factory.workflow.spike_sort_schemas import ClusteringParams

    params = ClusteringParams(
        strategy="grid_snap",
        grid_dx=15.0,
        grid_dz=15.0,
        initial_steps=["split", "demolish", "demolish"],
        n_waveforms_fit=40000,
        reasoning="Standard grid for medium-density recording",
    )
    assert params.strategy == "grid_snap"

    with pytest.raises(Exception):
        ClusteringParams(
            strategy="invalid",
            grid_dx=15.0,
            grid_dz=15.0,
            initial_steps=["split"],
            n_waveforms_fit=40000,
            reasoning="bad",
        )


def test_template_decision_merge_requires_partner():
    """Merge action without merge_with is valid per schema (None allowed)."""
    from factory.workflow.spike_sort_schemas import TemplateDecision

    keep = TemplateDecision(template_id=0, action="keep", reasoning="good unit")
    assert keep.merge_with is None

    merge = TemplateDecision(template_id=1, action="merge", merge_with=0, reasoning="similar")
    assert merge.merge_with == 0


def test_template_qc_output_schema():
    """TemplateQCOutput accepts a list of decisions with summary."""
    from factory.workflow.spike_sort_schemas import TemplateDecision, TemplateQCOutput

    output = TemplateQCOutput(
        decisions=[
            TemplateDecision(template_id=0, action="keep", reasoning="good SNR"),
            TemplateDecision(template_id=1, action="discard", reasoning="noise unit"),
            TemplateDecision(
                template_id=2, action="merge", merge_with=0, reasoning="similar waveform"
            ),
        ],
        summary="Kept 1, discarded 1, merged 1 into unit 0",
    )
    assert len(output.decisions) == 3


def test_noise_stats_schema():
    """NoiseStats validates all required fields."""
    from factory.workflow.spike_sort_schemas import NoiseStats

    stats = NoiseStats(
        median_noise_uv=10.5,
        mad_per_channel=[8.0, 9.0, 11.0],
        num_channels=3,
        sampling_rate_hz=30000.0,
        recording_duration_s=300.0,
        probe_type="neuropixels_2.0",
        channel_positions=[[0.0, 0.0], [0.0, 25.0], [32.0, 0.0]],
    )
    assert stats.num_channels == 3


def test_cluster_input_stats_schema():
    """ClusterInputStats validates all required fields."""
    from factory.workflow.spike_sort_schemas import ClusterInputStats

    stats = ClusterInputStats(
        spike_count=50000,
        drift_magnitude_um=12.5,
        feature_pca_variance_explained=[0.4, 0.15, 0.08],
        spike_density_per_channel_per_s=0.5,
        depth_range_um=3840.0,
        probe_type="neuropixels_2.0",
        channel_count=384,
    )
    assert stats.spike_count == 50000


def test_template_stats_schema():
    """TemplateStats validates stability bounds."""
    from factory.workflow.spike_sort_schemas import TemplateStats

    stats = TemplateStats(
        template_id=0,
        snr=5.0,
        spike_count=100,
        ptp_amplitude_uv=50.0,
        spatial_spread_um=75.0,
        stability=0.95,
    )
    assert stats.stability == 0.95

    with pytest.raises(Exception):
        TemplateStats(
            template_id=0,
            snr=5.0,
            spike_count=100,
            ptp_amplitude_uv=50.0,
            spatial_spread_um=75.0,
            stability=1.5,
        )


# ── Registration ─────────────────────────────────────────────────


def test_spike_sort_workflow_registered():
    """spike-sort is registered in register_all()."""
    from factory.workflow.definitions import register_all

    workflows = register_all()
    assert "spike-sort" in workflows


def test_spike_sort_workflow_in_meta():
    """spike-sort has a WORKFLOW_META entry."""
    from factory.workflow.skill_export import WORKFLOW_META

    assert "spike-sort" in WORKFLOW_META


# ── Skill export ─────────────────────────────────────────────────


def test_spike_sort_skill_export():
    """Verify spike-sort workflow generates a valid SKILL.md."""
    from factory.workflow.skill_export import workflow_to_skill_md

    wf = spike_sort_workflow()
    skill = workflow_to_skill_md(wf)
    assert "gate_post_cluster" in skill
    assert "gate_post_template" in skill
    assert "gate_post_match" in skill


# ── Trigger function ─────────────────────────────────────────────


def test_spike_sort_trigger():
    """Trigger fires when mode=spike-sort regardless of project state."""
    wf = spike_sort_workflow()
    assert wf.trigger is not None
    assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "spike-sort"})
    assert wf.trigger(ProjectState.NO_REPO, {"mode": "spike-sort"})
    assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
    assert not wf.trigger(ProjectState.HAS_FACTORY, {})


# ── CLI mode registration ───────────────────────────────────────


def test_spike_sort_in_ceo_modes():
    """spike-sort is in the CEO_MODES list."""
    from factory.cli._helpers import CEO_MODES

    assert "spike-sort" in CEO_MODES


def test_spike_sort_in_run_modes():
    """spike-sort is in the RUN_MODES list."""
    from factory.cli._helpers import RUN_MODES

    assert "spike-sort" in RUN_MODES


# ── Start node ───────────────────────────────────────────────────


def test_spike_sort_start_node():
    """Start node is preprocess."""
    wf = spike_sort_workflow()
    assert wf.start_node == "preprocess"


# ── Workflow name ────────────────────────────────────────────────


def test_spike_sort_workflow_name():
    """Workflow name is spike-sort."""
    wf = spike_sort_workflow()
    assert wf.name == "spike-sort"


# ── Input/output format ─────────────────────────────────────────


def test_spike_sort_input_output_format():
    """Pipeline start writes preprocessed/ and final node produces benchmark_result.json."""
    wf = spike_sort_workflow()

    first_node = wf.nodes[wf.start_node]
    assert "preprocessed/" in first_node.writes

    final_node = wf.nodes["evaluate_accuracy"]
    assert "benchmark_result.json" in final_node.writes


# ── Gate prompt constants ────────────────────────────────────────


def test_gate_prompt_constants_exist():
    """Gate prompt constants are defined and used by agent nodes."""
    from factory.workflow.definitions import (
        GATE_POST_CLUSTER_PROMPT,
        GATE_POST_MATCH_PROMPT,
        GATE_POST_TEMPLATE_PROMPT,
    )

    wf = spike_sort_workflow()
    assert wf.nodes["gate_post_cluster"].prompt_template == GATE_POST_CLUSTER_PROMPT
    assert wf.nodes["gate_post_template"].prompt_template == GATE_POST_TEMPLATE_PROMPT
    assert wf.nodes["gate_post_match"].prompt_template == GATE_POST_MATCH_PROMPT


def test_gate_prompts_are_context_first():
    """Gate prompts emphasize context-aware reasoning, not mechanical thresholds."""
    from factory.workflow.definitions import (
        GATE_POST_CLUSTER_PROMPT,
        GATE_POST_MATCH_PROMPT,
        GATE_POST_TEMPLATE_PROMPT,
    )

    assert "recording_context.json" in GATE_POST_CLUSTER_PROMPT
    assert "recording_context.json" in GATE_POST_TEMPLATE_PROMPT
    assert "recording_context.json" in GATE_POST_MATCH_PROMPT

    assert "context" in GATE_POST_CLUSTER_PROMPT.lower()
    assert "context" in GATE_POST_TEMPLATE_PROMPT.lower()
    assert "context" in GATE_POST_MATCH_PROMPT.lower()


# ── Recovery and accuracy node tests ──────────────────────────────


def test_spike_sort_recovery_node_properties():
    """Verify recover_low_snr_spikes node has correct reads/writes."""
    wf = spike_sort_workflow()

    recovery = wf.nodes["recover_low_snr_spikes"]
    assert isinstance(recovery, FnNode)
    assert recovery.callable_name == "factory.workflow.spike_sort_stages:recover_low_snr_spikes"
    assert "preprocessed/" in recovery.reads
    assert "sorting_final/" in recovery.reads
    assert "templates_refined/" in recovery.reads
    assert "sorting_final/" in recovery.writes
    assert "recovery_stats.json" in recovery.writes


def test_spike_sort_evaluate_accuracy_node_properties():
    """Verify evaluate_accuracy node has correct reads/writes."""
    wf = spike_sort_workflow()

    eval_node = wf.nodes["evaluate_accuracy"]
    assert isinstance(eval_node, FnNode)
    assert eval_node.callable_name == "factory.workflow.spike_sort_stages:evaluate_accuracy"
    assert "sorting_final/" in eval_node.reads
    assert "benchmark_result.json" in eval_node.writes


def test_spike_sort_recovery_edges():
    """Verify recovery and eval nodes are correctly wired."""
    wf = spike_sort_workflow()

    edge_pairs = [(e.source, e.target) for e in wf.edges]
    assert ("apply_final_actions", "recover_low_snr_spikes") in edge_pairs
    assert ("recover_low_snr_spikes", "evaluate_accuracy") in edge_pairs


def test_spike_sort_pipeline_ends_at_evaluate_accuracy():
    """Verify the pipeline terminates at evaluate_accuracy."""
    wf = spike_sort_workflow()

    outgoing = [e for e in wf.edges if e.source == "evaluate_accuracy"]
    assert len(outgoing) == 0

    assert wf.start_node == "preprocess"
