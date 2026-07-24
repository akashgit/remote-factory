"""Tests for the spike-sort workflow definition and schemas."""

from __future__ import annotations

import pytest

from factory.models import ProjectState
from factory.workflow.definitions import register_all, spike_sort_workflow
from factory.workflow.primitives import AgentNode, FnNode


# ── Graph validation ──────────────────────────────────────────────


def test_spike_sort_workflow_validates():
    """spike-sort graph passes structural validation."""
    wf = spike_sort_workflow()
    issues = wf.validate_graph()
    assert issues == [], f"Validation issues: {issues}"


# ── Node structure ────────────────────────────────────────────────


def test_spike_sort_has_10_nodes():
    """Spike-sort pipeline has exactly 10 nodes: 7 FnNodes + 3 AgentNodes."""
    wf = spike_sort_workflow()
    fn_nodes = [n for n in wf.nodes.values() if isinstance(n, FnNode)]
    agent_nodes = [n for n in wf.nodes.values() if isinstance(n, AgentNode)]
    assert len(fn_nodes) == 7, f"Expected 7 FnNodes, got {len(fn_nodes)}"
    assert len(agent_nodes) == 3, f"Expected 3 AgentNodes, got {len(agent_nodes)}"


def test_spike_sort_node_ids():
    """All expected node IDs are present."""
    wf = spike_sort_workflow()
    expected = {
        "preprocess",
        "detect_trial",
        "detect_params",
        "detect",
        "localize",
        "cluster_params",
        "cluster",
        "templates",
        "qc_templates",
        "match",
    }
    assert set(wf.nodes.keys()) == expected


# ── Linear pipeline structure ────────────────────────────────────


def test_spike_sort_is_linear():
    """Spike-sort has exactly 9 edges forming a linear chain."""
    wf = spike_sort_workflow()
    assert len(wf.edges) == 9

    expected_chain = [
        ("preprocess", "detect_trial"),
        ("detect_trial", "detect_params"),
        ("detect_params", "detect"),
        ("detect", "localize"),
        ("localize", "cluster_params"),
        ("cluster_params", "cluster"),
        ("cluster", "templates"),
        ("templates", "qc_templates"),
        ("qc_templates", "match"),
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


# ── Agent model assignment ────────────────────────────────────────


def test_spike_sort_agent_models():
    """Detection and QC use haiku; clustering uses sonnet."""
    wf = spike_sort_workflow()

    detect_params = wf.nodes["detect_params"]
    assert isinstance(detect_params, AgentNode)
    assert detect_params.model == "haiku"

    cluster_params = wf.nodes["cluster_params"]
    assert isinstance(cluster_params, AgentNode)
    assert cluster_params.model == "sonnet"

    qc_templates = wf.nodes["qc_templates"]
    assert isinstance(qc_templates, AgentNode)
    assert qc_templates.model == "haiku"


# ── FnNode callable names ────────────────────────────────────────


def test_spike_sort_fn_callables():
    """All FnNodes reference factory.workflow.spike_sort_stages callables."""
    wf = spike_sort_workflow()

    expected_callables = {
        "preprocess": "factory.workflow.spike_sort_stages:preprocess",
        "detect_trial": "factory.workflow.spike_sort_stages:detect_trial",
        "detect": "factory.workflow.spike_sort_stages:detect",
        "localize": "factory.workflow.spike_sort_stages:localize",
        "cluster": "factory.workflow.spike_sort_stages:cluster",
        "templates": "factory.workflow.spike_sort_stages:compute_templates",
        "match": "factory.workflow.spike_sort_stages:template_match",
    }
    for node_id, expected in expected_callables.items():
        node = wf.nodes[node_id]
        assert isinstance(node, FnNode)
        assert node.callable_name == expected, (
            f"{node_id}: expected {expected}, got {node.callable_name}"
        )


# ── Data flow connectivity ────────────────────────────────────────


def test_spike_sort_data_flow():
    """Each node's reads are a subset of predecessors' writes."""
    wf = spike_sort_workflow()

    node_order = [
        "preprocess",
        "detect_trial",
        "detect_params",
        "detect",
        "localize",
        "cluster_params",
        "cluster",
        "templates",
        "qc_templates",
        "match",
    ]

    available: set[str] = set()
    for nid in node_order:
        node = wf.nodes[nid]
        missing = node.reads - available
        assert not missing, f"Node '{nid}' reads {missing} but only {available} available"
        available |= node.writes


# ── Schema validation ─────────────────────────────────────────────


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


# ── detect_trial node ────────────────────────────────────────────


def test_detect_trial_node_exists():
    """detect_trial FnNode is present with correct callable and data flow."""
    wf = spike_sort_workflow()
    node = wf.nodes["detect_trial"]
    assert isinstance(node, FnNode)
    assert node.callable_name == "factory.workflow.spike_sort_stages:detect_trial"
    assert node.reads == {"preprocessed/", "noise_stats.json"}
    assert node.writes == {"trial_results.json"}


def test_detect_trial_edge_wiring():
    """preprocess → detect_trial → detect_params edge chain is correct."""
    wf = spike_sort_workflow()
    edges = [(e.source, e.target) for e in wf.edges]
    assert ("preprocess", "detect_trial") in edges
    assert ("detect_trial", "detect_params") in edges
    assert ("preprocess", "detect_params") not in edges


def test_detect_node_writes_denoised():
    """detect FnNode writes include denoised/ directory."""
    wf = spike_sort_workflow()
    detect_node = wf.nodes["detect"]
    assert isinstance(detect_node, FnNode)
    assert "denoised/" in detect_node.writes


def test_detect_params_reads_trial_results():
    """detect_params AgentNode reads trial_results.json."""
    wf = spike_sort_workflow()
    node = wf.nodes["detect_params"]
    assert isinstance(node, AgentNode)
    assert "trial_results.json" in node.reads


# ── Registration ──────────────────────────────────────────────────


def test_spike_sort_workflow_registered():
    """spike-sort is registered in register_all()."""
    workflows = register_all()
    assert "spike-sort" in workflows


def test_spike_sort_workflow_in_meta():
    """spike-sort has a WORKFLOW_META entry."""
    from factory.workflow.skill_export import WORKFLOW_META

    assert "spike-sort" in WORKFLOW_META


# ── SpikeInterface compatibility ──────────────────────────────────


def test_spike_sort_input_output_format():
    """Pipeline start writes preprocessed/ and final node produces sorting/."""
    wf = spike_sort_workflow()

    first_node = wf.nodes[wf.start_node]
    assert "preprocessed/" in first_node.writes

    last_node = wf.nodes["match"]
    assert "sorting/" in last_node.writes


# ── Trigger function ──────────────────────────────────────────────


def test_spike_sort_trigger():
    """Trigger fires when mode=spike-sort regardless of project state."""
    wf = spike_sort_workflow()
    assert wf.trigger is not None
    assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "spike-sort"})
    assert wf.trigger(ProjectState.NO_REPO, {"mode": "spike-sort"})
    assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
    assert not wf.trigger(ProjectState.HAS_FACTORY, {})


# ── CLI mode registration ────────────────────────────────────────


def test_spike_sort_in_ceo_modes():
    """spike-sort is in the CEO_MODES list."""
    from factory.cli._helpers import CEO_MODES

    assert "spike-sort" in CEO_MODES


def test_spike_sort_in_run_modes():
    """spike-sort is in the RUN_MODES list."""
    from factory.cli._helpers import RUN_MODES

    assert "spike-sort" in RUN_MODES


# ── Start node ────────────────────────────────────────────────────


def test_spike_sort_start_node():
    """Start node is preprocess."""
    wf = spike_sort_workflow()
    assert wf.start_node == "preprocess"


# ── Workflow name ─────────────────────────────────────────────────


def test_spike_sort_workflow_name():
    """Workflow name is spike-sort."""
    wf = spike_sort_workflow()
    assert wf.name == "spike-sort"
