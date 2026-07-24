"""Pydantic schemas for spike-sort workflow agent I/O contracts.

Each schema defines the structured output contract between AgentNodes
and downstream FnNodes. AgentNodes use these as output schemas, validated
at the tool-call layer so the model retries on mismatch.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NoiseStats(BaseModel):
    """Input to detect_params AgentNode. Computed by preprocess FnNode."""

    model_config = ConfigDict(strict=True, extra="forbid")

    median_noise_uv: float = Field(description="Median noise level in µV across channels")
    mad_per_channel: list[float] = Field(description="MAD noise estimate per channel")
    num_channels: int
    sampling_rate_hz: float
    recording_duration_s: float
    probe_type: str = Field(description="Probe identifier, e.g. 'neuropixels_2.0'")
    channel_positions: list[list[float]] = Field(description="(C, 2) channel positions in µm")


class DetectionParams(BaseModel):
    """Output of detect_params AgentNode. Consumed by detect FnNode."""

    model_config = ConfigDict(strict=True, extra="forbid")

    voltage_threshold: float = Field(
        ge=3.0,
        le=6.0,
        description="SNR threshold for spike detection (4.0 is DARTsort calibrated default)",
    )
    peak_sign: Literal["neg", "pos", "both"] = Field(description="Which polarity peaks to detect")
    dedup_temporal_radius: int = Field(
        ge=5,
        le=20,
        description="Temporal deduplication radius in samples",
    )
    use_denoiser: bool = Field(
        default=True,
        description="Apply single-channel denoiser NN before detection (recommended)",
    )
    reasoning: str = Field(description="Brief justification for parameter choices")


class ClusterInputStats(BaseModel):
    """Input to cluster_params AgentNode. Computed by localize FnNode."""

    model_config = ConfigDict(strict=True, extra="forbid")

    spike_count: int
    drift_magnitude_um: float = Field(description="Estimated drift range in µm")
    feature_pca_variance_explained: list[float] = Field(
        description="Variance explained by first N PCs",
    )
    spike_density_per_channel_per_s: float
    depth_range_um: float = Field(description="Span of spike depths in µm")
    probe_type: str
    channel_count: int


class ClusteringParams(BaseModel):
    """Output of cluster_params AgentNode. Consumed by cluster FnNode."""

    model_config = ConfigDict(strict=True, extra="forbid")

    strategy: Literal["channel_snap", "grid_snap", "dpc", "none"]
    grid_dx: float = Field(ge=5.0, le=50.0, description="Spatial grid X resolution in µm")
    grid_dz: float = Field(ge=5.0, le=50.0, description="Spatial grid Z resolution in µm")
    initial_steps: list[Literal["split", "merge", "demolish"]]
    n_waveforms_fit: int = Field(
        ge=10000,
        le=100000,
        description="Number of waveforms to subsample for fitting",
    )
    reasoning: str = Field(description="Brief justification for strategy choice")


class TemplateStats(BaseModel):
    """Per-template quality metrics. Input to qc_templates AgentNode."""

    model_config = ConfigDict(strict=True, extra="forbid")

    template_id: int
    snr: float = Field(description="Signal-to-noise ratio of template")
    spike_count: int = Field(description="Number of spikes assigned to this unit")
    ptp_amplitude_uv: float = Field(description="Peak-to-peak amplitude in µV")
    spatial_spread_um: float = Field(description="Spatial extent of template in µm")
    stability: float = Field(
        ge=0.0,
        le=1.0,
        description="Waveform stability across time (1.0 = perfectly stable)",
    )


class TemplateDecision(BaseModel):
    """Per-template QC decision. Part of qc_templates AgentNode output."""

    model_config = ConfigDict(strict=True, extra="forbid")

    template_id: int
    action: Literal["keep", "discard", "merge"]
    merge_with: int | None = None
    reasoning: str = Field(description="Brief justification for this decision")


class TemplateQCOutput(BaseModel):
    """Output of qc_templates AgentNode. Consumed by match FnNode."""

    model_config = ConfigDict(strict=True, extra="forbid")

    decisions: list[TemplateDecision]
    summary: str = Field(description="Overall QC summary: how many kept, discarded, merged")
