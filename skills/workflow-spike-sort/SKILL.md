---
name: workflow-spike-sort
description: "Spike sorting data pipeline with LLM-in-the-loop parameter selection. NOT a code-writing workflow — directly executes DARTsort algorithms (FnNodes) with LLM parameter advisors (AgentNodes) at three decision points: detection threshold, clustering strategy, and template QC. Input: SpikeInterface BaseRecording. Output: BaseSorting. Use with --mode spike-sort on a configured recording."
disable-model-invocation: true
argument-hint: "<project_path> --mode spike-sort"
---

# Spike Sort Workflow

The user wants: **$ARGUMENTS**

## Step: Preprocess

Bandpass filter, standardize, whiten the raw recording. Writes preprocessed recording to {output_dir}/preprocessed/ and noise statistics to {output_dir}/noise_stats.json.

```bash

```

## Step: Detect Trial

Fast threshold sweep on a small subset of the recording. Runs dartsort.threshold() at 3 candidate thresholds (3.5, 4.0, 4.5) with early termination (stop_after_n_spikes=10000, ensure_coverage=0.05). Writes trial results to {output_dir}/trial_results.json for the detection parameter advisor to make a data-informed threshold choice.

```bash

```

## Phase 1: Researcher — Detect Params

```bash
factory agent researcher --task "You are a spike detection parameter advisor for extracellular neural recordings. Read the noise statistics at {output_dir}/noise_stats.json and the trial detection results at {output_dir}/trial_results.json.

TRIAL RESULTS: A threshold sweep has already been run on a small subset of the recording at thresholds 3.5, 4.0, and 4.5. The trial_results.json file contains for each threshold: spike_count, spike_rate_hz, mean_amplitude, and amplitude_distribution_percentiles. Use this empirical data to inform your threshold selection.

IMPORTANT: DARTsort's detection operates on standardized (SNR-unit) traces. The threshold 4.0 is the DARTsort-calibrated baseline, validated on Neuropixels recordings with default preprocessing. A single-channel denoiser NN runs before detection and recovers low-amplitude spikes, so you do NOT need an aggressive (low) threshold to catch weak units. Prefer the default. Only deviate with clear evidence from the trial results AND noise statistics.

Select detection parameters:
- voltage_threshold (3.0-6.0): SNR threshold in standardized units. Default: 4.0. Do NOT lower below 4.0 unless you have strong evidence that the recording has exceptionally high SNR AND the denoiser is disabled. Raising above 4.0 is safer than lowering — it reduces false positives with minimal loss of real spikes.
- peak_sign ('neg', 'pos', 'both'): Which polarity peaks to detect. 'both' is standard for extracellular recordings.
- dedup_temporal_radius (5-20): Samples to deduplicate within. 11 is typical at 30kHz.
- use_denoiser (true/false): Whether to apply the single-channel denoiser NN before detection. Default: true. The denoiser cleans waveforms and improves detection of low-amplitude spikes. Disable only if the recording has unusual artifacts that the denoiser was not trained on.

Decision rules:
- Start with voltage_threshold=4.0 (the calibrated default).
- Compare trial results across thresholds: if 4.0 produces a reasonable spike rate (20-200 Hz per channel) keep it. If the rate is extremely high (>500 Hz), consider raising to 4.5 or 5.0.
- Very noisy data (median noise >20 µV after standardization): raise to 5.0-6.0.
- Clean data (<8 µV): keep 4.0 — the denoiser handles low-amplitude recovery.
- DO NOT lower the threshold to compensate for expected low-amplitude neurons. The denoiser NN is designed for this.
- Neuropixels probes → 'both' peak_sign.

In your reasoning field, explicitly state why you chose your threshold. Reference the trial results — cite the spike counts and rates you observed. If you chose anything other than 4.0, justify the deviation with specific evidence from the trial data and noise_stats.json.

Output your selection as a DetectionParams JSON object with a reasoning field.
Read: noise_stats.json, trial_results.json
Write output to: detection_params.json" --project "$PROJECT_PATH" --timeout 60
```

```bash
# Artifact verification: detect_params
_vfail=0
_f="$PROJECT_PATH/detection_params.json"
[ ! -f "$_f" ] && echo "VERIFY FAIL: detect_params: detection_params.json missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: detect_params: detection_params.json is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=detect_params" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: detect_params artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=detect_params" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Step: Detect

Threshold-crossing detection with LLM-selected parameters. Reads preprocessed recording and detection_params.json. Writes detections to {output_dir}/detections/ and summary to {output_dir}/detection_summary.json.

```bash

```

## Step: Localize

Point-source localization of detected spikes via Levenberg-Marquardt. Also estimates motion (drift). Writes localizations and cluster_input_stats.json for the clustering agent.

```bash

```

## Phase 2: Strategist — Cluster Params

```bash
factory agent strategist --task "You are a spike clustering strategy advisor. Read the cluster input statistics at {output_dir}/cluster_input_stats.json.

Select a clustering strategy and parameters:
- strategy: 'channel_snap' (fast, coarse — good for <20K spikes), 'grid_snap' (spatial grid — good for high density), 'dpc' (density peak clustering — slow but accurate, best for >20K spikes with drift), 'none' (skip clustering — only for pre-sorted data).
- grid_dx, grid_dz (5-50 µm): Spatial grid resolution. Smaller = more clusters initially. 15 µm is typical.
- initial_steps: Refinement sequence. ['split', 'demolish', 'demolish'] is standard. Add 'merge' if you expect many similar units.
- n_waveforms_fit (10K-100K): Subsample size for fitting. Higher = better but slower. 40K is typical.

Decision tree:
- spike_count < 20K → channel_snap
- drift > 15 µm → dpc with drift correction
- spike_density > 1.0 spikes/ch/s → grid_snap
- Otherwise → dpc

Output your selection as a ClusteringParams JSON object with a reasoning field.
Read: cluster_input_stats.json
Write output to: clustering_params.json" --project "$PROJECT_PATH" --timeout 120
```

```bash
# Artifact verification: cluster_params
_vfail=0
_f="$PROJECT_PATH/clustering_params.json"
[ ! -f "$_f" ] && echo "VERIFY FAIL: cluster_params: clustering_params.json missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: cluster_params: clustering_params.json is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=cluster_params" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: cluster_params artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=cluster_params" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Step: Cluster

GMM/DPC clustering with LLM-selected strategy and parameters. Reads detections and clustering_params.json. Writes clustered sorting to {output_dir}/clusters/.

```bash

```

## Step: Templates

Compute unit templates (average/median waveforms) from clustered spikes. Writes per-template quality statistics for the QC agent.

```bash

```

## Phase 3: Researcher — Qc Templates

```bash
factory agent researcher --task "You are a spike sorting template quality control reviewer. Read the template statistics at {output_dir}/template_stats.json.

For each template, decide: keep, discard, or merge.

Decision criteria:
- DISCARD if: SNR < 1.5 (noise unit), spike_count < 10 (too few spikes), or ptp_amplitude < 5 µV (below noise floor).
- MERGE if: two templates have very similar spatial positions (within 25 µm) AND similar PTP amplitudes (within 30%). Specify merge_with = template_id of partner.
- KEEP otherwise.

Quality heuristics:
- Good units: SNR > 3, count > 50, stability > 0.7
- Marginal units: SNR 1.5-3, count 10-50 — keep if isolated
- High spatial_spread (>200 µm) may indicate multi-unit — flag for discard

Output a TemplateQCOutput JSON with decisions list and summary string.
Read: template_stats.json
Write output to: template_decisions.json" --project "$PROJECT_PATH" --timeout 120
```

```bash
# Artifact verification: qc_templates
_vfail=0
_f="$PROJECT_PATH/template_decisions.json"
[ ! -f "$_f" ] && echo "VERIFY FAIL: qc_templates: template_decisions.json missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: qc_templates: template_decisions.json is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=qc_templates" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: qc_templates artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=qc_templates" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Step: Match

Template matching refinement using QC-filtered templates. Reads template_decisions.json to filter templates before matching. Produces the final sorting result.

```bash

```
