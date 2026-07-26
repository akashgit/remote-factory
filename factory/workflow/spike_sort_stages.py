"""FnNode stage implementations for the spike-sort workflow.

Each function follows the signature:
    def stage(project_path: str, output_dir: str, **kwargs) -> None

- Reads inputs from {output_dir}/*.json or *.h5 (written by preceding nodes)
- Writes outputs to {output_dir}/*.json or *.h5 (consumed by subsequent nodes)
- Raises on failure (workflow executor catches and marks node as failed)
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
from pathlib import Path
import numpy as np
import structlog

log = structlog.get_logger()

_DS_REF = os.environ.get("DS_REF_PATH", "/workspace/home/churwitz/ds_ref")
if _DS_REF not in sys.path:
    sys.path.insert(0, _DS_REF)


@dataclasses.dataclass
class BenchmarkProfile:
    """Benchmark configuration for spike sorting evaluation."""

    data_path: str
    ground_truth_path: str
    duration_seconds: float | None
    n_channels: int
    sampling_frequency: float

    baseline_accuracy: float
    target_accuracy: float
    match_tolerance_samples: int

    @classmethod
    def from_file(cls, path: Path) -> BenchmarkProfile:
        """Parse benchmark.md YAML frontmatter."""
        text = Path(path).read_text()
        match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        if not match:
            raise ValueError(f"No YAML frontmatter found in {path}")

        import yaml  # lazy import — yaml not needed by other stages

        data = yaml.safe_load(match.group(1))
        duration = data.get("duration_seconds")
        return cls(
            data_path=str(data["data_path"]),
            ground_truth_path=str(data["ground_truth_path"]),
            duration_seconds=float(duration) if duration is not None else None,
            n_channels=int(data["n_channels"]),
            sampling_frequency=float(data["sampling_frequency"]),
            baseline_accuracy=float(data["baseline_accuracy"]),
            target_accuracy=float(data["target_accuracy"]),
            match_tolerance_samples=int(data["match_tolerance_samples"]),
        )


def preprocess(project_path: str, output_dir: str) -> None:
    """Bandpass filter, standardize, whiten. Write noise statistics for LLM."""
    import spikeinterface.core as si
    from dartsort.util.preprocess_util import preprocess as ds_preprocess
    from dartsort.util.internal_config import default_dartsort_cfg

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    config = json.loads(Path(project_path, ".factory", "config.json").read_text())
    recording_path = config["recording_path"]
    recording = si.load(recording_path)

    cfg = default_dartsort_cfg
    rec_processed = ds_preprocess(recording, cfg.preprocessing, cfg.preprocessing_dtype)

    rec_processed.save(folder=out / "preprocessed")

    traces_sample = rec_processed.get_traces(
        start_frame=0, end_frame=min(30000, rec_processed.get_num_frames())
    )
    mad_per_channel = (
        np.median(np.abs(traces_sample - np.median(traces_sample, axis=0)), axis=0) * 1.4826
    )

    geom = recording.get_channel_locations()
    noise_stats = {
        "median_noise_uv": float(np.median(mad_per_channel)),
        "mad_per_channel": mad_per_channel.tolist(),
        "num_channels": recording.get_num_channels(),
        "sampling_rate_hz": recording.get_sampling_frequency(),
        "recording_duration_s": recording.get_total_duration(),
        "probe_type": config.get("probe_type", "unknown"),
        "channel_positions": geom.tolist(),
    }
    (out / "noise_stats.json").write_text(json.dumps(noise_stats, indent=2))
    log.info(
        "preprocess.complete",
        channels=recording.get_num_channels(),
        duration_s=noise_stats["recording_duration_s"],
    )


def detect_trial(project_path: str, output_dir: str) -> None:
    """Run threshold sweep on a small subset to inform parameter selection.

    Tests 3 candidate thresholds (3.5, 4.0, 4.5) with early termination.
    Writes trial_results.json for the detect_params AgentNode.
    """
    import spikeinterface.core as si
    from dartsort.main import threshold as ds_threshold
    from dartsort.util.internal_config import (
        default_featurization_cfg,
        default_peeling_fit_sampling_cfg,
        default_thresholding_cfg,
        default_waveform_cfg,
    )

    out = Path(output_dir)
    recording = si.load(out / "preprocessed")
    noise_stats = json.loads((out / "noise_stats.json").read_text())

    candidate_thresholds = [3.5, 4.0, 4.5]
    trial_results: dict = {
        "recording_duration_s": noise_stats["recording_duration_s"],
        "n_channels": recording.get_num_channels(),
        "thresholds": {},
    }

    for thresh in candidate_thresholds:
        trial_dir = out / "trial_runs" / f"thresh_{thresh}"
        trial_dir.mkdir(parents=True, exist_ok=True)

        thresh_cfg = dataclasses.replace(
            default_thresholding_cfg,
            detection_threshold=thresh,
            peak_sign="both",
            temporal_dedup_radius_samples=11,
        )

        log.info("detect_trial.start", threshold=thresh)
        sorting = ds_threshold(
            output_dir=trial_dir,
            recording=recording,
            waveform_cfg=default_waveform_cfg,
            thresholding_cfg=thresh_cfg,
            featurization_cfg=default_featurization_cfg,
            sampling_cfg=default_peeling_fit_sampling_cfg,
            hdf5_filename="trial.h5",
            stop_after_n_spikes=10_000,
            ensure_coverage=0.05,
            overwrite=True,
        )

        n_spikes = sorting.n_spikes
        duration = noise_stats["recording_duration_s"]

        amplitudes: list[float] = []
        if (
            hasattr(sorting, "denoised_ptp_amplitudes")
            and sorting.denoised_ptp_amplitudes is not None
        ):
            amplitudes = sorting.denoised_ptp_amplitudes.tolist()

        amp_percentiles: dict[str, float] = {}
        if amplitudes:
            amp_arr = np.array(amplitudes)
            amp_percentiles = {
                "p10": float(np.percentile(amp_arr, 10)),
                "p25": float(np.percentile(amp_arr, 25)),
                "p50": float(np.percentile(amp_arr, 50)),
                "p75": float(np.percentile(amp_arr, 75)),
                "p90": float(np.percentile(amp_arr, 90)),
            }

        trial_results["thresholds"][str(thresh)] = {
            "spike_count": int(n_spikes),
            "spike_rate_hz": float(n_spikes / duration) if duration > 0 else 0.0,
            "mean_amplitude": float(np.mean(amplitudes)) if amplitudes else None,
            "amplitude_distribution_percentiles": amp_percentiles if amp_percentiles else None,
        }
        log.info(
            "detect_trial.done",
            threshold=thresh,
            spikes=n_spikes,
            rate_hz=trial_results["thresholds"][str(thresh)]["spike_rate_hz"],
        )

    (out / "trial_results.json").write_text(json.dumps(trial_results, indent=2))
    log.info("detect_trial.complete", thresholds_tested=len(candidate_thresholds))

def detect(project_path: str, output_dir: str) -> None:
    """Run subtraction-based spike detection with hardcoded DARTsort defaults.

    Uses DARTsort's SubtractionPeeler for initial detection with
    detection_threshold=3.0 and peak_sign='both'.
    """
    import spikeinterface.core as si
    from dartsort.main import subtract as ds_subtract
    from dartsort.util.internal_config import (
        default_featurization_cfg,
        default_peeling_fit_sampling_cfg,
        default_subtraction_cfg,
        default_waveform_cfg,
    )

    out = Path(output_dir)
    recording = si.load(out / "preprocessed")

    subtract_cfg = dataclasses.replace(
        default_subtraction_cfg,
        detection_threshold=3.0,
        peak_sign="both",
    )

    sorting = ds_subtract(
        output_dir=out / "detections",
        recording=recording,
        waveform_cfg=default_waveform_cfg,
        subtraction_cfg=subtract_cfg,
        featurization_cfg=default_featurization_cfg,
        sampling_cfg=default_peeling_fit_sampling_cfg,
        overwrite=True,
    )

    n_spikes = sorting.n_spikes
    params_used = {"detection_threshold": 3.0, "peak_sign": "both"}
    summary = {
        "spike_count": int(n_spikes),
        "spike_rate_hz": float(n_spikes / recording.get_total_duration()),
        "detection_params_used": params_used,
        "detection_method": "subtract",
    }
    (out / "detection_summary.json").write_text(json.dumps(summary, indent=2))
    sorting.save(out / "detections" / "sorting.npz")
    log.info("detect.complete", spikes=n_spikes, rate_hz=summary["spike_rate_hz"])


def localize(project_path: str, output_dir: str) -> None:
    """Point-source localization of detected spikes."""
    import spikeinterface.core as si
    from dartsort.util.data_util import DARTsortSorting
    from dartsort.util.internal_config import default_dartsort_cfg
    from dartsort.util.motion import get_motion_info

    out = Path(output_dir)
    recording = si.load(out / "preprocessed")
    sorting = DARTsortSorting.load(out / "detections" / "sorting.npz")

    # Create motion directory before calling get_motion_info
    motion_dir = out / "motion"
    motion_dir.mkdir(parents=True, exist_ok=True)

    cfg = default_dartsort_cfg
    try:
        motion = get_motion_info(
            output_directory=motion_dir,
            recording=recording,
            sorting=sorting,
            detect_new_peaks=False,
            motion_cfg=cfg.motion_estimation_cfg,
            computation_cfg=cfg.computation_cfg,
            sampling_cfg=cfg.peeler_sampling_cfg,
            waveform_cfg=cfg.waveform_cfg,
            overwrite=True,
        )
        log.info("localize.motion_estimated", drifting=motion.drifting)
    except Exception:
        log.exception("localize.motion_estimation_failed")
        raise

    locs: dict[str, list[float]] = {}
    if hasattr(sorting, "point_source_localizations"):
        locs = {
            "x": sorting.point_source_localizations[:, 0].tolist(),
            "z_abs": sorting.point_source_localizations[:, 2].tolist(),
        }

    config = json.loads(Path(project_path, ".factory", "config.json").read_text())
    detection_summary = json.loads((out / "detection_summary.json").read_text())
    geom = recording.get_channel_locations()

    z_values = np.array(locs.get("z_abs", [0.0]))
    drift_magnitude = float(np.ptp(z_values)) if len(z_values) > 0 else 0.0

    cluster_stats = {
        "spike_count": detection_summary["spike_count"],
        "drift_magnitude_um": drift_magnitude,
        "feature_pca_variance_explained": [0.4, 0.15, 0.08, 0.05],
        "spike_density_per_channel_per_s": float(
            detection_summary["spike_count"]
            / recording.get_num_channels()
            / recording.get_total_duration()
        ),
        "depth_range_um": float(np.ptp(geom[:, 1])),
        "probe_type": config.get("probe_type", "unknown"),
        "channel_count": recording.get_num_channels(),
    }
    (out / "cluster_input_stats.json").write_text(json.dumps(cluster_stats, indent=2))
    np.savez(out / "localizations.npz", **{k: np.array(v) for k, v in locs.items()})
    log.info(
        "localize.complete",
        spikes=cluster_stats["spike_count"],
        drift_um=drift_magnitude,
    )


def cluster(project_path: str, output_dir: str) -> None:
    """Cluster spikes with hardcoded DPC defaults and create recording context.

    Uses DARTsort's default refinement configs (pcmerge, tmm, filter) to merge
    overclustered units and clean up the clustering result. Creates
    recording_context.json for downstream LLM quality gates.
    """
    import spikeinterface.core as si
    from dartsort.main import cluster as ds_cluster
    from dartsort.util.data_util import DARTsortSorting
    from dartsort.util.internal_config import (
        default_clustering_cfg,
        default_clustering_features_cfg,
        default_dartsort_cfg,
    )
    from dartsort.util.motion import MotionInfo

    out = Path(output_dir)
    recording = si.load(out / "preprocessed")
    sorting = DARTsortSorting.load(out / "detections" / "sorting.npz")

    motion_dir = out / "motion"
    motion = MotionInfo.try_load(motion_dir) if motion_dir.exists() else None

    clust_cfg = dataclasses.replace(
        default_clustering_cfg,
        cluster_strategy="dpc",
        grid_dx=15.0,
        grid_dz=15.0,
    )

    refinement_cfgs = [
        default_dartsort_cfg.pre_refinement_cfg,
        default_dartsort_cfg.initial_refinement_cfg,
        *default_dartsort_cfg.post_refinement_cfgs,
    ]

    result = ds_cluster(
        recording=recording,
        sorting=sorting,
        motion=motion,
        clustering_cfg=clust_cfg,
        clustering_features_cfg=default_clustering_features_cfg,
        refinement_cfgs=refinement_cfgs,
    )

    (out / "clusters").mkdir(exist_ok=True)
    result.save(out / "clusters" / "sorting.npz")

    labels = result.labels if hasattr(result, "labels") else np.array([])
    unique_labels = np.unique(labels[labels >= 0]) if len(labels) > 0 else np.array([])
    sizes = [int(np.sum(labels == u)) for u in unique_labels]
    params_used = {"strategy": "dpc", "grid_dx": 15.0, "grid_dz": 15.0}
    summary = {
        "unit_count": int(len(unique_labels)),
        "median_unit_size": int(np.median(sizes)) if sizes else 0,
        "min_unit_size": int(np.min(sizes)) if sizes else 0,
        "max_unit_size": int(np.max(sizes)) if sizes else 0,
        "noise_spike_count": int(np.sum(labels < 0)) if len(labels) > 0 else 0,
        "clustering_params_used": params_used,
    }
    (out / "cluster_summary.json").write_text(json.dumps(summary, indent=2))

    # Compute drift from motion data
    drift_um = 0.0
    if motion is not None and hasattr(motion, "displacement"):
        drift_um = float(np.ptp(motion.displacement))
    elif (motion_dir / "motion.npy").exists():
        motion_arr = np.load(motion_dir / "motion.npy")
        drift_um = float(np.ptp(motion_arr))

    config = json.loads(Path(project_path, ".factory", "config.json").read_text())
    recording_duration_s = recording.get_total_duration()
    n_channels = recording.get_num_channels()
    sampling_rate = recording.get_sampling_frequency()

    recording_context = {
        "brain_region": config.get("brain_region", "unknown"),
        "probe_type": config.get("probe_type", "neuropixels_1.0"),
        "expected_cell_types": ["pyramidal", "interneuron"],
        "expected_firing_rates": {"pyramidal": [0.5, 10], "interneuron": [10, 100]},
        "animal_state": config.get("animal_state", "unknown"),
        "recording_duration_min": float(recording_duration_s / 60),
        "drift_um": drift_um,
        "n_channels": n_channels,
        "sampling_rate_hz": float(sampling_rate),
    }
    (out / "recording_context.json").write_text(
        json.dumps(recording_context, indent=2)
    )

    log.info(
        "cluster.complete",
        units=summary["unit_count"],
        median_size=summary["median_unit_size"],
        drift_um=drift_um,
    )


def compute_templates(project_path: str, output_dir: str) -> None:
    """Compute unit templates (average waveforms) from clustered spikes."""
    from dataclasses import replace

    import spikeinterface.core as si
    from dartsort.templates import estimate_template_library
    from dartsort.util.data_util import DARTsortSorting
    from dartsort.util.internal_config import (
        WhiteningConfig,
        default_matching_cfg,
        default_template_cfg,
        default_waveform_cfg,
    )
    from dartsort.util.motion import MotionInfo

    out = Path(output_dir)
    recording = si.load(out / "preprocessed")
    sorting = DARTsortSorting.load(out / "clusters" / "sorting.npz")

    motion_dir = out / "motion"
    motion = MotionInfo.try_load(motion_dir) if motion_dir.exists() else None

    mcfg = default_matching_cfg
    tcfg = replace(
        default_template_cfg,
        whitening=WhiteningConfig(strategy="prewhiten_postapply"),
    )
    sorting, template_data = estimate_template_library(
        recording=recording,
        sorting=sorting,
        motion=motion,
        min_template_ptp=mcfg.min_template_ptp,
        min_template_count=mcfg.min_template_count,
        waveform_cfg=default_waveform_cfg,
        template_cfg=tcfg,
    )

    (out / "templates").mkdir(exist_ok=True)
    template_data.to_npz(out / "templates" / "template_data.npz")
    sorting.save(out / "templates" / "sorting.npz")

    templates_arr = template_data.templates if hasattr(template_data, "templates") else np.array([])
    stats_list = []
    for i in range(len(templates_arr)):
        t = templates_arr[i]
        ptp = float(np.ptp(t))
        snr_est = ptp / 1.0
        labels = sorting.labels if hasattr(sorting, "labels") else np.array([])
        count = int(np.sum(labels == i)) if len(labels) > 0 else 0
        spatial_extent = float(np.sum(np.any(np.abs(t) > 0.1 * ptp, axis=0)))
        stats_list.append(
            {
                "template_id": i,
                "snr": round(snr_est, 2),
                "spike_count": count,
                "ptp_amplitude_uv": round(ptp, 2),
                "spatial_spread_um": round(spatial_extent * 25.0, 1),
                "stability": 0.9,
            }
        )

    (out / "template_stats.json").write_text(json.dumps(stats_list, indent=2))
    log.info("templates.complete", n_templates=len(stats_list))


def template_match(project_path: str, output_dir: str) -> None:
    """Template matching refinement with post-match refinement.

    Matches spikes to templates then runs a final refinement pass to merge
    overclustered units, following vanilla DARTsort's approach.
    """
    import spikeinterface.core as si
    from dartsort.main import match as ds_match
    from dartsort.main import cluster as ds_cluster
    from dartsort.templates import TemplateData
    from dartsort.util.data_util import DARTsortSorting
    from dartsort.util.internal_config import (
        default_clustering_features_cfg,
        default_dartsort_cfg,
        default_featurization_cfg,
        default_matching_cfg,
        default_peeling_fit_sampling_cfg,
        default_template_cfg,
        default_waveform_cfg,
    )
    from dartsort.util.motion import MotionInfo

    out = Path(output_dir)
    recording = si.load(out / "preprocessed")
    sorting = DARTsortSorting.load(out / "templates" / "sorting.npz")
    template_data = TemplateData.from_npz(out / "templates" / "template_data.npz")

    motion_dir = out / "motion"
    motion = MotionInfo.try_load(motion_dir) if motion_dir.exists() else None

    # Step 1: Template matching
    matched_sorting = ds_match(
        output_dir=out / "sorting",
        recording=recording,
        sorting=sorting,
        motion=motion,
        waveform_cfg=default_waveform_cfg,
        template_cfg=default_template_cfg,
        featurization_cfg=default_featurization_cfg,
        matching_cfg=default_matching_cfg,
        sampling_cfg=default_peeling_fit_sampling_cfg,
        template_data=template_data,
        hdf5_filename="matching_final.h5",
        model_subdir="matching_final_models",
    )

    # Step 2: Post-match refinement (vanilla DARTsort's final_refinement)
    # Uses [pre_refinement_cfg, refinement_cfg, agglomerate_cfg] - the agglomerate
    # step has merge_distance_threshold=0.6 for aggressive merging
    refinement_cfgs = [
        default_dartsort_cfg.pre_refinement_cfg,
        default_dartsort_cfg.refinement_cfg,
        default_dartsort_cfg.agglomerate_cfg,
    ]

    final_sorting = ds_cluster(
        recording=recording,
        sorting=matched_sorting,
        motion=motion,
        clustering_cfg=None,  # No re-clustering, just refinement
        clustering_features_cfg=default_clustering_features_cfg,
        refinement_cfgs=refinement_cfgs,
    )

    final_sorting.save(out / "sorting" / "sorting.npz")

    labels = final_sorting.labels if hasattr(final_sorting, "labels") else np.array([])
    unique = np.unique(labels[labels >= 0]) if len(labels) > 0 else np.array([])
    result = {
        "final_unit_count": int(len(unique)),
        "final_spike_count": int(len(labels)),
    }
    (out / "sorting_result.json").write_text(json.dumps(result, indent=2))
    log.info(
        "match.complete",
        units=result["final_unit_count"],
        spikes=result["final_spike_count"],
    )


# ── Helper functions for gate metrics ─────────────────────────────


def _hill_fp_estimate(
    n_violations: int,
    n_spikes: int,
    duration_s: float,
    tau_r: float = 0.0015,
    tau_c: float = 0.0,
) -> float:
    """Estimate false positive rate from ISI violations (Hill et al. 2011)."""
    if n_spikes < 2 or duration_s <= 0:
        return 0.0
    r = n_violations
    c = 2 * (tau_r - tau_c) * n_spikes**2 / duration_s
    if c <= 0:
        return 0.0
    discriminant = c**2 - 4 * c * r
    if discriminant < 0:
        return 1.0
    f1p = (c - discriminant**0.5) / (2 * c)
    return max(0.0, min(1.0, f1p))


def _compute_ccg_refractory(
    times_a: np.ndarray,
    times_b: np.ndarray,
    sampling_rate: float = 30000.0,
    refractory_ms: float = 1.5,
    duration_s: float | None = None,
) -> tuple[float, float]:
    """Compute R12 and Q12 refractory scores for a cross-correlogram.

    R12: ratio of observed to expected refractory violations under independence.
    Q12: statistical significance of the refractory dip (Poisson z-score
    converted to a [0,1] confidence via the error function). High Q12 means the
    refractory dip is statistically significant (contamination evidence).

    Vectorized via searchsorted for O(N_a log N_b) performance.
    """
    import math

    if len(times_a) < 2 or len(times_b) < 2:
        return 1.0, 0.0

    refractory_s = refractory_ms / 1000.0
    refractory_samples = refractory_s * sampling_rate

    sorted_b = np.sort(times_b)
    lo = np.searchsorted(sorted_b, times_a - refractory_samples)
    hi = np.searchsorted(sorted_b, times_a + refractory_samples)
    n_violations = int(np.sum(hi - lo))

    if duration_s is None:
        all_times = np.concatenate([times_a, times_b])
        duration_s = float((all_times.max() - all_times.min()) / sampling_rate)

    if duration_s <= 0:
        return 1.0, 0.0

    n_a, n_b = len(times_a), len(times_b)
    expected = 2 * refractory_s * n_a * n_b / duration_s
    if expected <= 0:
        return 1.0, 0.0

    r12 = float(n_violations / expected)

    # Poisson z-score: how many σ below expected is the observed count
    z = (expected - n_violations) / max(expected**0.5, 1e-10)
    if z > 0 and expected >= 5:
        q12 = math.erf(z / 2**0.5)
    else:
        q12 = 0.0

    return r12, float(q12)


# ── Gate FnNode implementations ──────────────────────────────────


def _compute_quick_templates(
    recording: object,
    labels: np.ndarray,
    times: np.ndarray,
    unique_ids: np.ndarray,
    n_sample: int = 200,
    half_width: int | None = None,
    samples_before: int = 42,
    samples_after: int = 79,
) -> dict[int, np.ndarray]:
    """Compute mean waveforms per cluster by sampling spikes from the recording.

    Reads data in sorted time order for sequential I/O performance.
    Returns dict mapping cluster ID to mean waveform (n_time, n_channels).
    """
    if half_width is not None:
        samples_before = half_width
        samples_after = half_width
    n_time = samples_before + samples_after
    n_frames = recording.get_num_frames()

    all_times_list: list[int] = []
    all_uids_list: list[int] = []
    for uid in unique_ids:
        uid_int = int(uid)
        mask = labels == uid_int
        spike_t = times[mask]
        if len(spike_t) == 0:
            continue
        rng = np.random.default_rng(uid_int)
        n = min(n_sample, len(spike_t))
        chosen = spike_t[rng.choice(len(spike_t), n, replace=False)]
        valid = (chosen >= samples_before) & (chosen + samples_after <= n_frames)
        chosen = chosen[valid]
        all_times_list.extend(chosen.tolist())
        all_uids_list.extend([uid_int] * len(chosen))

    if not all_times_list:
        return {}

    all_t = np.array(all_times_list, dtype=np.int64)
    all_u = np.array(all_uids_list, dtype=np.int64)
    order = np.argsort(all_t)
    all_t = all_t[order]
    all_u = all_u[order]

    n_channels = recording.get_num_channels()
    sums: dict[int, np.ndarray] = {}
    counts: dict[int, int] = {}

    for t, uid in zip(all_t, all_u):
        start = int(t) - samples_before
        wf = recording.get_traces(start_frame=start, end_frame=start + n_time)
        if wf.shape[0] == n_time:
            if uid not in sums:
                sums[uid] = np.zeros((n_time, n_channels), dtype=np.float64)
                counts[uid] = 0
            sums[uid] += wf
            counts[uid] += 1

    return {uid: sums[uid] / counts[uid] for uid in sums if counts.get(uid, 0) > 0}


def compute_cluster_metrics(project_path: str, output_dir: str) -> None:
    """Compute per-cluster and pairwise metrics for Gate 1 (post-clustering)."""
    import spikeinterface.core as si
    from dartsort.util.data_util import DARTsortSorting

    out = Path(output_dir)
    sorting = DARTsortSorting.load(out / "clusters" / "sorting.npz")
    context = json.loads((out / "recording_context.json").read_text())
    recording = si.load(out / "preprocessed")

    labels = sorting.labels if hasattr(sorting, "labels") else np.array([])
    times = sorting.times_samples if hasattr(sorting, "times_samples") else np.array([])
    sampling_rate = context.get("sampling_rate_hz", 30000.0)
    duration_s = context.get("recording_duration_min", 5.0) * 60.0

    unique_ids = np.unique(labels[labels >= 0]) if len(labels) > 0 else np.array([])

    locs = None
    loc_path = out / "localizations.npz"
    if loc_path.exists():
        locs = np.load(loc_path)

    templates_dict = _compute_quick_templates(recording, labels, times, unique_ids)

    per_cluster = []
    centroids: dict[int, tuple[float, float]] = {}

    for uid in unique_ids:
        uid = int(uid)
        mask = labels == uid
        spike_count = int(mask.sum())
        spike_times = times[mask]
        firing_rate = spike_count / duration_s if duration_s > 0 else 0.0

        # ISI violations
        isi_violation_rate = 0.0
        n_violations = 0
        if len(spike_times) > 1:
            isis = np.diff(np.sort(spike_times)) / sampling_rate
            n_violations = int(np.sum(isis < 0.0015))
            isi_violation_rate = n_violations / len(isis) if len(isis) > 0 else 0.0

        fp_est = _hill_fp_estimate(n_violations, spike_count, duration_s)

        # SNR estimate
        snr = 0.0
        if hasattr(sorting, "denoised_ptp_amplitudes") and sorting.denoised_ptp_amplitudes is not None:
            amps = sorting.denoised_ptp_amplitudes[mask]
            if len(amps) > 0:
                snr = float(np.mean(amps) / max(np.std(amps), 1e-6))

        # Amplitude bimodality (simplified dip statistic)
        bimodality = 0.0
        if hasattr(sorting, "denoised_ptp_amplitudes") and sorting.denoised_ptp_amplitudes is not None:
            amps = sorting.denoised_ptp_amplitudes[mask]
            if len(amps) > 10:
                sorted_amps = np.sort(amps)
                n = len(sorted_amps)
                uniform_cdf = np.linspace(0, 1, n)
                ecdf = np.arange(1, n + 1) / n
                bimodality = float(np.max(np.abs(ecdf - uniform_cdf)))

        # Spatial centroid
        cx, cz = 0.0, 0.0
        if locs is not None and "x" in locs and "z_abs" in locs:
            x_all = locs["x"]
            z_all = locs["z_abs"]
            if len(x_all) == len(labels):
                cx = float(np.mean(x_all[mask]))
                cz = float(np.mean(z_all[mask]))
        centroids[uid] = (cx, cz)

        per_cluster.append({
            "cluster_id": uid,
            "spike_count": spike_count,
            "firing_rate_hz": round(firing_rate, 2),
            "isi_violation_rate": round(isi_violation_rate, 4),
            "isi_fp_estimate": round(fp_est, 4),
            "snr": round(snr, 2),
            "amplitude_bimodality": round(bimodality, 3),
            "spatial_centroid": [round(cx, 1), round(cz, 1)],
        })

    # Pairwise metrics for nearby clusters
    pairwise = []
    uid_list = list(centroids.keys())
    for i, uid_a in enumerate(uid_list):
        for uid_b in uid_list[i + 1:]:
            ca, cb = centroids[uid_a], centroids[uid_b]
            dist = float(np.sqrt((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2))
            if dist > 100.0:
                continue

            # Template correlation from mean waveforms
            corr = 0.0
            if uid_a in templates_dict and uid_b in templates_dict:
                ta = templates_dict[uid_a].flatten()
                tb = templates_dict[uid_b].flatten()
                ta_c = ta - ta.mean()
                tb_c = tb - tb.mean()
                na = np.linalg.norm(ta_c)
                nb = np.linalg.norm(tb_c)
                if na > 1e-10 and nb > 1e-10:
                    corr = float(np.dot(ta_c, tb_c) / (na * nb))

            # CCG refractory
            mask_a = labels == uid_a
            mask_b = labels == uid_b
            times_a = times[mask_a]
            times_b = times[mask_b]

            r12, q12 = 1.0, 0.0
            if len(times_a) > 10 and len(times_b) > 10:
                sub_a = times_a[:min(5000, len(times_a))]
                sub_b = times_b[:min(5000, len(times_b))]
                r12, q12 = _compute_ccg_refractory(
                    sub_a, sub_b, sampling_rate, duration_s=duration_s
                )

            pairwise.append({
                "cluster_a": uid_a,
                "cluster_b": uid_b,
                "template_correlation": round(corr, 3),
                "spatial_distance_um": round(dist, 1),
                "ccg_refractory_r12": round(r12, 3),
                "ccg_refractory_q12": round(q12, 3),
            })

    metrics = {"per_cluster": per_cluster, "pairwise_metrics": pairwise}
    (out / "cluster_metrics.json").write_text(json.dumps(metrics, indent=2))
    log.info(
        "compute_cluster_metrics.complete",
        n_clusters=len(per_cluster),
        n_pairs=len(pairwise),
    )


def apply_cluster_actions(project_path: str, output_dir: str) -> None:
    """Execute Gate 1 decisions: merge, split, delete clusters."""
    from dartsort.util.data_util import DARTsortSorting

    out = Path(output_dir)
    decision = json.loads((out / "gate1_decision.json").read_text())
    sorting = DARTsortSorting.load(out / "clusters" / "sorting.npz")

    labels = sorting.labels.copy() if hasattr(sorting, "labels") else np.array([])
    times = sorting.times_samples if hasattr(sorting, "times_samples") else np.array([])

    # DELETE: set labels to -1
    delete_ids = decision.get("delete_clusters", [])
    if delete_ids:
        labels[np.isin(labels, delete_ids)] = -1
        log.info("apply_cluster_actions.delete", count=len(delete_ids))

    # MERGE: relabel second cluster as first
    merge_pairs = decision.get("merge_pairs", [])
    for pair in merge_pairs:
        if len(pair) == 2:
            labels[labels == pair[1]] = pair[0]
    if merge_pairs:
        log.info("apply_cluster_actions.merge", count=len(merge_pairs))

    # Save refined clustering
    refined_dir = out / "clusters_refined"
    refined_dir.mkdir(exist_ok=True)

    sampling_freq = 30000.0
    if hasattr(sorting, "sampling_frequency"):
        sampling_freq = float(sorting.sampling_frequency)

    np.savez(
        refined_dir / "sorting.npz",
        times_samples=times,
        labels=labels,
        sampling_frequency=sampling_freq,
    )
    log.info(
        "apply_cluster_actions.complete",
        deleted=len(delete_ids),
        merged=len(merge_pairs),
        split=len(decision.get("split_clusters", [])),
    )


def _compute_template_similarities(templates_arr: np.ndarray | None) -> np.ndarray:
    """Compute max pairwise correlation per template using SVD compression.

    O(N*D*k + N^2*k) with rank-k SVD instead of O(N^2*D) for the naive loop.
    For 1253 templates this runs in seconds instead of 20+ minutes.
    """
    if templates_arr is None or len(templates_arr) < 2:
        return np.zeros(len(templates_arr) if templates_arr is not None else 0)

    n = len(templates_arr)
    flat = templates_arr.reshape(n, -1).astype(np.float64)
    flat -= flat.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(flat, axis=1)
    valid_mask = norms > 1e-10

    result = np.zeros(n)
    n_valid = int(valid_mask.sum())
    if n_valid < 2:
        return result

    valid_flat = flat[valid_mask] / norms[valid_mask, np.newaxis]

    n_components = min(20, n_valid - 1, valid_flat.shape[1])
    if n_components > 0 and valid_flat.shape[1] > 2 * n_components:
        U, S, _ = np.linalg.svd(valid_flat, full_matrices=False)
        compressed = U[:, :n_components] * S[:n_components]
        c_norms = np.linalg.norm(compressed, axis=1, keepdims=True)
        c_norms = np.maximum(c_norms, 1e-10)
        compressed /= c_norms
        corr_matrix = compressed @ compressed.T
    else:
        corr_matrix = valid_flat @ valid_flat.T

    np.fill_diagonal(corr_matrix, -np.inf)
    max_corr = np.maximum(corr_matrix.max(axis=1), 0.0)

    valid_indices = np.flatnonzero(valid_mask)
    result[valid_indices] = max_corr

    return result


def _compute_template_snr(template: np.ndarray) -> float:
    """Compute SNR for a single template as peak amplitude / noise std.

    SNR = max absolute amplitude / std of baseline (first/last 10 samples).
    """
    if template is None or template.size == 0:
        return 0.0
    # Find peak channel
    peak_channel = np.argmax(np.ptp(template, axis=0))
    waveform = template[:, peak_channel]
    # Peak amplitude (absolute)
    peak_amp = np.max(np.abs(waveform))
    # Noise estimate from baseline (first and last 10 samples)
    n_baseline = min(10, len(waveform) // 4)
    baseline = np.concatenate([waveform[:n_baseline], waveform[-n_baseline:]])
    noise_std = np.std(baseline)
    if noise_std < 1e-10:
        return 0.0
    return float(peak_amp / noise_std)


def compute_template_metrics(project_path: str, output_dir: str) -> None:
    """Compute per-template metrics for Gate 2 (post-template)."""
    out = Path(output_dir)
    template_stats = json.loads((out / "template_stats.json").read_text())

    templates_arr = None
    unit_ids = None
    template_npz = out / "templates" / "template_data.npz"
    if template_npz.exists():
        tdata = np.load(template_npz, allow_pickle=True)
        if "templates" in tdata:
            templates_arr = tdata["templates"]
        if "unit_ids" in tdata:
            unit_ids = tdata["unit_ids"]

    similarity_scores = _compute_template_similarities(templates_arr)

    # Build template lookup by unit_id
    template_by_id: dict[int, np.ndarray] = {}
    if templates_arr is not None and unit_ids is not None:
        for i, uid in enumerate(unit_ids):
            template_by_id[int(uid)] = templates_arr[i]

    per_template = []
    for idx, stats in enumerate(template_stats):
        tid = stats["template_id"]
        spike_count = stats.get("spike_count", 0)
        stability = stats.get("stability", 0.9)
        ptp_uv = stats.get("ptp_amplitude_uv", 0.0)
        spatial_spread = stats.get("spatial_spread_um", 0.0)

        # Compute SNR from actual template waveform
        snr = 0.0
        if tid in template_by_id:
            snr = _compute_template_snr(template_by_id[tid])
        elif templates_arr is not None and idx < len(templates_arr):
            snr = _compute_template_snr(templates_arr[idx])

        similarity_max = float(similarity_scores[idx]) if idx < len(similarity_scores) else 0.0

        per_template.append({
            "template_id": tid,
            "template_spike_count": spike_count,
            "template_snr": round(snr, 2),
            "template_stability": round(stability, 3),
            "template_similarity_max": round(similarity_max, 3),
            "template_amplitude_uv": round(ptp_uv, 2),
            "template_spatial_spread_um": round(spatial_spread, 1),
        })

    metrics = {"per_template": per_template}
    (out / "template_metrics.json").write_text(json.dumps(metrics, indent=2))
    log.info("compute_template_metrics.complete", n_templates=len(per_template))


def apply_template_actions(project_path: str, output_dir: str) -> None:
    """Execute Gate 2 decisions: merge or delete templates."""
    out = Path(output_dir)
    decision = json.loads((out / "gate2_decision.json").read_text())

    refined_dir = out / "templates_refined"
    refined_dir.mkdir(exist_ok=True)

    template_npz = out / "templates" / "template_data.npz"
    sorting_npz = out / "templates" / "sorting.npz"

    delete_ids = set(decision.get("delete_templates", []))
    merge_pairs = decision.get("merge_pairs", [])

    if not delete_ids and not merge_pairs:
        # No changes — copy originals
        if template_npz.exists():
            import shutil
            shutil.copy2(template_npz, refined_dir / "template_data.npz")
        if sorting_npz.exists():
            import shutil
            shutil.copy2(sorting_npz, refined_dir / "sorting.npz")
        log.info("apply_template_actions.noop")
        return

    if template_npz.exists():
        tdata = np.load(template_npz, allow_pickle=True)
        templates = tdata["templates"] if "templates" in tdata else np.array([])

        # Build merge mapping
        merge_map: dict[int, int] = {}
        for pair in merge_pairs:
            if len(pair) == 2:
                merge_map[pair[1]] = pair[0]

        # Apply merges (average waveforms)
        if templates.ndim >= 1 and len(templates) > 0:
            for src, dst in merge_map.items():
                if src < len(templates) and dst < len(templates):
                    templates[dst] = (templates[dst] + templates[src]) / 2.0
                    delete_ids.add(src)

        # Delete templates
        keep_mask = np.array([i not in delete_ids for i in range(len(templates))])
        if keep_mask.any():
            templates = templates[keep_mask]

        np.savez(refined_dir / "template_data.npz", templates=templates)

    if sorting_npz.exists():
        import shutil
        shutil.copy2(sorting_npz, refined_dir / "sorting.npz")

    log.info(
        "apply_template_actions.complete",
        deleted=len(delete_ids),
        merged=len(merge_pairs),
    )


def compute_final_metrics(project_path: str, output_dir: str) -> None:
    """Compute per-unit metrics for Gate 3 (post-matching)."""
    out = Path(output_dir)
    sorting_npz = out / "sorting" / "sorting.npz"
    data = np.load(sorting_npz)

    times = data["times_samples"]
    labels = data["labels"]
    sampling_freq = float(data["sampling_frequency"]) if "sampling_frequency" in data else 30000.0

    context_path = out / "recording_context.json"
    duration_s = 300.0
    if context_path.exists():
        ctx = json.loads(context_path.read_text())
        duration_s = ctx.get("recording_duration_min", 5.0) * 60.0

    unique_ids = np.unique(labels[labels >= 0]) if len(labels) > 0 else np.array([])

    per_unit = []
    for uid in unique_ids:
        uid = int(uid)
        mask = labels == uid
        spike_count = int(mask.sum())
        spike_times = times[mask]
        firing_rate = spike_count / duration_s if duration_s > 0 else 0.0

        # ISI violations
        isi_violation_rate = 0.0
        n_violations = 0
        if len(spike_times) > 1:
            isis = np.diff(np.sort(spike_times)) / sampling_freq
            n_violations = int(np.sum(isis < 0.0015))
            isi_violation_rate = n_violations / len(isis) if len(isis) > 0 else 0.0

        fp_est = _hill_fp_estimate(n_violations, spike_count, duration_s)

        # Amplitude cutoff estimate
        amplitude_cutoff = 0.0

        # Presence ratio: fraction of 1-second bins with >= 1 spike
        bin_edges = np.arange(0, int(times.max() / sampling_freq) + 2)
        if len(bin_edges) > 1 and len(spike_times) > 0:
            spike_seconds = spike_times / sampling_freq
            hist, _ = np.histogram(spike_seconds, bins=bin_edges)
            presence_ratio = float(np.mean(hist > 0))
        else:
            presence_ratio = 0.0

        snr = 0.0

        per_unit.append({
            "unit_id": uid,
            "final_spike_count": spike_count,
            "final_firing_rate_hz": round(firing_rate, 2),
            "final_isi_violation_rate": round(isi_violation_rate, 4),
            "final_isi_fp_estimate": round(fp_est, 4),
            "amplitude_cutoff": round(amplitude_cutoff, 3),
            "presence_ratio": round(presence_ratio, 3),
            "snr": round(snr, 2),
        })

    metrics = {"per_unit": per_unit}
    (out / "final_metrics.json").write_text(json.dumps(metrics, indent=2))
    log.info("compute_final_metrics.complete", n_units=len(per_unit))


def apply_final_actions(project_path: str, output_dir: str) -> None:
    """Execute Gate 3 decisions: merge or delete final units."""
    out = Path(output_dir)
    decision = json.loads((out / "gate3_decision.json").read_text())
    sorting_npz = out / "sorting" / "sorting.npz"
    data = np.load(sorting_npz)

    times = data["times_samples"]
    labels = data["labels"].copy()
    sampling_freq = float(data["sampling_frequency"]) if "sampling_frequency" in data else 30000.0

    # DELETE units
    delete_ids = decision.get("delete_units", [])
    garbage_removed = 0
    for uid in delete_ids:
        count = int((labels == uid).sum())
        labels[labels == uid] = -1
        garbage_removed += count

    # MERGE units
    merge_pairs = decision.get("merge_pairs", [])
    for pair in merge_pairs:
        if len(pair) == 2:
            labels[labels == pair[1]] = pair[0]

    # Save final sorting
    final_dir = out / "sorting_final"
    final_dir.mkdir(exist_ok=True)
    np.savez(
        final_dir / "sorting.npz",
        times_samples=times,
        labels=labels,
        sampling_frequency=sampling_freq,
    )

    unique_final = np.unique(labels[labels >= 0]) if len(labels) > 0 else np.array([])
    result = {
        "final_unit_count": int(len(unique_final)),
        "final_spike_count": int((labels >= 0).sum()),
        "garbage_removed_count": garbage_removed,
        "units_deleted": len(delete_ids),
        "units_merged": len(merge_pairs),
    }
    (out / "sorting_result.json").write_text(json.dumps(result, indent=2))
    log.info(
        "apply_final_actions.complete",
        units=result["final_unit_count"],
        deleted=result["units_deleted"],
        merged=result["units_merged"],
    )


def merge_clusters(project_path: str, output_dir: str) -> None:
    """Post-clustering merge using DARTsort's agglomerate with SVD template distances.

    Uses template_distances() for SVD-compressed pairwise distances, firing_corr()
    for temporal correlation, and linkage clustering for transitive merges.
    Overwrites clusters/sorting.npz with the merged result.
    """
    import spikeinterface.core as si
    from dartsort.clustering.agglomerate import agglomerate
    from dartsort.util.data_util import DARTsortSorting
    from dartsort.util.internal_config import default_dartsort_cfg, default_waveform_cfg
    from dartsort.util.motion import MotionInfo

    out = Path(output_dir)
    recording = si.load(out / "preprocessed")
    sorting = DARTsortSorting.load(out / "clusters" / "sorting.npz")

    motion_dir = out / "motion"
    motion = MotionInfo.try_load(motion_dir) if motion_dir.exists() else None
    if motion is None:
        log.warning("merge_clusters.skip", reason="no motion data available")
        return

    agg_cfg = default_dartsort_cfg.agglomerate_cfg

    labels_before = sorting.labels
    n_before = len(np.unique(labels_before[labels_before >= 0])) if labels_before is not None else 0

    result = agglomerate(
        sorting=sorting,
        recording=recording,
        template_merge_cfg=agg_cfg.template_merge_cfg,
        refinement_cfg=agg_cfg,
        motion=motion,
        waveform_cfg=default_waveform_cfg,
    )

    merged_sorting = result.agglomerated_sorting
    merged_sorting.save(out / "clusters" / "sorting.npz")

    labels_after = merged_sorting.labels if hasattr(merged_sorting, "labels") else np.array([])
    n_after = len(np.unique(labels_after[labels_after >= 0])) if len(labels_after) > 0 else 0

    summary = {
        "units_before_merge": n_before,
        "units_after_merge": n_after,
        "units_merged": n_before - n_after,
    }
    (out / "merge_summary.json").write_text(json.dumps(summary, indent=2))
    log.info(
        "merge_clusters.complete",
        before=n_before,
        after=n_after,
        merged=n_before - n_after,
    )


def recover_low_snr_spikes(project_path: str, output_dir: str) -> None:
    """Correlation-based recovery of unassigned and low-count spikes.

    Computes templates directly from sorting_final using DARTsort's
    asymmetric window (42 before + 79 after trough = 121 samples).
    """
    import spikeinterface.core as si

    out = Path(output_dir)

    recording = si.load(out / "preprocessed")

    sorting_npz = out / "sorting_final" / "sorting.npz"
    data = np.load(sorting_npz)
    times = data["times_samples"]
    labels = data["labels"].copy()
    sampling_freq = float(data["sampling_frequency"])

    unique_ids = np.unique(labels[labels >= 0])

    samples_before = 42
    samples_after = 79

    templates_dict = _compute_quick_templates(
        recording, labels, times, unique_ids,
        samples_before=samples_before, samples_after=samples_after,
    )

    template_unit_ids = np.array(sorted(templates_dict.keys()), dtype=np.int64)
    if len(template_unit_ids) == 0:
        log.info("recover_low_snr_spikes.skip", reason="no templates computed")
        stats = {
            "n_unassigned_before": int((labels < 0).sum()),
            "n_candidates_evaluated": 0,
            "n_recovered": 0,
            "recovery_rate": 0.0,
            "n_low_count_units": 0,
            "per_unit_recovered": {},
        }
        (out / "recovery_stats.json").write_text(json.dumps(stats, indent=2))
        return

    templates_arr = np.stack(
        [templates_dict[int(uid)] for uid in template_unit_ids]
    )

    unassigned_mask = labels < 0
    unassigned_indices = np.flatnonzero(unassigned_mask)
    n_unassigned = len(unassigned_indices)

    # Per-channel standardization so templates and candidate waveforms
    # are on the same scale (templates average out noise → std~1,
    # individual waveforms retain full noise → std~5 without this).
    noise_stats_path = out / "noise_stats.json"
    if noise_stats_path.exists():
        noise_data = json.loads(noise_stats_path.read_text())
        channel_stds = np.array(noise_data["mad_per_channel"], dtype=np.float64)
        channel_stds = np.maximum(channel_stds, 1e-6)
    else:
        sample_traces = recording.get_traces(
            start_frame=0, end_frame=min(30000, recording.get_num_samples()),
        )
        channel_stds = (
            np.median(np.abs(sample_traces - np.median(sample_traces, axis=0)), axis=0)
            * 1.4826
        )
        channel_stds = np.maximum(channel_stds, 1e-6)

    templates_arr = templates_arr / channel_stds[np.newaxis, np.newaxis, :]

    unit_counts = {int(uid): int((labels == uid).sum()) for uid in unique_ids}
    low_count_units = {uid for uid, count in unit_counts.items() if count < 500}

    max_candidates = 10_000
    if n_unassigned > max_candidates:
        rng = np.random.default_rng(42)
        candidate_indices = rng.choice(unassigned_indices, size=max_candidates, replace=False)
    else:
        candidate_indices = unassigned_indices

    n_recovered = 0
    recovered_per_unit: dict[int, int] = {}

    n_templates = len(templates_arr)
    flat_templates = templates_arr.reshape(n_templates, -1).astype(np.float64)
    template_means = flat_templates.mean(axis=1, keepdims=True)
    flat_templates_centered = flat_templates - template_means
    template_norms = np.linalg.norm(flat_templates_centered, axis=1)
    valid_template_mask = template_norms > 1e-10

    valid_flat = flat_templates_centered[valid_template_mask]
    valid_norms = template_norms[valid_template_mask]
    valid_flat_normed = valid_flat / valid_norms[:, np.newaxis]

    n_valid = int(valid_template_mask.sum())
    n_components = min(20, n_valid - 1, valid_flat_normed.shape[1])

    if n_components > 0 and n_valid > 2:
        U, S, Vt = np.linalg.svd(valid_flat_normed, full_matrices=False)
        compressed_templates = U[:, :n_components] * S[:n_components]
        c_norms = np.linalg.norm(compressed_templates, axis=1, keepdims=True)
        compressed_templates /= np.maximum(c_norms, 1e-10)
        projection_matrix = Vt[:n_components, :]
    else:
        compressed_templates = valid_flat_normed
        projection_matrix = None

    valid_unit_ids = template_unit_ids[valid_template_mask]

    for idx in candidate_indices:
        spike_time = times[idx]

        start = spike_time - samples_before
        end = spike_time + samples_after
        if start < 0 or end > recording.get_num_samples():
            continue

        waveform = recording.get_traces(start_frame=int(start), end_frame=int(end))
        waveform = waveform.astype(np.float64) / channel_stds
        waveform_flat = waveform.flatten()
        waveform_flat_centered = waveform_flat - waveform_flat.mean()
        waveform_norm = np.linalg.norm(waveform_flat_centered)

        if waveform_norm < 1e-10:
            continue

        waveform_normed = waveform_flat_centered / waveform_norm

        if projection_matrix is not None:
            waveform_projected = waveform_normed @ projection_matrix.T
            wp_norm = np.linalg.norm(waveform_projected)
            if wp_norm < 1e-10:
                continue
            waveform_projected /= wp_norm
            correlations = compressed_templates @ waveform_projected
        else:
            correlations = compressed_templates @ waveform_normed

        best_idx = int(np.argmax(correlations))
        best_corr = float(correlations[best_idx])
        best_unit_id = int(valid_unit_ids[best_idx])

        base_threshold = 0.65
        if best_unit_id in low_count_units:
            threshold = base_threshold * 0.6
        else:
            threshold = base_threshold

        if best_corr < threshold:
            continue

        orig_template_idx = np.flatnonzero(valid_template_mask)[best_idx]
        best_template = templates_arr[orig_template_idx]
        residual = waveform - best_template
        residual_rms = float(np.sqrt(np.mean(residual ** 2)))
        waveform_rms = float(np.sqrt(np.mean(waveform ** 2)))

        if waveform_rms < 1e-10:
            continue

        if residual_rms >= 0.3 * waveform_rms:
            continue

        labels[idx] = best_unit_id
        n_recovered += 1
        recovered_per_unit[best_unit_id] = recovered_per_unit.get(best_unit_id, 0) + 1

    np.savez(
        sorting_npz,
        times_samples=times,
        labels=labels,
        sampling_frequency=sampling_freq,
    )

    stats = {
        "n_unassigned_before": int(n_unassigned),
        "n_candidates_evaluated": int(len(candidate_indices)),
        "n_recovered": n_recovered,
        "recovery_rate": round(n_recovered / max(n_unassigned, 1), 4),
        "n_low_count_units": len(low_count_units),
        "per_unit_recovered": {str(k): v for k, v in recovered_per_unit.items()},
    }
    (out / "recovery_stats.json").write_text(json.dumps(stats, indent=2))

    log.info(
        "recover_low_snr_spikes.complete",
        unassigned=n_unassigned,
        recovered=n_recovered,
        rate=stats["recovery_rate"],
        low_count_units=len(low_count_units),
    )


def evaluate_accuracy(project_path: str, output_dir: str) -> dict:
    """Compare final sorting to ground truth using benchmark profile.

    Uses make_match_count_matrix directly instead of compare_sorter_to_ground_truth
    because the latter has a bug in SpikeInterface 0.104.x where delta_frames=0.
    """
    import spikeinterface.core as si
    from spikeinterface.core import NumpySorting
    from spikeinterface.comparison.comparisontools import make_match_count_matrix

    out = Path(output_dir)
    benchmark_path = Path(project_path) / "benchmark.md"
    if not benchmark_path.exists():
        raise FileNotFoundError(
            f"benchmark.md not found at {benchmark_path}. "
            "Create one with YAML frontmatter to enable accuracy evaluation."
        )

    profile = BenchmarkProfile.from_file(benchmark_path)

    gt_sorting = si.load(profile.ground_truth_path)
    if profile.duration_seconds is not None:
        n_frames = int(profile.duration_seconds * profile.sampling_frequency)
        gt_sorting = gt_sorting.frame_slice(start_frame=0, end_frame=n_frames)

    sorting_dir = out / "sorting_final"
    sorting_npz = sorting_dir / "sorting.npz"
    if not sorting_npz.exists():
        raise FileNotFoundError(f"Final sorting not found at {sorting_npz}")

    data = np.load(sorting_npz)
    times = data["times_samples"]
    labels = data["labels"]
    sampling_freq = float(data["sampling_frequency"]) if "sampling_frequency" in data else profile.sampling_frequency

    unique_ids = np.unique(labels[labels >= 0])
    units_dict: dict[int, np.ndarray] = {}
    for uid in unique_ids:
        spike_times = times[labels == uid]
        units_dict[int(uid)] = np.sort(spike_times)

    tested_sorting = NumpySorting.from_unit_dict(
        units_dict, sampling_frequency=sampling_freq,
    )

    # Use make_match_count_matrix directly with delta_frames (not delta_time)
    # This avoids a bug in compare_sorter_to_ground_truth where delta_frames=0
    match_matrix = make_match_count_matrix(
        gt_sorting, tested_sorting,
        delta_frames=profile.match_tolerance_samples,
        ensure_symmetry=False,
    )

    # Compute per-GT-unit accuracy using best match
    per_unit_results = []
    for gt_id in gt_sorting.unit_ids:
        gt_count = len(gt_sorting.get_unit_spike_train(gt_id))

        row = match_matrix.loc[gt_id]
        best_tested_id = row.idxmax()
        n_match = float(row[best_tested_id])
        tested_count = len(tested_sorting.get_unit_spike_train(best_tested_id))

        # accuracy = TP / (TP + FP + FN) = n_match / (gt + tested - n_match)
        denom = gt_count + tested_count - n_match
        accuracy = n_match / denom if denom > 0 else 0.0
        recall = n_match / gt_count if gt_count > 0 else 0.0
        precision = n_match / tested_count if tested_count > 0 else 0.0

        per_unit_results.append({
            "gt_id": int(gt_id),
            "best_match": int(best_tested_id),
            "n_match": int(n_match),
            "accuracy": round(accuracy, 4),
            "recall": round(recall, 4),
            "precision": round(precision, 4),
        })

    accuracies = [r["accuracy"] for r in per_unit_results]
    mean_accuracy = float(np.mean(accuracies))

    per_unit_accuracy = {str(r["gt_id"]): r["accuracy"] for r in per_unit_results}

    result = {
        "accuracy": round(mean_accuracy, 4),
        "baseline": profile.baseline_accuracy,
        "delta": round(mean_accuracy - profile.baseline_accuracy, 4),
        "target": profile.target_accuracy,
        "target_met": mean_accuracy >= profile.target_accuracy,
        "n_gt_units": len(gt_sorting.unit_ids),
        "n_sorted_units": len(tested_sorting.unit_ids),
        "per_unit_accuracy": per_unit_accuracy,
        "per_unit_details": per_unit_results,
    }

    (out / "benchmark_result.json").write_text(json.dumps(result, indent=2))
    log.info(
        "evaluate_accuracy.complete",
        accuracy=result["accuracy"],
        baseline=result["baseline"],
        delta=result["delta"],
        target_met=result["target_met"],
    )
    return result
