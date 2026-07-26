---
name: workflow-spike-sort
description: "Spike sorting pipeline with LLM quality gates and low-SNR recovery — executes DARTsort algorithms with three quality gates (post-clustering, post-template, post-matching) that compute metrics deterministically and use LLM agents to interpret them in recording context. Includes correlation-based recovery of unassigned/low-count spikes with residual verification, and benchmark accuracy evaluation against ground truth. Use when invoked with --mode spike-sort."
disable-model-invocation: true
argument-hint: "<project_path>"
---

# Spike Sort Workflow

The user wants: **$ARGUMENTS**

## Step: Preprocess

Bandpass filter, standardize, whiten the raw recording. Writes preprocessed recording to {output_dir}/preprocessed/ and noise statistics to {output_dir}/noise_stats.json.

```bash

```

## Step: Detect

Subtraction-based spike detection (SubtractionPeeler) with DARTsort defaults: detection_threshold=3.0, peak_sign='both'. No longer reads detection_params.json — parameters are hardcoded.

```bash

```

## Step: Localize

Point-source localization of detected spikes via Levenberg-Marquardt. Also estimates motion (drift).

```bash

```

## Step: Cluster

DPC clustering with hardcoded defaults: strategy='dpc', grid_dx=15.0, grid_dz=15.0. No longer reads clustering_params.json. Creates recording_context.json from config + computed drift.

```bash

```

## Step: Compute Cluster Metrics

Compute per-cluster and pairwise metrics for Gate 1. Per-cluster: spike_count, firing_rate_hz, isi_violation_rate, isi_fp_estimate (Hill formula), snr, amplitude_bimodality (Hartigan dip), spatial_centroid. Pairwise (clusters within 100um): template_correlation, spatial_distance_um, ccg_refractory_r12, ccg_refractory_q12.

```bash

```

## Phase 1: Health Checker — Gate Post Cluster

```bash
factory agent health_checker --task "You are a spike sorting quality gate for the POST-CLUSTERING stage. Your role is to make context-aware decisions about cluster merging, splitting, and deletion — decisions that require judgment, not just threshold checking.

## Why You Exist

If we could just apply fixed thresholds, we'd use deterministic code. You exist because the same metric means different things in different contexts. A 5% ISI violation rate might be acceptable contamination in a fast-spiking interneuron or catastrophic in a sparse pyramidal cell. Your job is to reason about what the metrics mean for THIS recording.

## Step 1: Understand the Recording Context

Read {output_dir}/recording_context.json FIRST. Before looking at any metrics, understand:
- **Brain region:** What cell types are expected? What firing rates?
- **Drift magnitude:** High drift (>15µm) causes fragmentation — be more aggressive about merging
- **Recording duration:** Affects what spike counts are reasonable
- **Expected cell types:** Purkinje cells fire at 50-150Hz; pyramidal cells at 0.5-10Hz; fast interneurons at 20-100Hz

## Step 2: Review Cluster Metrics

Read {output_dir}/cluster_metrics.json. For each cluster and pair, interpret the metrics IN CONTEXT:

**Per-cluster metrics:**
- spike_count, firing_rate_hz — Is this plausible for the expected cell types?
- isi_violation_rate — Raw fraction of ISIs < 1.5ms
- isi_fp_estimate — Hill formula estimate accounting for firing rate (USE THIS, not raw ISI rate)
- snr — Signal quality
- amplitude_bimodality — Suggests two populations merged if high
- spatial_centroid — Position on probe

**Pairwise metrics (clusters within 100µm):**
- template_correlation — Waveform similarity
- spatial_distance_um — Physical proximity
- ccg_refractory_r12, ccg_refractory_q12 — CCG refractory dip scores. Low R12 (<0.25) with low Q12 (<0.05) suggests same neuron.

## Step 3: Make Decisions

For each decision, reason about the specific context:

### MERGE Decisions
The clustering algorithm deliberately oversplits — your primary job is to identify and merge fragments of the same neuron.

Strong merge signal: CCG shows refractory dip (independent confirmation that spikes come from the same neuron). If CCG is refractory AND clusters are nearby AND templates are similar, merge.

Weak merge signal without CCG: Template correlation alone is not enough — similar waveforms can come from different neurons. Require additional evidence (proximity, firing pattern continuity).

Drift consideration: If drift is high, waveforms from the same neuron may differ across time. CCG refractory is more reliable than template correlation in drifting recordings.

### DELETE Decisions
Delete clusters that are clearly not single neurons. But interpret in context:

- High ISI + high rate: Likely garbage collector (noise unit). But check brain region — cerebellar Purkinje cells legitimately fire at 100+ Hz.
- High ISI + low rate: Use isi_fp_estimate, not raw ISI. A 1% ISI rate at 3Hz firing indicates ~50% contamination (delete), while 1% at 50Hz is ~3% contamination (keep).
- Very low spike count: May be a real but sparse neuron, or may be noise. Check SNR — low count + low SNR = noise.

### SPLIT Decisions
Split when evidence suggests two neurons were merged:
- Bimodal amplitude distribution (two distinct sizes)
- CCG shows NO refractory dip (neurons fire independently)
- Spatial spread larger than expected for one neuron

## Output

Write {output_dir}/gate1_decision.json:
```json
{
  "merge_pairs": [[id1, id2], ...],
  "split_clusters": [id, ...],
  "delete_clusters": [id, ...],
  "confidence": 0.0-1.0,
  "reasoning": "..."
}
```

In your reasoning, explain HOW CONTEXT influenced your decisions. Don't just cite threshold values — explain what the metrics mean for this specific recording and why your decisions make sense.
Read: cluster_metrics.json, recording_context.json
Write output to: gate1_decision.json" --project "$PROJECT_PATH" --timeout 300
```

```bash
# Artifact verification: gate_post_cluster
_vfail=0
_f="$PROJECT_PATH/gate1_decision.json"
[ ! -f "$_f" ] && echo "VERIFY FAIL: gate_post_cluster: gate1_decision.json missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: gate_post_cluster: gate1_decision.json is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=gate_post_cluster" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: gate_post_cluster artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=gate_post_cluster" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Step: Apply Cluster Actions

Execute Gate 1 decisions. MERGE: agglomerate refinement (merge_distance_threshold=0.3). SPLIT: TMM refinement (mixture_steps=['split']). DELETE: labels[np.isin(labels, delete_clusters)] = -1.

```bash

```

## Step: Templates

Compute unit templates (average/median waveforms) from refined clusters. Reads clusters_refined/ if present, else clusters/.

```bash

```

## Step: Compute Template Metrics

Compute per-template metrics for Gate 2. Metrics: template_spike_count, template_snr, template_stability, template_similarity_max, template_amplitude_uv, template_spatial_spread_um.

```bash

```

## Phase 2: Health Checker — Gate Post Template

```bash
factory agent health_checker --task "You are a spike sorting quality gate for the POST-TEMPLATE stage.

## Context First

Read {output_dir}/recording_context.json. Key factors:
- **Drift:** High drift makes templates less stable — instability may be drift, not noise
- **Recording duration:** Affects expected spike counts per template

## Template Metrics

Read {output_dir}/template_metrics.json:
- template_spike_count — More spikes = more reliable template
- template_snr — Signal quality
- template_stability — Variance over time (lower = more stable)
- template_similarity_max — Correlation with most similar other template
- template_amplitude_uv, template_spatial_spread_um

## Decision Guidance

**DELETE:** Templates that will cause matching problems:
- Very few spikes AND low SNR — noise template, will match noise
- But consider: a template with few spikes but high SNR might be a real sparse neuron, not noise

**MERGE:** Near-duplicate templates:
- Very high similarity (>0.95) suggests same neuron split into two templates
- But check spatial locations — similar waveforms from distant sites are different neurons

**Context matters:**
- If many templates look bad, that's an upstream clustering problem, not a template problem — note this
- Low stability in high-drift recordings is expected, not a delete signal

## Output

Write {output_dir}/gate2_decision.json:
```json
{
  "merge_pairs": [[id1, id2], ...],
  "delete_templates": [id, ...],
  "confidence": 0.0-1.0,
  "reasoning": "..."
}
```

This is a lighter gate. If templates look reasonable for the recording context, output empty action lists.
Read: recording_context.json, template_metrics.json
Write output to: gate2_decision.json" --project "$PROJECT_PATH" --timeout 120
```

```bash
# Artifact verification: gate_post_template
_vfail=0
_f="$PROJECT_PATH/gate2_decision.json"
[ ! -f "$_f" ] && echo "VERIFY FAIL: gate_post_template: gate2_decision.json missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: gate_post_template: gate2_decision.json is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=gate_post_template" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: gate_post_template artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=gate_post_template" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Step: Apply Template Actions

Execute Gate 2 decisions. DELETE: remove templates from set. MERGE: average similar templates, update mapping.

```bash

```

## Step: Match

Template matching refinement with post-match agglomeration. Reads templates_refined/ if present, else templates/. Produces the final sorting result.

```bash

```

## Step: Compute Final Metrics

Compute final unit metrics for Gate 3. Metrics: final_spike_count, final_firing_rate_hz, final_isi_violation_rate, final_isi_fp_estimate, amplitude_cutoff, presence_ratio, snr.

```bash

```

## Phase 3: Health Checker — Gate Post Match

```bash
factory agent health_checker --task "You are a spike sorting quality gate for the POST-MATCHING stage. This is final cleanup — you cannot recover lost accuracy, only remove clear garbage.

## Context First

Read {output_dir}/recording_context.json. Critical:
- **Expected cell types:** What firing rates are biologically plausible?
- **Brain region:** Cerebellum allows 100+ Hz; cortex typically <50 Hz

## Final Metrics

Read {output_dir}/final_metrics.json:
- final_spike_count, final_firing_rate_hz
- final_isi_violation_rate (raw) vs final_isi_fp_estimate (rate-adjusted)
- amplitude_cutoff, presence_ratio, snr

## Decision Guidance

**DELETE with confidence:**
- Obvious garbage collectors: implausibly high rate + high ISI + low SNR
- But ALWAYS check brain region first — 150 Hz is garbage in cortex, normal in cerebellum

**DELETE with caution:**
- Low spike count — might be sparse neuron, check SNR and presence_ratio
- High ISI at low rate — use isi_fp_estimate to assess true contamination

**MERGE conservatively:**
- At this stage, prefer keeping units separate for human review
- Only merge with overwhelming evidence (high correlation + refractory CCG + proximity)
- Merging two different neurons is worse than keeping one neuron split

## Output

Write {output_dir}/gate3_decision.json:
```json
{
  "merge_pairs": [[id1, id2], ...],
  "delete_units": [id, ...],
  "confidence": 0.0-1.0,
  "reasoning": "..."
}
```

Explain your reasoning in context. Don't just cite numbers — explain why this unit is/isn't plausible for the recording context.
Read: final_metrics.json, recording_context.json
Write output to: gate3_decision.json" --project "$PROJECT_PATH" --timeout 120
```

```bash
# Artifact verification: gate_post_match
_vfail=0
_f="$PROJECT_PATH/gate3_decision.json"
[ ! -f "$_f" ] && echo "VERIFY FAIL: gate_post_match: gate3_decision.json missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: gate_post_match: gate3_decision.json is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=gate_post_match" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: gate_post_match artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=gate_post_match" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Step: Apply Final Actions

Execute Gate 3 decisions. DELETE: labels[labels == unit_id] = -1. MERGE: labels[labels == unit_b] = unit_a. Writes final cleaned sorting.

```bash

```

## Step: Recover Low Snr Spikes

Correlation-based recovery of unassigned and low-count spikes. Identifies spikes with label=-1 and units with <500 spikes. For each unassigned spike: correlates with templates, applies lower threshold (0.6x normal) for low-count units, verifies via residual energy (RMS(residual) < 0.3 * RMS(waveform)). Updates sorting_final/ in-place with recovered assignments.

```bash

```

## Step: Evaluate Accuracy

Compare final sorting against ground truth benchmark. Reads benchmark.md from project root for ground truth path and accuracy targets. Computes per-unit accuracy, recall, and precision. Writes benchmark_result.json.

```bash

```
