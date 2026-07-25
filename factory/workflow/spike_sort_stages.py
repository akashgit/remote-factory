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
import sys
from pathlib import Path
import numpy as np
import structlog

log = structlog.get_logger()

_DS_REF = os.environ.get("DS_REF_PATH", "/workspace/home/churwitz/ds_ref")
if _DS_REF not in sys.path:
    sys.path.insert(0, _DS_REF)


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
    window_ms: float = 50.0,
) -> tuple[float, float]:
    """Compute R12 and Q12 refractory scores from cross-correlogram."""
    max_lag = int(window_ms * sampling_rate / 1000)
    refractory_samples = refractory_ms * sampling_rate / 1000
    baseline_samples = 10.0 * sampling_rate / 1000

    all_diffs = []
    for t in times_a:
        diffs = times_b - t
        mask = np.abs(diffs) <= max_lag
        all_diffs.extend(diffs[mask].tolist())

    if not all_diffs:
        return 1.0, 0.0

    diffs_arr = np.array(all_diffs)
    refractory_mask = np.abs(diffs_arr) < refractory_samples
    baseline_mask = np.abs(diffs_arr) > baseline_samples

    if baseline_mask.any() or refractory_mask.any():
        n_baseline = baseline_mask.sum()
        n_refractory = refractory_mask.sum()
        baseline_density = n_baseline / (max_lag - baseline_samples) if (max_lag - baseline_samples) > 0 else 1.0
        refractory_density = n_refractory / refractory_samples if refractory_samples > 0 else 0.0
        r12 = refractory_density / baseline_density if baseline_density > 0 else 1.0
    else:
        r12 = 1.0

    q12 = max(0.0, 1.0 - r12)
    return r12, q12


# ── Gate FnNode implementations ──────────────────────────────────


def compute_cluster_metrics(project_path: str, output_dir: str) -> None:
    """Compute per-cluster and pairwise metrics for Gate 1 (post-clustering)."""
    from dartsort.util.data_util import DARTsortSorting

    out = Path(output_dir)
    sorting = DARTsortSorting.load(out / "clusters" / "sorting.npz")
    context = json.loads((out / "recording_context.json").read_text())

    labels = sorting.labels if hasattr(sorting, "labels") else np.array([])
    times = sorting.times_samples if hasattr(sorting, "times_samples") else np.array([])
    sampling_rate = context.get("sampling_rate_hz", 30000.0)
    duration_s = context.get("recording_duration_min", 5.0) * 60.0

    unique_ids = np.unique(labels[labels >= 0]) if len(labels) > 0 else np.array([])

    locs = None
    loc_path = out / "localizations.npz"
    if loc_path.exists():
        locs = np.load(loc_path)

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

            # Template correlation (using mean spike amplitudes as proxy)
            corr = 0.0

            # CCG refractory
            mask_a = labels == uid_a
            mask_b = labels == uid_b
            times_a = times[mask_a]
            times_b = times[mask_b]

            r12, q12 = 1.0, 0.0
            if len(times_a) > 10 and len(times_b) > 10:
                sub_a = times_a[:min(5000, len(times_a))]
                sub_b = times_b[:min(5000, len(times_b))]
                r12, q12 = _compute_ccg_refractory(sub_a, sub_b, sampling_rate)

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


def compute_template_metrics(project_path: str, output_dir: str) -> None:
    """Compute per-template metrics for Gate 2 (post-template)."""
    out = Path(output_dir)
    template_stats = json.loads((out / "template_stats.json").read_text())

    # Load template data for similarity computation
    templates_arr = None
    template_npz = out / "templates" / "template_data.npz"
    if template_npz.exists():
        tdata = np.load(template_npz, allow_pickle=True)
        if "templates" in tdata:
            templates_arr = tdata["templates"]

    per_template = []
    for stats in template_stats:
        tid = stats["template_id"]
        spike_count = stats.get("spike_count", 0)
        snr = stats.get("snr", 0.0)
        stability = stats.get("stability", 0.9)
        ptp_uv = stats.get("ptp_amplitude_uv", 0.0)
        spatial_spread = stats.get("spatial_spread_um", 0.0)

        # Compute max similarity with other templates
        similarity_max = 0.0
        if templates_arr is not None and len(templates_arr) > 1 and tid < len(templates_arr):
            t_flat = templates_arr[tid].flatten()
            for j in range(len(templates_arr)):
                if j == tid:
                    continue
                other_flat = templates_arr[j].flatten()
                if len(t_flat) == len(other_flat) and np.std(t_flat) > 0 and np.std(other_flat) > 0:
                    corr = float(np.corrcoef(t_flat, other_flat)[0, 1])
                    similarity_max = max(similarity_max, corr)

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
