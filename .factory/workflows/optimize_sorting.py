"""Optimize-sorting mode — three-tier iterative speed optimization for spike
sorting pipelines with hard accuracy constraints.

Tier 1: config sweep (parameter tuning, zero code changes)
Tier 2: code optimization (preserve algorithmic behavior)
Tier 3: algorithm changes (strict 0.5%/5% gates)
One experiment per CEO invocation. Terminal mode.
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)

meta = {
    "name": "optimize-sorting",
    "description": (
        "Optimize-sorting mode — three-tier iterative speed optimization "
        "for spike sorting pipelines with hard accuracy constraints. "
        "Tier 1: config sweep. Tier 2: code optimization. "
        "Tier 3: algorithm changes. Terminal mode."
    ),
}

# ── I/O capture helper (embedded in modified FnNode scripts) ─────

_IO_CAPTURE_FN = """\
def capture_io():
    try:
        with open('/proc/self/io', 'r') as f:
            stats = {}
            for line in f:
                key, value = line.strip().split(': ')
                stats[key] = int(value)
            return {
                'read_bytes': stats.get('read_bytes', 0),
                'write_bytes': stats.get('write_bytes', 0),
                'syscr': stats.get('syscr', 0),
                'syscw': stats.get('syscw', 0),
            }
    except (FileNotFoundError, PermissionError, ValueError):
        return None

"""

# ── FnNode script constants ──────────────────────────────────────

_LOCK_BASELINE_SCRIPT = _IO_CAPTURE_FN + """\
import json, subprocess, sys, statistics, os, datetime

p = sys.argv[1]
bl = os.path.join(p, '.factory', 'sorting', 'baseline.json')
os.makedirs(os.path.dirname(bl), exist_ok=True)

if os.path.exists(bl):
    print('Baseline already locked, skipping.')
    sys.exit(0)

cfg_path = os.path.join(p, '.factory', 'config.json')
cfg = json.load(open(cfg_path))
sb = cfg.get('sorting_benchmark', cfg.get('research_target', {}))
cmd = sb.get('command', sb.get('run_command', ''))
if not cmd:
    print('ERROR: No sorting_benchmark.command in config.json', file=sys.stderr)
    sys.exit(1)

N = 3
results = []
io_samples = []

for i in range(N):
    print(f'Baseline run {i+1}/{N}')

    io_before = capture_io()

    out_file = os.path.join(p, '.factory', 'sorting', f'baseline_run_{i}.json')
    run_cmd = cmd.replace('{recording}', sb.get('recording', '')).replace('{output}', out_file)
    r = subprocess.run(run_cmd, shell=True, capture_output=True, text=True, cwd=p)
    if r.returncode != 0:
        print(f'Benchmark failed: {r.stderr}', file=sys.stderr)
        sys.exit(1)

    io_after = capture_io()

    if os.path.exists(out_file):
        results.append(json.load(open(out_file)))
    else:
        results.append(json.loads(r.stdout))

    if io_before is not None and io_after is not None:
        io_delta = {k: io_after[k] - io_before[k] for k in io_before}
        io_samples.append(io_delta)

accs = [r['accuracy'] for r in results]
speeds = [r['speed_seconds'] for r in results]
acc_mean = statistics.mean(accs)
acc_std = statistics.stdev(accs) if len(accs) > 1 else 0.0
spd_mean = statistics.mean(speeds)
spd_std = statistics.stdev(speeds) if len(speeds) > 1 else 0.0

pu = {}
for r in results:
    for uid, val in r.get('per_unit_accuracy', {}).items():
        pu.setdefault(uid, []).append(val)
pu_stats = {uid: {'mean': statistics.mean(vals), 'std': statistics.stdev(vals) if len(vals) > 1 else 0.0}
             for uid, vals in pu.items()}

st = {}
for r in results:
    for stage, val in r.get('stage_timing', {}).items():
        st.setdefault(stage, []).append(val)
st_stats = {stage: {'mean': statistics.mean(vals), 'std': statistics.stdev(vals) if len(vals) > 1 else 0.0}
             for stage, vals in st.items()}

baseline = {
    'accuracy': {'mean': acc_mean, 'std': acc_std},
    'speed_seconds': {'mean': spd_mean, 'std': spd_std},
    'per_unit_accuracy': pu_stats,
    'stage_timing': st_stats,
    'n_runs': N,
    'locked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'thresholds': {
        'tier1': acc_mean - acc_std,
        'tier2': acc_mean - acc_std,
        'tier3_overall': acc_mean - 0.005,
        'tier3_per_unit_drop': 0.05,
    },
}

if io_samples:
    baseline['io_profile'] = {
        'read_bytes': {
            'mean': statistics.mean([s['read_bytes'] for s in io_samples]),
            'std': statistics.stdev([s['read_bytes'] for s in io_samples]) if len(io_samples) > 1 else 0.0,
        },
        'write_bytes': {
            'mean': statistics.mean([s['write_bytes'] for s in io_samples]),
            'std': statistics.stdev([s['write_bytes'] for s in io_samples]) if len(io_samples) > 1 else 0.0,
        },
        'syscr': {
            'mean': statistics.mean([s['syscr'] for s in io_samples]),
            'std': statistics.stdev([s['syscr'] for s in io_samples]) if len(io_samples) > 1 else 0.0,
        },
        'syscw': {
            'mean': statistics.mean([s['syscw'] for s in io_samples]),
            'std': statistics.stdev([s['syscw'] for s in io_samples]) if len(io_samples) > 1 else 0.0,
        },
        'available': True,
    }
    print(f'I/O profile captured: read={baseline["io_profile"]["read_bytes"]["mean"]:.0f}B, '
          f'write={baseline["io_profile"]["write_bytes"]["mean"]:.0f}B')
else:
    baseline['io_profile'] = {
        'available': False,
        'reason': 'Platform does not support /proc/self/io',
    }
    print('INFO: I/O profiling unavailable on this platform. Continuing without I/O metrics.')

json.dump(baseline, open(bl, 'w'), indent=2)
print(f'Baseline locked: acc={acc_mean:.4f}+-{acc_std:.4f}, speed={spd_mean:.2f}+-{spd_std:.2f}s')
"""

_SELECT_TIER_SCRIPT = """\
import json, os, re, subprocess, sys

p = sys.argv[1]
subprocess.run(['git', 'checkout', 'main'], cwd=p, check=True)
focus_path = os.path.join(p, '.factory', 'sorting', 'focus.txt')
exp_path = os.path.join(p, '.factory', 'sorting', 'experiments.jsonl')
out_path = os.path.join(p, '.factory', 'sorting', 'tier-selection.json')
tier = None
focus = None
reason = 'default'
if os.path.exists(focus_path):
    focus = open(focus_path).read().strip()
    fl = focus.lower()
    if re.search(r'tier\\s*1|config', fl):
        tier = 1
        reason = f'focus: {focus}'
    elif re.search(r'tier\\s*2|profil', fl):
        tier = 2
        reason = f'focus: {focus}'
    elif re.search(r'tier\\s*3|algorithm', fl):
        tier = 3
        reason = f'focus: {focus}'
if tier is None:
    experiments = []
    if os.path.exists(exp_path):
        for line in open(exp_path):
            line = line.strip()
            if line:
                try:
                    experiments.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if not experiments:
        tier = 1
        reason = 'no experiments yet, starting at tier 1'
    else:
        for check_tier in [1, 2, 3]:
            tier_exps = [e for e in experiments if e.get('tier') == check_tier]
            if len(tier_exps) < 3:
                tier = check_tier
                reason = f'tier {check_tier} has {len(tier_exps)} experiments (< 3)'
                break
            recent = tier_exps[-3:]
            deltas = [abs(e.get('speed_delta_pct', 0)) for e in recent]
            if all(d < 1.0 for d in deltas):
                reason = f'tier {check_tier} plateau detected ({deltas})'
                continue
            else:
                tier = check_tier
                reason = f'tier {check_tier} still improving'
                break
        if tier is None:
            tier = 0
            reason = 'all tiers plateaued'
result = {'tier': tier, 'focus': focus, 'reason': reason}
json.dump(result, open(out_path, 'w'), indent=2)
print(f'Selected tier {tier}: {reason}')
"""

_RUN_BENCHMARK_SCRIPT = _IO_CAPTURE_FN + """\
import json, subprocess, sys, os

p = sys.argv[1]

cfg = json.load(open(os.path.join(p, '.factory', 'config.json')))
sb = cfg.get('sorting_benchmark', cfg.get('research_target', {}))
cmd = sb.get('command', sb.get('run_command', ''))
if not cmd:
    print('ERROR: No benchmark command in config.json', file=sys.stderr)
    sys.exit(1)

out = os.path.join(p, '.factory', 'sorting', 'benchmark-result.json')
os.makedirs(os.path.dirname(out), exist_ok=True)

io_before = capture_io()

run_cmd = cmd.replace('{recording}', sb.get('recording', '')).replace('{output}', out)
r = subprocess.run(run_cmd, shell=True, capture_output=True, text=True, cwd=p)
if r.returncode != 0:
    print(f'Benchmark failed: {r.stderr}', file=sys.stderr)
    sys.exit(1)

io_after = capture_io()

if not os.path.exists(out):
    data = json.loads(r.stdout)
    json.dump(data, open(out, 'w'), indent=2)

data = json.load(open(out))
assert 'accuracy' in data, 'Missing accuracy in benchmark result'
assert 'speed_seconds' in data, 'Missing speed_seconds in benchmark result'

if io_before is not None and io_after is not None:
    data['io_profile'] = {
        'read_bytes': io_after['read_bytes'] - io_before['read_bytes'],
        'write_bytes': io_after['write_bytes'] - io_before['write_bytes'],
        'syscr': io_after['syscr'] - io_before['syscr'],
        'syscw': io_after['syscw'] - io_before['syscw'],
        'available': True,
    }
else:
    data['io_profile'] = {'available': False}

json.dump(data, open(out, 'w'), indent=2)

print(f'Benchmark: accuracy={data["accuracy"]:.4f}, speed={data["speed_seconds"]:.2f}s')
"""

_CONFIRM_BENCHMARK_SCRIPT = _IO_CAPTURE_FN + """\
import json, subprocess, sys, statistics, os, datetime

p = sys.argv[1]

cfg = json.load(open(os.path.join(p, '.factory', 'config.json')))
sb = cfg.get('sorting_benchmark', cfg.get('research_target', {}))
cmd = sb.get('command', sb.get('run_command', ''))
if not cmd:
    print('ERROR: No benchmark command in config.json', file=sys.stderr)
    sys.exit(1)

br_path = os.path.join(p, '.factory', 'sorting', 'benchmark-result.json')
run1 = json.load(open(br_path))

results = [run1]
io_samples = []

if run1.get('io_profile', {}).get('available'):
    io_samples.append({
        'read_bytes': run1['io_profile']['read_bytes'],
        'write_bytes': run1['io_profile']['write_bytes'],
        'syscr': run1['io_profile']['syscr'],
        'syscw': run1['io_profile']['syscw'],
    })

for i in range(2):
    print(f'Confirmation run {i+2}/3')

    io_before = capture_io()

    out_i = os.path.join(p, '.factory', 'sorting', f'benchmark_confirm_{i}.json')
    run_cmd = cmd.replace('{recording}', sb.get('recording', '')).replace('{output}', out_i)
    r = subprocess.run(run_cmd, shell=True, capture_output=True, text=True, cwd=p)
    if r.returncode != 0:
        print(f'Benchmark failed: {r.stderr}', file=sys.stderr)
        sys.exit(1)

    io_after = capture_io()

    if os.path.exists(out_i):
        results.append(json.load(open(out_i)))
    else:
        results.append(json.loads(r.stdout))

    if io_before is not None and io_after is not None:
        io_delta = {k: io_after[k] - io_before[k] for k in io_before}
        io_samples.append(io_delta)

accs = [r['accuracy'] for r in results]
speeds = [r['speed_seconds'] for r in results]
acc_mean = statistics.mean(accs)
acc_std = statistics.stdev(accs) if len(accs) > 1 else 0.0
spd_mean = statistics.mean(speeds)
spd_std = statistics.stdev(speeds) if len(speeds) > 1 else 0.0

pu = {}
for r in results:
    for uid, val in r.get('per_unit_accuracy', {}).items():
        pu.setdefault(uid, []).append(val)
pu_stats = {uid: {'mean': statistics.mean(vals), 'std': statistics.stdev(vals) if len(vals) > 1 else 0.0}
             for uid, vals in pu.items()}

st = {}
for r in results:
    for stage, val in r.get('stage_timing', {}).items():
        st.setdefault(stage, []).append(val)
st_stats = {stage: {'mean': statistics.mean(vals), 'std': statistics.stdev(vals) if len(vals) > 1 else 0.0}
             for stage, vals in st.items()}

averaged = {
    'accuracy': {'mean': acc_mean, 'std': acc_std},
    'speed_seconds': {'mean': spd_mean, 'std': spd_std},
    'per_unit_accuracy': pu_stats,
    'stage_timing': st_stats,
    'n_runs': 3,
    'confirmed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
}

if io_samples:
    averaged['io_profile'] = {
        'read_bytes': {
            'mean': statistics.mean([s['read_bytes'] for s in io_samples]),
            'std': statistics.stdev([s['read_bytes'] for s in io_samples]) if len(io_samples) > 1 else 0.0,
        },
        'write_bytes': {
            'mean': statistics.mean([s['write_bytes'] for s in io_samples]),
            'std': statistics.stdev([s['write_bytes'] for s in io_samples]) if len(io_samples) > 1 else 0.0,
        },
        'syscr': {
            'mean': statistics.mean([s['syscr'] for s in io_samples]),
            'std': statistics.stdev([s['syscr'] for s in io_samples]) if len(io_samples) > 1 else 0.0,
        },
        'syscw': {
            'mean': statistics.mean([s['syscw'] for s in io_samples]),
            'std': statistics.stdev([s['syscw'] for s in io_samples]) if len(io_samples) > 1 else 0.0,
        },
        'available': True,
    }
else:
    averaged['io_profile'] = {
        'available': False,
        'reason': 'Platform does not support /proc/self/io',
    }

json.dump(averaged, open(br_path, 'w'), indent=2)
print(f'Confirmed (3 runs): acc={acc_mean:.4f}+-{acc_std:.4f}, speed={spd_mean:.2f}+-{spd_std:.2f}s')
"""

# ── AgentNode prompt constants ───────────────────────────────────

_RESEARCHER_DISCOVER_PARAMS_PROMPT = """\
You are a Researcher investigating tunable configuration parameters \
for a Neuropixels-scale, GPU-accelerated spike sorting pipeline.

## Context
- Speed is the optimization target. Accuracy is a hard floor.
- Scale: 384+ channels, 30kHz sampling, millions of samples per recording.
- GPU-accelerated sorters (e.g. Kilosort, YASS) have many tunable parameters.

## Your Task
1. Discover ALL config files in the project (YAML, JSON, TOML, INI, CFG).
2. Extract every tunable parameter with its current value, valid range, and default.
3. Rank parameters by expected speed impact (highest first).
4. Note which parameters affect accuracy (these need careful testing).
5. Write your findings to `.factory/sorting/research-params.md`.

## Output Format
A markdown file with:
- Table of all parameters (name, file, current value, range, speed impact estimate)
- Top 5 recommendations for speed improvement
- Accuracy-sensitive parameters flagged with warnings

Read: .factory/sorting/baseline.json
Write output to: .factory/sorting/research-params.md"""

_RESEARCHER_PROFILE_PROMPT = """\
You are a Researcher profiling a Neuropixels-scale, GPU-accelerated spike \
sorting pipeline to identify performance bottlenecks.

## Context
- Speed is the optimization target. Accuracy is a hard floor.
- Scale: 384+ channels, 30kHz sampling, millions of samples.
- Read baseline at `.factory/sorting/baseline.json`.
- **Baseline contains measured stage timing:** `.stage_timing` has
  `{stage_name: {mean: <seconds>, std: <seconds>}` averaged from 3 runs.
  These are ground-truth measurements — use them, do not estimate.

## Your Task
1. Identify all pipeline stages (preprocessing, detection, clustering, etc.).
2. Read `.stage_timing` from `.factory/sorting/baseline.json` for actual
   measured wall-clock time per stage. Use these as ground truth.
3. Rank stages by measured time (highest `mean` first) — the slowest stage
   is the primary optimization target.
4. For each stage, determine if it is GPU-bound or CPU-bound via code analysis.
5. Identify memory allocation patterns and potential bottlenecks.

## Gap Detection Analysis

After profiling stage timings, perform gap detection:

1. **Calculate gap**:
   - Read `speed_seconds.mean` from `baseline.json` (total elapsed time)
   - Sum all stage `mean` values from `.stage_timing` in `baseline.json`
   - Calculate: `gap_seconds = total_elapsed - sum_stage_times`
   - Calculate: `gap_pct = (gap_seconds / total_elapsed) * 100`

2. **Evaluate significance**:
   - Gap is significant if `gap_pct > 10.0`
   - Gaps under 10% are normal measurement noise and overhead

3. **Generate hypotheses** (if significant):
   - Inter-stage serialization (pickle/unpickle, file I/O between stages)
   - GPU memory transfers (host↔device copies between stages)
   - I/O waits (disk reads/writes not captured in stage timers)
   - Process orchestration (subprocess spawn, stage handoff logic)

4. **Write gap analysis**:
   - Add `gap_analysis` section to stage-timing.json (see output format below)
   - Include gap analysis section in the profiling report
   - If gap > 10%, rank the gap as a potential optimization target alongside
     hot stages — a 25% gap may be more impactful than the slowest individual
     stage
   - If gap > 20%, flag it as a HIGH PRIORITY optimization target

**Example**: If detection=120s, clustering=450s, refinement=158s (sum=728s),
but total benchmark time (`speed_seconds.mean`) is 960s, the 232s gap (24%)
is a major optimization signal worth investigating before optimizing any
individual stage.

## I/O Analysis

If `baseline.json` contains an `io_profile` section with `available: true`:
1. Report the baseline I/O profile (read_bytes, write_bytes, syscr, syscw)
2. Convert bytes to human-readable units (MB or GB)
3. Note whether I/O volume is disproportionate to data size
4. Include I/O insights in the profiling report under an "I/O Profile" section

If `io_profile` is missing or `available: false`, skip this section silently
(do NOT warn — the platform may not support /proc/self/io).

## Output

Write dual output:
- `.factory/sorting/research-profile.md`: detailed profiling report with:
  - Stages ranked by measured baseline time (slowest first)
  - Gap detection analysis section (if gap > 10%)
  - I/O profile analysis section (if available)
  - GPU/CPU binding analysis per stage
  - Memory allocation patterns
- `.factory/sorting/stage-timing.json`: structured timing data with format:
  ```json
  {
    "total_elapsed_seconds": <speed_seconds.mean from baseline>,
    "sum_stage_seconds": <sum of all stage means>,
    "gap_analysis": {
      "gap_seconds": <float>,
      "gap_pct": <float>,
      "gap_threshold_exceeded": <bool>,
      "gap_threshold_pct": 10.0,
      "gap_hypotheses": [<list of string hypotheses if gap > 10%>]
    },
    "stages": {
      "<stage_name>": {
        "baseline_mean": <float>,
        "baseline_std": <float>,
        "pct_of_total": <float>,
        "gpu_bound": <bool>,
        "notes": "<string>"
      }
    }
  }
  ```

Read: .factory/sorting/baseline.json
Write output to: .factory/sorting/research-profile.md, .factory/sorting/stage-timing.json"""

_RESEARCHER_EXPLORE_ALTERNATIVES_PROMPT = """\
You are a Researcher exploring alternative algorithmic approaches \
for Neuropixels-scale spike sorting.

## Context
- Speed is the optimization target. Accuracy is a hard floor.
- Scale: 384+ channels, 30kHz sampling, millions of samples, GPU-accelerated.
- Read baseline at `.factory/sorting/baseline.json`.
- Read stage timing at `.factory/sorting/stage-timing.json` (if available).

## Your Task
1. Explore alternative algorithmic approaches for the bottleneck stages.
2. For each alternative, assess:
   - Expected speed improvement
   - Per-unit accuracy risk (which unit types might be affected)
   - Implementation complexity
   - Evidence from literature or benchmarks
3. Include Neuropixels-scale validation considerations.
4. Write findings to `.factory/sorting/research-alternatives.md`.

Read: .factory/sorting/baseline.json
Write output to: .factory/sorting/research-alternatives.md"""

_STRATEGIST_T1_PROMPT = """\
You are a Strategist selecting ONE configuration parameter variation to test \
for speed optimization of a spike sorting pipeline.

## Context
- Speed is the optimization target. Accuracy is a hard floor.
- Read the researcher's parameter discovery at `.factory/sorting/research-params.md`.
- Read the baseline at `.factory/sorting/baseline.json` for current performance.
- Check `.factory/sorting/experiments.jsonl` for prior attempts (avoid repeats).

## Your Task
1. Rank discovered parameters by: highest speed impact × lowest accuracy risk.
2. Select ONE parameter variation to test this cycle.
3. Justify why this variation is the best next experiment.
4. Write the strategy to `.factory/strategy/current.md` with:
   - Which parameter to change
   - What value to set (with rationale)
   - Expected speed impact
   - Accuracy risk assessment

Read: .factory/sorting/baseline.json, .factory/sorting/research-params.md
Write output to: .factory/strategy/current.md"""

_STRATEGIST_T2_PROMPT = """\
You are a Strategist identifying ONE hot-path optimization \
for a spike sorting pipeline.

## Context
- Speed is the optimization target. Accuracy is a hard floor.
- Read profiling at `.factory/sorting/research-profile.md` and \
`.factory/sorting/stage-timing.json`.
- Read baseline at `.factory/sorting/baseline.json`.
- Check `.factory/sorting/experiments.jsonl` for prior attempts.

## Your Task
1. Target the PRIMARY bottleneck stage for optimization.
2. Validate the optimization preserves algorithmic behavior \
(same inputs → same outputs).
3. Write strategy to `.factory/strategy/current.md` with:
   - Which stage/function to optimize
   - What optimization to apply
   - Why it preserves correctness
   - Expected speed improvement

Read: .factory/sorting/baseline.json, .factory/sorting/research-profile.md, \
.factory/sorting/stage-timing.json
Write output to: .factory/strategy/current.md"""

_STRATEGIST_T3_PROMPT = """\
You are a Strategist evaluating algorithmic alternatives for spike sorting.

## Context
- Speed is the optimization target. Accuracy is a hard floor.
- STRICT thresholds: 0.5% overall accuracy, 5% per-unit drop maximum.
- Read alternatives at `.factory/sorting/research-alternatives.md`.
- Read baseline at `.factory/sorting/baseline.json`.
- Check `.factory/sorting/experiments.jsonl` for prior attempts.

## Your Task
1. Evaluate risk/reward for each alternative.
2. Select ONE algorithmic change with lowest accuracy risk.
3. Prefer incremental changes over wholesale replacements.
4. Flag specific units that may be at risk.
5. Write strategy to `.factory/strategy/current.md` with:
   - Which algorithm/approach to change
   - Risk assessment per unit type
   - Fallback plan if accuracy drops
   - Expected speed improvement

Read: .factory/sorting/baseline.json, .factory/sorting/research-alternatives.md
Write output to: .factory/strategy/current.md"""

_BUILDER_CONFIG_CHANGE_PROMPT = """\
You are a Builder applying a configuration change to optimize spike sorting speed.

## Context
- Speed is the optimization target. Accuracy is a hard floor.
- Neuropixels-scale: 384+ channels, 30kHz, millions of samples.
- Read the strategy at `.factory/strategy/current.md` for which parameter to change.

## STRICT RULES
- ONLY modify config/parameter files: .yaml, .json, .toml, .cfg, .ini, .conf, .env
- ZERO source code changes: do NOT modify .py, .cu, .cpp, .c, .h, .pyx, .sh files
- A gate will verify no code was changed — if you modify code, you'll be sent back

## Reloop Handling
- If you were sent back from `gate_no_code_changes`: you modified source code. \
Revert those changes and apply the config change using ONLY config files.

## Your Task
1. Apply the parameter change specified in the strategy
2. Verify you only touched config files
3. Write a summary to `.factory/reviews/builder-latest.md`
4. Write the config diff to `.factory/sorting/config-diff.json`

Read: .factory/strategy/current.md
Write output to: .factory/reviews/builder-latest.md, .factory/sorting/config-diff.json"""

_BUILDER_OPTIMIZE_HOTPATH_PROMPT = """\
You are a Builder implementing a hot-path optimization for spike sorting.

## Context
- Speed is the optimization target. Accuracy is a hard floor.
- Neuropixels-scale: 384+ channels, 30kHz, millions of samples, GPU-accelerated.
- Read strategy at `.factory/strategy/current.md`.
- Read stage timing at `.factory/sorting/stage-timing.json`.

## STRICT RULES
- Preserve algorithmic behavior: same inputs MUST produce same outputs.
- Run existing tests to verify correctness after changes.
- Focus on the specific optimization described in the strategy.

## Your Task
1. Implement the optimization from the strategy
2. Run existing tests to verify correctness
3. Write summary to `.factory/reviews/builder-latest.md`

Read: .factory/sorting/stage-timing.json, .factory/strategy/current.md
Write output to: .factory/reviews/builder-latest.md"""

_BUILDER_IMPLEMENT_ALTERNATIVE_PROMPT = """\
You are a Builder implementing an algorithmic change for spike sorting.

## Context
- Speed is the optimization target. Accuracy is a hard floor.
- Neuropixels-scale: 384+ channels, 30kHz, millions of samples, GPU-accelerated.
- STRICT accuracy: 0.5% overall threshold, 5% per-unit drop maximum.
- Read strategy at `.factory/strategy/current.md`.

## STRICT RULES
- Consider per-unit impact — some unit types may be more sensitive.
- Prefer incremental changes over wholesale replacements.
- Run existing tests after implementation.

## Reloop Handling
- If sent back from `gate_accuracy_t3`: overall accuracy dropped >0.5%. \
Investigate which part of the algorithm caused the regression.
- If sent back from `gate_per_unit_accuracy`: specific units regressed >5%. \
The feedback will list which units. Adjust the algorithm to handle those unit \
types better, or add special-case handling.

## Your Task
1. Implement the algorithmic change from the strategy
2. Run existing tests to verify correctness
3. Write summary to `.factory/reviews/builder-latest.md`

Read: .factory/strategy/current.md
Write output to: .factory/reviews/builder-latest.md"""

_ARCHIVE_RESULT_PROMPT_TEMPLATE = """\
You are the Archivist recording the result of a sorting optimization experiment.

## Your Task

1. Read the benchmark result at `.factory/sorting/benchmark-result.json`
   - If `accuracy` is a dict with `mean`/`std` keys, the file contains **averaged
     3-run** data (from confirm_benchmark). Use `.accuracy.mean` and
     `.speed_seconds.mean` for deltas.
   - If `accuracy` is a plain float, it is a **single-run** result (catastrophic
     gate halted before confirmation runs). Use `.accuracy` and `.speed_seconds`
     directly.
2. Read the baseline at `.factory/sorting/baseline.json`
3. Compute deltas:
   - `speed_delta = baseline_speed - result_speed` (positive = faster)
   - `speed_delta_pct = (speed_delta / baseline_speed) * 100`
   - `accuracy_delta = result_accuracy - baseline_accuracy`
   - `per_unit_deltas`: for each unit, `result - baseline_mean`
   - `stage_timing_deltas`: iterate over the **union** of all stage names
     from baseline `.stage_timing` and result `.stage_timing`:
     - **Stage in both baseline and result:** Read baseline stage time from
       `baseline.stage_timing[stage].mean`. Read result stage time: if result
       `.stage_timing[stage]` is a dict with `mean`/`std`, use `.mean`; if it
       is a plain float, use directly. Compute:
       `delta = baseline_mean - result_time` (positive = faster),
       `pct = (delta / baseline_mean) * 100`.
       Store as `{stage: {delta: float, pct: float`.
     - **Stage in result but NOT in baseline (new stage):** Store as
       `{stage: {delta: null, pct: null, status: "new"}}`. There is no
       baseline reference for comparison.
     - **Stage in baseline but NOT in result (removed stage):** Store as
       `{stage: {delta: null, pct: null, status: "removed"}}`. The stage
       existed in the baseline pipeline but is absent from the result.
   - If either file has empty/missing `stage_timing`, set
     `stage_timing_deltas` to `null`
4. Determine verdict:
   - If you reached this node via a PROCEED edge from gate_accuracy
     (or gate_per_unit_accuracy for tier 3): verdict = 'keep'
   - If you reached this node via a HALT edge (catastrophic gate or
     accuracy gate failure): verdict = 'revert'
   - If reverting, run `git revert HEAD --no-edit` to undo the change
5. Read the tier from `.factory/sorting/tier-selection.json`
6. Append ONE JSONL line to `.factory/sorting/experiments.jsonl` with fields:
   `tier`, `change` (brief description), `speed_delta`, `speed_delta_pct`, \
`accuracy_delta`, `per_unit_deltas`, `stage_timing`, `stage_timing_deltas`, \
`verdict`, `timestamp` (ISO8601)

## PR Comment (Tier __TIER_NUM__)
If the verdict is **'keep'**, post a PR comment with the benchmark summary:
1. Find the PR number for the current branch:
   ```
   gh pr list --head $(git branch --show-current) --json number -q '.[0].number'
   ```
2. Post the comment:
   ```
   gh pr comment <PR_NUMBER> --body '<message>'
   ```
   The message must include:
   - **Tier**: __TIER_NUM__
   - **Change**: brief description of what was changed
   - **Speed delta**: absolute value (e.g. -1.23s) and percentage (e.g. -15.2%)
   - **Accuracy delta**: e.g. +0.0012
   - **Per-stage timing** (if `stage_timing_deltas` is not null): a table
     showing each stage name, baseline time, result time, delta, pct change,
     and status. Include rows for new and removed stages.
     Example:
     ```
     | Stage        | Baseline (s) | Result (s) | Delta (s) | Change (%) | Status  |
     |--------------|-------------|------------|-----------|------------|--------|
     | clustering   | 4.21        | 3.55       | +0.66     | +15.7%     | ✓       |
     | detection    | 2.10        | 2.08       | +0.02     | +1.0%      | ✓       |
     | postprocess  | —           | 0.45       | —         | —          | NEW     |
     | legacy_merge | 1.30        | —          | —         | —          | REMOVED |
     ```
   - **Verdict**: keep
   - **Confidence**: mean ± std from 3 confirmation runs

If the verdict is **'revert'**, do NOT post a PR comment.

## Rules
- Append exactly ONE line (valid JSON) to experiments.jsonl
- Do NOT overwrite the file — append only
- Include all fields even if some are null or empty
- Use the actual measured values, not estimates

Read: .factory/sorting/baseline.json, .factory/sorting/benchmark-result.json
Write output to: .factory/sorting/experiments.jsonl"""


def _archive_prompt(tier: int) -> str:
    """Return the archive result prompt with the tier number filled in."""
    return _ARCHIVE_RESULT_PROMPT_TEMPLATE.replace("__TIER_NUM__", str(tier))


# ── Gate evaluator command constants ─────────────────────────────

_GATE_TIER_CMD_TEMPLATE = (
    "python3 -c \""
    "import json, os;"
    "p = '{project_path}';"
    "sel = json.load(open(os.path.join(p, '.factory', 'sorting', 'tier-selection.json')));"
    "t = sel.get('tier', 0);"
    "print('pass: tier matches' if t == __TIER__ else 'fail: tier is ' + str(t))"
    "\""
)

_GATE_CATASTROPHIC_CMD = (
    "python3 -c \""
    "import json, os;"
    "p = '{project_path}';"
    "bl = json.load(open(os.path.join(p, '.factory', 'sorting', 'baseline.json')));"
    "br = json.load(open(os.path.join(p, '.factory', 'sorting', 'benchmark-result.json')));"
    "acc = br['accuracy'];"
    "bl_mean = bl['accuracy']['mean'];"
    "threshold = bl_mean * 0.9;"
    "print(f'pass: acc={acc:.4f} >= catastrophic threshold={threshold:.4f}'"
    " if acc >= threshold else"
    " f'fail: catastrophic drop acc={acc:.4f} < {threshold:.4f}"
    " (>{10}% below baseline={bl_mean:.4f})')"
    "\""
)

_GATE_ACCURACY_T12_CMD = (
    "python3 -c \""
    "import json, os;"
    "p = '{project_path}';"
    "bl = json.load(open(os.path.join(p, '.factory', 'sorting', 'baseline.json')));"
    "br = json.load(open(os.path.join(p, '.factory', 'sorting', 'benchmark-result.json')));"
    "threshold = bl['accuracy']['mean'] - bl['accuracy']['std'];"
    "acc = br['accuracy'];"
    "acc = acc['mean'] if isinstance(acc, dict) else acc;"
    "bl_mean = bl['accuracy']['mean'];"
    "delta = acc - bl_mean;"
    "print(f'pass: acc={acc:.4f} >= threshold={threshold:.4f} (delta={delta:+.4f})'"
    " if acc >= threshold else"
    " f'fail: acc={acc:.4f} < threshold={threshold:.4f}"
    " (delta={delta:+.4f}, baseline={bl_mean:.4f})')"
    "\""
)

_GATE_ACCURACY_T3_CMD = (
    "python3 -c \""
    "import json, os;"
    "p = '{project_path}';"
    "bl = json.load(open(os.path.join(p, '.factory', 'sorting', 'baseline.json')));"
    "br = json.load(open(os.path.join(p, '.factory', 'sorting', 'benchmark-result.json')));"
    "threshold = bl['accuracy']['mean'] - 0.005;"
    "acc = br['accuracy'];"
    "acc = acc['mean'] if isinstance(acc, dict) else acc;"
    "bl_mean = bl['accuracy']['mean'];"
    "delta = acc - bl_mean;"
    "print(f'pass: acc={acc:.4f} >= threshold={threshold:.4f} (delta={delta:+.4f})'"
    " if acc >= threshold else"
    " f'fail: acc={acc:.4f} < threshold={threshold:.4f}"
    " (delta={delta:+.4f}, baseline={bl_mean:.4f})')"
    "\""
)

_GATE_PER_UNIT_ACCURACY_SCRIPT = """\
import json, os
p = '{project_path}'
bl = json.load(open(os.path.join(p, '.factory', 'sorting', 'baseline.json')))
br = json.load(open(os.path.join(p, '.factory', 'sorting', 'benchmark-result.json')))
pu_bl = bl.get('per_unit_accuracy', {})
pu_br = br.get('per_unit_accuracy', {})
drops = []
for uid, stats in pu_bl.items():
    bl_mean = stats['mean']
    if bl_mean == 0:
        continue
    if uid not in pu_br:
        drops.append((uid, 1.0))
        continue
    cur = pu_br[uid]
    if isinstance(cur, dict):
        cur = cur['mean']
    drop = (bl_mean - cur) / bl_mean
    if drop > 0.05:
        drops.append((uid, drop))
if len(drops) == 0:
    print('pass: all units within 5% of baseline')
elif len(drops) <= 2:
    details = ', '.join(f'{u}: {d:.1%} drop' for u, d in drops)
    print(f'reloop: {len(drops)} unit(s) regressed — {details}')
else:
    details = ', '.join(f'{u}: {d:.1%} drop' for u, d in drops)
    print(f'fail: {len(drops)} units regressed (>2) — {details}')
"""

_GATE_PER_UNIT_ACCURACY_CMD = (
    "cat > /tmp/_gate_per_unit.py << 'PYEOF'\n"
    + _GATE_PER_UNIT_ACCURACY_SCRIPT
    + "PYEOF\n"
    "python3 /tmp/_gate_per_unit.py"
)


# ── Workflow definition ──────────────────────────────────────────


def workflow() -> Workflow:
    """Build and return the optimize-sorting workflow graph."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── FnNodes ───────────────────────────────────────────────────

    nodes["lock_baseline"] = FnNode(
        id="lock_baseline",
        command=(
            "cat > /tmp/_lock_baseline.py << 'PYEOF'\n"
            + _LOCK_BASELINE_SCRIPT
            + "PYEOF\n"
            "python3 /tmp/_lock_baseline.py {project_path}"
        ),
        reads=set(),
        writes={".factory/sorting/baseline.json"},
    )

    nodes["select_tier"] = FnNode(
        id="select_tier",
        command=(
            "cat > /tmp/_select_tier.py << 'PYEOF'\n"
            + _SELECT_TIER_SCRIPT
            + "PYEOF\n"
            "python3 /tmp/_select_tier.py {project_path}"
        ),
        reads={".factory/sorting/baseline.json"},
        writes={".factory/sorting/tier-selection.json"},
    )

    for tier_id in ("t1", "t2", "t3"):
        nodes[f"run_benchmark_{tier_id}"] = FnNode(
            id=f"run_benchmark_{tier_id}",
            command=(
                "cat > /tmp/_run_benchmark.py << 'PYEOF'\n"
                + _RUN_BENCHMARK_SCRIPT
                + "PYEOF\n"
                "python3 /tmp/_run_benchmark.py {project_path}"
            ),
            reads={".factory/sorting/baseline.json"},
            writes={".factory/sorting/benchmark-result.json"},
        )

        nodes[f"confirm_benchmark_{tier_id}"] = FnNode(
            id=f"confirm_benchmark_{tier_id}",
            command=(
                "cat > /tmp/_confirm_benchmark.py << 'PYEOF'\n"
                + _CONFIRM_BENCHMARK_SCRIPT
                + "PYEOF\n"
                "python3 /tmp/_confirm_benchmark.py {project_path}"
            ),
            reads={".factory/sorting/benchmark-result.json"},
            writes={".factory/sorting/benchmark-result.json"},
        )

    # ── GateNodes ─────────────────────────────────────────────────

    for tier_num, gate_id in ((1, "gate_is_tier1"), (2, "gate_is_tier2"), (3, "gate_is_tier3")):
        nodes[gate_id] = GateNode(
            id=gate_id,
            evaluator_type="fn",
            evaluator_command=_GATE_TIER_CMD_TEMPLATE.replace(
                "__TIER__", str(tier_num)
            ),
            reads={".factory/sorting/tier-selection.json"},
        )

    nodes["gate_no_code_changes"] = GateNode(
        id="gate_no_code_changes",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "FILES=$(git diff HEAD --name-only 2>/dev/null || true) && "
            "if [ -z \"$FILES\" ]; then echo 'pass: no changes detected'; exit 0; fi && "
            "CODE_FILES=$(echo \"$FILES\" | grep -v -E "
            "'\\.(json|yaml|yml|toml|cfg|ini|conf|env)$' || true) && "
            "if [ -z \"$CODE_FILES\" ]; then echo 'pass: config-only changes'; exit 0; fi && "
            "echo \"reloop: source code modified — $CODE_FILES\"; exit 0"
        ),
        reads={".factory/reviews/builder-latest.md"},
        # NOTE: max_iterations=3 for RELOOP is handled via workflow slots at runtime
    )

    for tier_id in ("t1", "t2", "t3"):
        nodes[f"gate_catastrophic_{tier_id}"] = GateNode(
            id=f"gate_catastrophic_{tier_id}",
            evaluator_type="fn",
            evaluator_command=_GATE_CATASTROPHIC_CMD,
            reads={
                ".factory/sorting/baseline.json",
                ".factory/sorting/benchmark-result.json",
            },
        )

    nodes["gate_accuracy_t1"] = GateNode(
        id="gate_accuracy_t1",
        evaluator_type="fn",
        evaluator_command=_GATE_ACCURACY_T12_CMD,
        reads={
            ".factory/sorting/baseline.json",
            ".factory/sorting/benchmark-result.json",
        },
    )

    nodes["gate_accuracy_t2"] = GateNode(
        id="gate_accuracy_t2",
        evaluator_type="fn",
        evaluator_command=_GATE_ACCURACY_T12_CMD,
        reads={
            ".factory/sorting/baseline.json",
            ".factory/sorting/benchmark-result.json",
        },
    )

    nodes["gate_accuracy_t3"] = GateNode(
        id="gate_accuracy_t3",
        evaluator_type="fn",
        evaluator_command=_GATE_ACCURACY_T3_CMD,
        reads={
            ".factory/sorting/baseline.json",
            ".factory/sorting/benchmark-result.json",
        },
    )

    nodes["gate_per_unit_accuracy"] = GateNode(
        id="gate_per_unit_accuracy",
        evaluator_type="fn",
        evaluator_command=_GATE_PER_UNIT_ACCURACY_CMD,
        reads={
            ".factory/sorting/baseline.json",
            ".factory/sorting/benchmark-result.json",
        },
        # NOTE: max_iterations=3 for RELOOP is handled via workflow slots at runtime
    )

    # ── AgentNodes ────────────────────────────────────────────────

    nodes["researcher_discover_params"] = AgentNode(
        id="researcher_discover_params",
        role=AgentRole.RESEARCHER,
        timeout=600,
        prompt_template=_RESEARCHER_DISCOVER_PARAMS_PROMPT,
        reads={".factory/sorting/baseline.json"},
        writes={".factory/sorting/research-params.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/sorting/research-params.md", must_exist=True
            ),
        ],
    )

    nodes["researcher_profile_pipeline"] = AgentNode(
        id="researcher_profile_pipeline",
        role=AgentRole.RESEARCHER,
        timeout=600,
        prompt_template=_RESEARCHER_PROFILE_PROMPT,
        reads={".factory/sorting/baseline.json"},
        writes={
            ".factory/sorting/research-profile.md",
            ".factory/sorting/stage-timing.json",
        },
        post_checks=[
            ArtifactCheck(
                path=".factory/sorting/research-profile.md", must_exist=True
            ),
            ArtifactCheck(
                path=".factory/sorting/stage-timing.json", must_exist=True
            ),
        ],
    )

    nodes["researcher_explore_alternatives"] = AgentNode(
        id="researcher_explore_alternatives",
        role=AgentRole.RESEARCHER,
        timeout=600,
        prompt_template=_RESEARCHER_EXPLORE_ALTERNATIVES_PROMPT,
        reads={".factory/sorting/baseline.json"},
        writes={".factory/sorting/research-alternatives.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/sorting/research-alternatives.md", must_exist=True
            ),
        ],
    )

    nodes["strategist_t1"] = AgentNode(
        id="strategist_t1",
        role=AgentRole.STRATEGIST,
        timeout=600,
        prompt_template=_STRATEGIST_T1_PROMPT,
        reads={
            ".factory/sorting/baseline.json",
            ".factory/sorting/research-params.md",
        },
        writes={".factory/strategy/current.md"},
        post_checks=[
            ArtifactCheck(path=".factory/strategy/current.md", must_exist=True),
        ],
    )

    nodes["strategist_t2"] = AgentNode(
        id="strategist_t2",
        role=AgentRole.STRATEGIST,
        timeout=600,
        prompt_template=_STRATEGIST_T2_PROMPT,
        reads={
            ".factory/sorting/baseline.json",
            ".factory/sorting/research-profile.md",
            ".factory/sorting/stage-timing.json",
        },
        writes={".factory/strategy/current.md"},
        post_checks=[
            ArtifactCheck(path=".factory/strategy/current.md", must_exist=True),
        ],
    )

    nodes["strategist_t3"] = AgentNode(
        id="strategist_t3",
        role=AgentRole.STRATEGIST,
        timeout=600,
        prompt_template=_STRATEGIST_T3_PROMPT,
        reads={
            ".factory/sorting/baseline.json",
            ".factory/sorting/research-alternatives.md",
        },
        writes={".factory/strategy/current.md"},
        post_checks=[
            ArtifactCheck(path=".factory/strategy/current.md", must_exist=True),
        ],
    )

    nodes["builder_config_change"] = AgentNode(
        id="builder_config_change",
        role=AgentRole.BUILDER,
        timeout=3600,
        prompt_template=_BUILDER_CONFIG_CHANGE_PROMPT,
        reads={".factory/strategy/current.md"},
        writes={
            ".factory/reviews/builder-latest.md",
            ".factory/sorting/config-diff.json",
        },
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/builder-latest.md", must_exist=True
            ),
            ArtifactCheck(
                path=".factory/sorting/config-diff.json", must_exist=True
            ),
        ],
    )

    nodes["builder_optimize_hotpath"] = AgentNode(
        id="builder_optimize_hotpath",
        role=AgentRole.BUILDER,
        timeout=3600,
        prompt_template=_BUILDER_OPTIMIZE_HOTPATH_PROMPT,
        reads={
            ".factory/sorting/stage-timing.json",
            ".factory/strategy/current.md",
        },
        writes={".factory/reviews/builder-latest.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/builder-latest.md", must_exist=True
            ),
        ],
    )

    nodes["builder_implement_alternative"] = AgentNode(
        id="builder_implement_alternative",
        role=AgentRole.BUILDER,
        timeout=3600,
        prompt_template=_BUILDER_IMPLEMENT_ALTERNATIVE_PROMPT,
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/builder-latest.md", must_exist=True
            ),
        ],
    )

    for tier_num, tier_id in ((1, "t1"), (2, "t2"), (3, "t3")):
        nodes[f"archive_result_{tier_id}"] = AgentNode(
            id=f"archive_result_{tier_id}",
            role=AgentRole.ARCHIVIST,
            model="haiku",
            timeout=300,
            prompt_template=_archive_prompt(tier_num),
            reads={
                ".factory/sorting/baseline.json",
                ".factory/sorting/benchmark-result.json",
            },
            writes={".factory/sorting/experiments.jsonl"},
            post_checks=[
                ArtifactCheck(
                    path=".factory/sorting/experiments.jsonl", must_exist=True
                ),
            ],
        )

    # ── Edges (39 total) ──────────────────────────────────────────

    edges = [
        # ── Baseline + Tier Selection ──
        Edge(source="lock_baseline", target="select_tier"),
        Edge(source="select_tier", target="gate_is_tier1"),
        # ── Tier Routing ──
        Edge(
            source="gate_is_tier1",
            target="researcher_discover_params",
            condition=VerdictType.PROCEED,
        ),
        Edge(
            source="gate_is_tier1",
            target="gate_is_tier2",
            condition=VerdictType.HALT,
        ),
        Edge(
            source="gate_is_tier2",
            target="researcher_profile_pipeline",
            condition=VerdictType.PROCEED,
        ),
        Edge(
            source="gate_is_tier2",
            target="gate_is_tier3",
            condition=VerdictType.HALT,
        ),
        Edge(
            source="gate_is_tier3",
            target="researcher_explore_alternatives",
            condition=VerdictType.PROCEED,
        ),
        # ── Tier 1 Pipeline ──
        Edge(source="researcher_discover_params", target="strategist_t1"),
        Edge(source="strategist_t1", target="builder_config_change"),
        Edge(source="builder_config_change", target="gate_no_code_changes"),
        Edge(
            source="gate_no_code_changes",
            target="run_benchmark_t1",
            condition=VerdictType.PROCEED,
        ),
        Edge(
            source="gate_no_code_changes",
            target="builder_config_change",
            condition=VerdictType.RELOOP,
        ),
        Edge(source="run_benchmark_t1", target="gate_catastrophic_t1"),
        Edge(
            source="gate_catastrophic_t1",
            target="confirm_benchmark_t1",
            condition=VerdictType.PROCEED,
        ),
        Edge(
            source="gate_catastrophic_t1",
            target="archive_result_t1",
            condition=VerdictType.HALT,
        ),
        Edge(source="confirm_benchmark_t1", target="gate_accuracy_t1"),
        Edge(
            source="gate_accuracy_t1",
            target="archive_result_t1",
            condition=VerdictType.PROCEED,
        ),
        Edge(
            source="gate_accuracy_t1",
            target="archive_result_t1",
            condition=VerdictType.HALT,
        ),
        # ── Tier 2 Pipeline ──
        Edge(source="researcher_profile_pipeline", target="strategist_t2"),
        Edge(source="strategist_t2", target="builder_optimize_hotpath"),
        Edge(source="builder_optimize_hotpath", target="run_benchmark_t2"),
        Edge(source="run_benchmark_t2", target="gate_catastrophic_t2"),
        Edge(
            source="gate_catastrophic_t2",
            target="confirm_benchmark_t2",
            condition=VerdictType.PROCEED,
        ),
        Edge(
            source="gate_catastrophic_t2",
            target="archive_result_t2",
            condition=VerdictType.HALT,
        ),
        Edge(source="confirm_benchmark_t2", target="gate_accuracy_t2"),
        Edge(
            source="gate_accuracy_t2",
            target="archive_result_t2",
            condition=VerdictType.PROCEED,
        ),
        Edge(
            source="gate_accuracy_t2",
            target="archive_result_t2",
            condition=VerdictType.HALT,
        ),
        # ── Tier 3 Pipeline ──
        Edge(source="researcher_explore_alternatives", target="strategist_t3"),
        Edge(source="strategist_t3", target="builder_implement_alternative"),
        Edge(source="builder_implement_alternative", target="run_benchmark_t3"),
        Edge(source="run_benchmark_t3", target="gate_catastrophic_t3"),
        Edge(
            source="gate_catastrophic_t3",
            target="confirm_benchmark_t3",
            condition=VerdictType.PROCEED,
        ),
        Edge(
            source="gate_catastrophic_t3",
            target="archive_result_t3",
            condition=VerdictType.HALT,
        ),
        Edge(source="confirm_benchmark_t3", target="gate_accuracy_t3"),
        Edge(
            source="gate_accuracy_t3",
            target="gate_per_unit_accuracy",
            condition=VerdictType.PROCEED,
        ),
        Edge(
            source="gate_accuracy_t3",
            target="archive_result_t3",
            condition=VerdictType.HALT,
        ),
        Edge(
            source="gate_per_unit_accuracy",
            target="archive_result_t3",
            condition=VerdictType.PROCEED,
        ),
        Edge(
            source="gate_per_unit_accuracy",
            target="builder_implement_alternative",
            condition=VerdictType.RELOOP,
        ),
        Edge(
            source="gate_per_unit_accuracy",
            target="archive_result_t3",
            condition=VerdictType.HALT,
        ),
    ]

    # ── Trigger ───────────────────────────────────────────────────

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "optimize-sorting"

    return Workflow(
        name="optimize-sorting",
        nodes=nodes,
        edges=edges,
        start_node="lock_baseline",
        terminal=True,
        trigger=trigger,
    )
