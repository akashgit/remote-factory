"""Optimize-sorting workflow — iterative speed optimization of spike sorting pipelines.

Three-tier DAG with chained binary gate routing. Each CEO invocation traverses
the shared prefix (baseline lock → tier selection → routing gates), enters
exactly one tier subgraph, and terminates at that tier's archive node.

Tiers:
  1. Config sweep — tune parameters without touching source code.
  2. Code optimization — preserve algorithmic behavior, faster execution.
  3. Algorithm changes — strict accuracy thresholds including per-unit gates.

Benchmark JSON output contract:
    {
      "accuracy": float,         // required — overall accuracy [0.0-1.0]
      "speed_seconds": float,    // required — wall-clock time
      "per_unit_accuracy": {},   // required for tier 3 — {unit_id: float}
      "stage_timing": {},        // recommended for tier 2 — {stage_name: float}
    }
"""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)

meta = {
    "name": "optimize-sorting",
    "description": (
        "Optimize-sorting mode — three-tier iterative speed optimization for "
        "spike sorting pipelines with hard accuracy constraints. "
        "Tier 1: config sweep (parameter tuning, no code changes). "
        "Tier 2: code optimization (preserve algorithmic behavior). "
        "Tier 3: algorithm changes (strict 0.5% overall + 5% per-unit accuracy gates). "
        "One experiment per CEO invocation. Terminal mode."
    ),
}


# ── helper functions for command generation ───────────────────────


def _lock_baseline_command() -> str:
    """Inline python3 -c command for the lock_baseline FnNode."""
    return (
        "python3 -c \""
        "import json, subprocess, sys, statistics, os, datetime;"
        "p = '{project_path}';"
        "bl = os.path.join(p, '.factory', 'sorting', 'baseline.json');"
        "os.makedirs(os.path.dirname(bl), exist_ok=True);"
        "exists = os.path.exists(bl);"
        "print(f'baseline exists: {exists}');"
        ""
        "if exists:"
        "    print('Baseline already locked, skipping.');"
        "    sys.exit(0);"
        ""
        "cfg_path = os.path.join(p, '.factory', 'config.json');"
        "cfg = json.load(open(cfg_path));"
        "sb = cfg.get('sorting_benchmark', cfg.get('research_target', {}));"
        "cmd = sb.get('command', sb.get('run_command', ''));"
        ""
        "if not cmd:"
        "    print('ERROR: No sorting_benchmark.command in config.json', file=sys.stderr);"
        "    sys.exit(1);"
        ""
        "N = 3;"
        "results = [];"
        "for i in range(N):"
        "    print(f'Baseline run {i+1}/{N}');"
        "    run_cmd = cmd.replace('{recording}', sb.get('recording', '')).replace('{output}', os.path.join(p, '.factory', 'sorting', f'baseline_run_{i}.json'));"
        "    r = subprocess.run(run_cmd, shell=True, capture_output=True, text=True, cwd=p);"
        "    if r.returncode != 0:"
        "        print(f'Benchmark failed: {r.stderr}', file=sys.stderr);"
        "        sys.exit(1);"
        "    out_file = os.path.join(p, '.factory', 'sorting', f'baseline_run_{i}.json');"
        "    if os.path.exists(out_file):"
        "        results.append(json.load(open(out_file)));"
        "    else:"
        "        results.append(json.loads(r.stdout));"
        ""
        "accs = [r['accuracy'] for r in results];"
        "speeds = [r['speed_seconds'] for r in results];"
        "acc_mean = statistics.mean(accs);"
        "acc_std = statistics.stdev(accs) if len(accs) > 1 else 0.0;"
        "spd_mean = statistics.mean(speeds);"
        "spd_std = statistics.stdev(speeds) if len(speeds) > 1 else 0.0;"
        ""
        "pu = {};"
        "for r in results:"
        "    for uid, val in r.get('per_unit_accuracy', {}).items():"
        "        pu.setdefault(uid, []).append(val);"
        "pu_stats = {uid: {'mean': statistics.mean(vals), 'std': statistics.stdev(vals) if len(vals) > 1 else 0.0} for uid, vals in pu.items()};"
        ""
        "st = {};"
        "for r in results:"
        "    for stage, val in r.get('stage_timing', {}).items():"
        "        st.setdefault(stage, []).append(val);"
        "st_stats = {stage: {'mean': statistics.mean(vals), 'std': statistics.stdev(vals) if len(vals) > 1 else 0.0} for stage, vals in st.items()};"
        ""
        "baseline = {"
        "    'accuracy': {'mean': acc_mean, 'std': acc_std},"
        "    'speed_seconds': {'mean': spd_mean, 'std': spd_std},"
        "    'per_unit_accuracy': pu_stats,"
        "    'stage_timing': st_stats,"
        "    'n_runs': N,"
        "    'locked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),"
        "    'thresholds': {"
        "        'tier1': acc_mean - acc_std,"
        "        'tier2': acc_mean - acc_std,"
        "        'tier3_overall': acc_mean - 0.005,"
        "        'tier3_per_unit_drop': 0.05"
        "    }"
        "};"
        "json.dump(baseline, open(bl, 'w'), indent=2);"
        "print(f'Baseline locked: acc={acc_mean:.4f}+-{acc_std:.4f}, speed={spd_mean:.2f}+-{spd_std:.2f}s')"
        "\""
    )


def _select_tier_command() -> str:
    """Inline python3 -c command for the select_tier FnNode."""
    return (
        "python3 -c \""
        "import json, os, re, subprocess, sys;"
        "p = '{project_path}';"
        "# Reset to main so each experiment measures against the baseline independently."
        "# Experiment branches are created by the builder."
        "subprocess.run(['git', 'checkout', 'main'], cwd=p, check=True);"
        "focus_path = os.path.join(p, '.factory', 'sorting', 'focus.txt');"
        "exp_path = os.path.join(p, '.factory', 'sorting', 'experiments.jsonl');"
        "out_path = os.path.join(p, '.factory', 'sorting', 'tier-selection.json');"
        ""
        "tier = None;"
        "focus = None;"
        "reason = 'default';"
        ""
        "if os.path.exists(focus_path):"
        "    focus = open(focus_path).read().strip();"
        "    fl = focus.lower();"
        "    if re.search(r'tier\\s*1|config', fl):"
        "        tier = 1;"
        "        reason = f'focus: {focus}';"
        "    elif re.search(r'tier\\s*2|profil', fl):"
        "        tier = 2;"
        "        reason = f'focus: {focus}';"
        "    elif re.search(r'tier\\s*3|algorithm', fl):"
        "        tier = 3;"
        "        reason = f'focus: {focus}';"
        ""
        "if tier is None:"
        "    experiments = [];"
        "    if os.path.exists(exp_path):"
        "        for line in open(exp_path):"
        "            line = line.strip();"
        "            if line:"
        "                try:"
        "                    experiments.append(json.loads(line));"
        "                except json.JSONDecodeError:"
        "                    pass;"
        ""
        "    if not experiments:"
        "        tier = 1;"
        "        reason = 'no experiments yet, starting at tier 1';"
        "    else:"
        "        for check_tier in [1, 2, 3]:"
        "            tier_exps = [e for e in experiments if e.get('tier') == check_tier];"
        "            if len(tier_exps) < 3:"
        "                tier = check_tier;"
        "                reason = f'tier {check_tier} has {len(tier_exps)} experiments (< 3)';"
        "                break;"
        "            recent = tier_exps[-3:];"
        "            deltas = [abs(e.get('speed_delta_pct', 0)) for e in recent];"
        "            if all(d < 1.0 for d in deltas):"
        "                reason = f'tier {check_tier} plateau detected ({deltas})';"
        "                continue;"
        "            else:"
        "                tier = check_tier;"
        "                reason = f'tier {check_tier} still improving';"
        "                break;"
        "        if tier is None:"
        "            tier = 0;"
        "            reason = 'all tiers plateaued';"
        ""
        "result = {'tier': tier, 'focus': focus, 'reason': reason};"
        "json.dump(result, open(out_path, 'w'), indent=2);"
        "print(f'Selected tier {tier}: {reason}')"
        "\""
    )


def _tier_gate_command(tier_num: int) -> str:
    """Inline python3 -c evaluator command for gate_is_tierN."""
    return (
        "python3 -c \""
        "import json, os;"
        "p = '{project_path}';"
        "sel = json.load(open(os.path.join(p, '.factory', 'sorting', 'tier-selection.json')));"
        f"t = sel.get('tier', 0);"
        f"print('pass: tier matches' if t == {tier_num} else 'fail: tier is ' + str(t))"
        "\""
    )


def _benchmark_command() -> str:
    """Inline python3 -c command for run_benchmark_t{1,2,3} FnNodes."""
    return (
        "python3 -c \""
        "import json, subprocess, sys, os;"
        "p = '{project_path}';"
        "cfg = json.load(open(os.path.join(p, '.factory', 'config.json')));"
        "sb = cfg.get('sorting_benchmark', cfg.get('research_target', {}));"
        "cmd = sb.get('command', sb.get('run_command', ''));"
        ""
        "if not cmd:"
        "    print('ERROR: No benchmark command in config.json', file=sys.stderr);"
        "    sys.exit(1);"
        ""
        "out = os.path.join(p, '.factory', 'sorting', 'benchmark-result.json');"
        "os.makedirs(os.path.dirname(out), exist_ok=True);"
        "run_cmd = cmd.replace('{recording}', sb.get('recording', '')).replace('{output}', out);"
        "r = subprocess.run(run_cmd, shell=True, capture_output=True, text=True, cwd=p);"
        "if r.returncode != 0:"
        "    print(f'Benchmark failed: {r.stderr}', file=sys.stderr);"
        "    sys.exit(1);"
        ""
        "if not os.path.exists(out):"
        "    data = json.loads(r.stdout);"
        "    json.dump(data, open(out, 'w'), indent=2);"
        ""
        "data = json.load(open(out));"
        "assert 'accuracy' in data, 'Missing accuracy in benchmark result';"
        "assert 'speed_seconds' in data, 'Missing speed_seconds in benchmark result';"
        "print(f'Benchmark: accuracy={data[\"accuracy\"]:.4f}, speed={data[\"speed_seconds\"]:.2f}s')"
        "\""
    )


def _benchmark_3x_command() -> str:
    """Inline python3 -c command for confirm_benchmark_t{1,2,3} FnNodes.

    Reads the existing benchmark-result.json (run 1), executes 2 more runs,
    then averages all 3 and overwrites benchmark-result.json with
    mean/std fields (same format as baseline.json).
    """
    return (
        "python3 -c \""
        "import json, subprocess, sys, statistics, os, datetime;"
        "p = '{project_path}';"
        "cfg = json.load(open(os.path.join(p, '.factory', 'config.json')));"
        "sb = cfg.get('sorting_benchmark', cfg.get('research_target', {}));"
        "cmd = sb.get('command', sb.get('run_command', ''));"
        ""
        "if not cmd:"
        "    print('ERROR: No benchmark command in config.json', file=sys.stderr);"
        "    sys.exit(1);"
        ""
        "br_path = os.path.join(p, '.factory', 'sorting', 'benchmark-result.json');"
        "run1 = json.load(open(br_path));"
        "results = [run1];"
        ""
        "for i in range(2):"
        "    print(f'Confirmation run {i+2}/3');"
        "    out_i = os.path.join(p, '.factory', 'sorting', f'benchmark_confirm_{i}.json');"
        "    run_cmd = cmd.replace('{recording}', sb.get('recording', '')).replace('{output}', out_i);"
        "    r = subprocess.run(run_cmd, shell=True, capture_output=True, text=True, cwd=p);"
        "    if r.returncode != 0:"
        "        print(f'Benchmark failed: {r.stderr}', file=sys.stderr);"
        "        sys.exit(1);"
        "    if os.path.exists(out_i):"
        "        results.append(json.load(open(out_i)));"
        "    else:"
        "        results.append(json.loads(r.stdout));"
        ""
        "accs = [r['accuracy'] for r in results];"
        "speeds = [r['speed_seconds'] for r in results];"
        "acc_mean = statistics.mean(accs);"
        "acc_std = statistics.stdev(accs) if len(accs) > 1 else 0.0;"
        "spd_mean = statistics.mean(speeds);"
        "spd_std = statistics.stdev(speeds) if len(speeds) > 1 else 0.0;"
        ""
        "pu = {};"
        "for r in results:"
        "    for uid, val in r.get('per_unit_accuracy', {}).items():"
        "        pu.setdefault(uid, []).append(val);"
        "pu_stats = {uid: {'mean': statistics.mean(vals), 'std': statistics.stdev(vals) if len(vals) > 1 else 0.0} for uid, vals in pu.items()};"
        ""
        "st = {};"
        "for r in results:"
        "    for stage, val in r.get('stage_timing', {}).items():"
        "        st.setdefault(stage, []).append(val);"
        "st_stats = {stage: {'mean': statistics.mean(vals), 'std': statistics.stdev(vals) if len(vals) > 1 else 0.0} for stage, vals in st.items()};"
        ""
        "averaged = {"
        "    'accuracy': {'mean': acc_mean, 'std': acc_std},"
        "    'speed_seconds': {'mean': spd_mean, 'std': spd_std},"
        "    'per_unit_accuracy': pu_stats,"
        "    'stage_timing': st_stats,"
        "    'n_runs': 3,"
        "    'confirmed_at': datetime.datetime.now(datetime.timezone.utc).isoformat()"
        "};"
        "json.dump(averaged, open(br_path, 'w'), indent=2);"
        "print(f'Confirmed (3 runs): acc={acc_mean:.4f}+-{acc_std:.4f}, speed={spd_mean:.2f}+-{spd_std:.2f}s')"
        "\""
    )


def _catastrophic_gate_command() -> str:
    """Inline python3 -c evaluator command for gate_catastrophic_t{1,2,3}.

    Only catches catastrophic accuracy drops (>10% below baseline mean).
    Outputs pass (PROCEED) or fail (HALT).
    """
    return (
        "python3 -c \""
        "import json, os;"
        "p = '{project_path}';"
        "bl = json.load(open(os.path.join(p, '.factory', 'sorting', 'baseline.json')));"
        "br = json.load(open(os.path.join(p, '.factory', 'sorting', 'benchmark-result.json')));"
        "acc = br['accuracy'];"
        "bl_mean = bl['accuracy']['mean'];"
        "threshold = bl_mean * 0.9;"
        "if acc >= threshold:"
        "    print(f'pass: acc={acc:.4f} >= catastrophic threshold={threshold:.4f}');"
        "else:"
        "    print(f'fail: catastrophic drop acc={acc:.4f} < {threshold:.4f} (>{10}% below baseline={bl_mean:.4f})')"
        "\""
    )


def _config_gate_command() -> str:
    """Shell evaluator_command for gate_no_code_changes."""
    return (
        "cd {project_path} && "
        "FILES=$(git diff HEAD --name-only 2>/dev/null || true) && "
        "if [ -z \"$FILES\" ]; then "
        "echo 'pass: no changes detected'; exit 0; fi && "
        "CODE_FILES=$(echo \"$FILES\" | grep -v -E "
        "'\\.(json|yaml|yml|toml|cfg|ini|conf|env)$' || true) && "
        "if [ -z \"$CODE_FILES\" ]; then "
        "echo 'pass: config-only changes'; exit 0; fi && "
        "echo \"reloop: source code modified — $CODE_FILES\"; exit 0"
    )


def _accuracy_gate_command(tier: int) -> str:
    """Inline python3 -c evaluator command for gate_accuracy_t{1,2,3}.

    Runs AFTER confirm_benchmark so benchmark-result.json has averaged format
    with {accuracy: {mean, std}, ...}.  Falls back to plain float for safety.
    Outputs pass (PROCEED) or fail (HALT) — no reloop since 3-run average is
    definitive.
    """
    if tier in (1, 2):
        threshold_expr = "bl['accuracy']['mean'] - bl['accuracy']['std']"
    else:
        threshold_expr = "bl['accuracy']['mean'] - 0.005"
    return (
        "python3 -c \""
        "import json, os;"
        "p = '{project_path}';"
        "bl = json.load(open(os.path.join(p, '.factory', 'sorting', 'baseline.json')));"
        "br = json.load(open(os.path.join(p, '.factory', 'sorting', 'benchmark-result.json')));"
        f"threshold = {threshold_expr};"
        "acc = br['accuracy'];"
        "if isinstance(acc, dict):"
        "    acc = acc['mean'];"
        "bl_mean = bl['accuracy']['mean'];"
        "delta = acc - bl_mean;"
        "if acc >= threshold:"
        "    print(f'pass: acc={acc:.4f} >= threshold={threshold:.4f} (delta={delta:+.4f})');"
        "else:"
        "    print(f'fail: acc={acc:.4f} < threshold={threshold:.4f} (delta={delta:+.4f}, baseline={bl_mean:.4f})')"
        "\""
    )


def _per_unit_gate_command() -> str:
    """Inline python3 -c evaluator command for gate_per_unit_accuracy.

    Handles averaged format: per_unit_accuracy values may be
    {mean: float, std: float} dicts (from confirm_benchmark) or plain floats.
    """
    return (
        "python3 -c \""
        "import json, os;"
        "p = '{project_path}';"
        "bl = json.load(open(os.path.join(p, '.factory', 'sorting', 'baseline.json')));"
        "br = json.load(open(os.path.join(p, '.factory', 'sorting', 'benchmark-result.json')));"
        "pu_bl = bl.get('per_unit_accuracy', {});"
        "pu_br = br.get('per_unit_accuracy', {});"
        "drops = [];"
        "for uid, stats in pu_bl.items():"
        "    bl_mean = stats['mean'];"
        "    if bl_mean == 0:"
        "        continue;"
        "    if uid not in pu_br:"
        "        drops.append((uid, 1.0));"
        "        continue;"
        "    cur = pu_br[uid];"
        "    if isinstance(cur, dict):"
        "        cur = cur['mean'];"
        "    drop = (bl_mean - cur) / bl_mean;"
        "    if drop > 0.05:"
        "        drops.append((uid, drop));"
        "if len(drops) == 0:"
        "    print('pass: all units within 5% of baseline');"
        "elif len(drops) <= 2:"
        "    details = ', '.join(f'{u}: {d:.1%} drop' for u, d in drops);"
        "    print(f'reloop: {len(drops)} unit(s) regressed — {details}');"
        "else:"
        "    details = ', '.join(f'{u}: {d:.1%} drop' for u, d in drops);"
        "    print(f'fail: {len(drops)} units regressed (>2) — {details}')"
        "\""
    )


def _archive_prompt(tier: int) -> str:
    """Prompt template for archive_result_t{1,2,3} AgentNodes."""
    return (
        "You are the Archivist recording the result of a sorting optimization experiment.\n\n"
        "## Your Task\n\n"
        "1. Read the benchmark result at `.factory/sorting/benchmark-result.json`\n"
        "   - If `accuracy` is a dict with `mean`/`std` keys, the file contains **averaged\n"
        "     3-run** data (from confirm_benchmark). Use `.accuracy.mean` and\n"
        "     `.speed_seconds.mean` for deltas.\n"
        "   - If `accuracy` is a plain float, it is a **single-run** result (catastrophic\n"
        "     gate halted before confirmation runs). Use `.accuracy` and `.speed_seconds`\n"
        "     directly.\n"
        "2. Read the baseline at `.factory/sorting/baseline.json`\n"
        "3. Compute deltas:\n"
        "   - `speed_delta = baseline_speed - result_speed` (positive = faster)\n"
        "   - `speed_delta_pct = (speed_delta / baseline_speed) * 100`\n"
        "   - `accuracy_delta = result_accuracy - baseline_accuracy`\n"
        "   - `per_unit_deltas`: for each unit, `result - baseline_mean`\n"
        "4. Determine verdict:\n"
        "   - If you reached this node via a PROCEED edge from gate_accuracy\n"
        "     (or gate_per_unit_accuracy for tier 3): verdict = 'keep'\n"
        "   - If you reached this node via a HALT edge (catastrophic gate or\n"
        "     accuracy gate failure): verdict = 'revert'\n"
        "   - If reverting, run `git revert HEAD --no-edit` to undo the change\n"
        "5. Read the tier from `.factory/sorting/tier-selection.json`\n"
        "6. Append ONE JSONL line to `.factory/sorting/experiments.jsonl` with fields:\n"
        "   `tier`, `change` (brief description), `speed_delta`, `speed_delta_pct`, "
        "`accuracy_delta`, `per_unit_deltas`, `stage_timing`, `verdict`, `timestamp` (ISO8601)\n\n"
        f"## PR Comment (Tier {tier})\n"
        "If the verdict is **'keep'**, post a PR comment with the benchmark summary:\n"
        "1. Find the PR number for the current branch:\n"
        "   ```\n"
        "   gh pr list --head $(git branch --show-current) --json number -q '.[0].number'\n"
        "   ```\n"
        "2. Post the comment:\n"
        "   ```\n"
        "   gh pr comment <PR_NUMBER> --body '<message>'\n"
        "   ```\n"
        "   The message must include:\n"
        f"   - **Tier**: {tier}\n"
        "   - **Change**: brief description of what was changed\n"
        "   - **Speed delta**: absolute value (e.g. -1.23s) and percentage (e.g. -15.2%)\n"
        "   - **Accuracy delta**: e.g. +0.0012\n"
        "   - **Verdict**: keep\n"
        "   - **Confidence**: mean ± std from 3 confirmation runs\n\n"
        "If the verdict is **'revert'**, do NOT post a PR comment.\n\n"
        "## Rules\n"
        "- Append exactly ONE line (valid JSON) to experiments.jsonl\n"
        "- Do NOT overwrite the file — append only\n"
        "- Include all fields even if some are null or empty\n"
        "- Use the actual measured values, not estimates\n"
    )


# ── prompt templates ─────────────────────────────────────────────


_RESEARCHER_T1_PROMPT = (
    "You are a Researcher investigating tunable configuration parameters for a "
    "Neuropixels-scale, GPU-accelerated spike sorting pipeline.\n\n"
    "## Context\n"
    "- Speed is the optimization target. Accuracy is a hard floor.\n"
    "- Scale: 384+ channels, 30kHz sampling, millions of samples per recording.\n"
    "- GPU-accelerated sorters (e.g. Kilosort, YASS) have many tunable parameters.\n\n"
    "## Your Task\n"
    "1. Discover ALL config files in the project (YAML, JSON, TOML, INI, CFG).\n"
    "2. Extract every tunable parameter with its current value, valid range, "
    "and default.\n"
    "3. Rank parameters by expected speed impact (highest first).\n"
    "4. Note which parameters affect accuracy (these need careful testing).\n"
    "5. Write your findings to `.factory/sorting/research-params.md`.\n\n"
    "## Output Format\n"
    "A markdown file with:\n"
    "- Table of all parameters (name, file, current value, range, speed impact estimate)\n"
    "- Top 5 recommendations for speed improvement\n"
    "- Accuracy-sensitive parameters flagged with warnings\n"
)

_STRATEGIST_T1_PROMPT = (
    "You are a Strategist selecting ONE configuration parameter variation to test "
    "for speed optimization of a spike sorting pipeline.\n\n"
    "## Context\n"
    "- Speed is the optimization target. Accuracy is a hard floor.\n"
    "- Read the researcher's parameter discovery at `.factory/sorting/research-params.md`.\n"
    "- Read the baseline at `.factory/sorting/baseline.json` for current performance.\n"
    "- Check `.factory/sorting/experiments.jsonl` for prior attempts (avoid repeats).\n\n"
    "## Your Task\n"
    "1. Rank discovered parameters by: highest speed impact × lowest accuracy risk.\n"
    "2. Select ONE parameter variation to test this cycle.\n"
    "3. Justify why this variation is the best next experiment.\n"
    "4. Write the strategy to `.factory/strategy/current.md` with:\n"
    "   - Which parameter to change\n"
    "   - What value to set (with rationale)\n"
    "   - Expected speed impact\n"
    "   - Accuracy risk assessment\n"
)

_BUILDER_T1_PROMPT = (
    "You are a Builder applying a configuration change to optimize spike sorting speed.\n\n"
    "## Context\n"
    "- Speed is the optimization target. Accuracy is a hard floor.\n"
    "- Neuropixels-scale: 384+ channels, 30kHz, millions of samples.\n"
    "- Read the strategy at `.factory/strategy/current.md` for which parameter to change.\n\n"
    "## STRICT RULES\n"
    "- ONLY modify config/parameter files: .yaml, .json, .toml, .cfg, .ini, .conf, .env\n"
    "- ZERO source code changes: do NOT modify .py, .cu, .cpp, .c, .h, .pyx, .sh files\n"
    "- A gate will verify no code was changed — if you modify code, you'll be sent back\n\n"
    "## Reloop Handling\n"
    "- If you were sent back from `gate_no_code_changes`: you modified source code. "
    "Revert those changes and apply the config change using ONLY config files.\n\n"
    "## Your Task\n"
    "1. Apply the parameter change specified in the strategy\n"
    "2. Verify you only touched config files\n"
    "3. Write a summary to `.factory/reviews/builder-latest.md`\n"
    "4. Write the config diff to `.factory/sorting/config-diff.json`\n"
)

_RESEARCHER_T2_PROMPT = (
    "You are a Researcher profiling a Neuropixels-scale, GPU-accelerated spike "
    "sorting pipeline to identify performance bottlenecks.\n\n"
    "## Context\n"
    "- Speed is the optimization target. Accuracy is a hard floor.\n"
    "- Scale: 384+ channels, 30kHz sampling, millions of samples.\n"
    "- Read baseline at `.factory/sorting/baseline.json`.\n\n"
    "## Your Task\n"
    "1. Identify all pipeline stages (preprocessing, detection, clustering, etc.).\n"
    "2. Measure or estimate time per stage from code analysis and profiling.\n"
    "3. Distinguish GPU-bound vs CPU-bound stages.\n"
    "4. Identify memory allocation patterns and potential bottlenecks.\n"
    "5. Write dual output:\n"
    "   - `.factory/sorting/research-profile.md`: detailed profiling report\n"
    "   - `.factory/sorting/stage-timing.json`: structured timing data\n"
    "     `{stage_name: {estimated_pct: float, gpu_bound: bool, notes: str}}`\n"
)

_STRATEGIST_T2_PROMPT = (
    "You are a Strategist identifying ONE hot-path optimization for a spike sorting pipeline.\n\n"
    "## Context\n"
    "- Speed is the optimization target. Accuracy is a hard floor.\n"
    "- Read profiling at `.factory/sorting/research-profile.md` and "
    "`.factory/sorting/stage-timing.json`.\n"
    "- Read baseline at `.factory/sorting/baseline.json`.\n"
    "- Check `.factory/sorting/experiments.jsonl` for prior attempts.\n\n"
    "## Your Task\n"
    "1. Target the PRIMARY bottleneck stage for optimization.\n"
    "2. Validate the optimization preserves algorithmic behavior "
    "(same inputs → same outputs).\n"
    "3. Write strategy to `.factory/strategy/current.md` with:\n"
    "   - Which stage/function to optimize\n"
    "   - What optimization to apply\n"
    "   - Why it preserves correctness\n"
    "   - Expected speed improvement\n"
)

_BUILDER_T2_PROMPT = (
    "You are a Builder implementing a hot-path optimization for spike sorting.\n\n"
    "## Context\n"
    "- Speed is the optimization target. Accuracy is a hard floor.\n"
    "- Neuropixels-scale: 384+ channels, 30kHz, millions of samples, GPU-accelerated.\n"
    "- Read strategy at `.factory/strategy/current.md`.\n"
    "- Read stage timing at `.factory/sorting/stage-timing.json`.\n\n"
    "## STRICT RULES\n"
    "- Preserve algorithmic behavior: same inputs MUST produce same outputs.\n"
    "- Run existing tests to verify correctness after changes.\n"
    "- Focus on the specific optimization described in the strategy.\n\n"
    "## Your Task\n"
    "1. Implement the optimization from the strategy\n"
    "2. Run existing tests to verify correctness\n"
    "3. Write summary to `.factory/reviews/builder-latest.md`\n"
)

_RESEARCHER_T3_PROMPT = (
    "You are a Researcher exploring alternative algorithmic approaches for "
    "Neuropixels-scale spike sorting.\n\n"
    "## Context\n"
    "- Speed is the optimization target. Accuracy is a hard floor.\n"
    "- Scale: 384+ channels, 30kHz sampling, millions of samples, GPU-accelerated.\n"
    "- Read baseline at `.factory/sorting/baseline.json`.\n"
    "- Read stage timing at `.factory/sorting/stage-timing.json` (if available).\n\n"
    "## Your Task\n"
    "1. Explore alternative algorithmic approaches for the bottleneck stages.\n"
    "2. For each alternative, assess:\n"
    "   - Expected speed improvement\n"
    "   - Per-unit accuracy risk (which unit types might be affected)\n"
    "   - Implementation complexity\n"
    "   - Evidence from literature or benchmarks\n"
    "3. Include Neuropixels-scale validation considerations.\n"
    "4. Write findings to `.factory/sorting/research-alternatives.md`.\n"
)

_STRATEGIST_T3_PROMPT = (
    "You are a Strategist evaluating algorithmic alternatives for spike sorting.\n\n"
    "## Context\n"
    "- Speed is the optimization target. Accuracy is a hard floor.\n"
    "- STRICT thresholds: 0.5% overall accuracy, 5% per-unit drop maximum.\n"
    "- Read alternatives at `.factory/sorting/research-alternatives.md`.\n"
    "- Read baseline at `.factory/sorting/baseline.json`.\n"
    "- Check `.factory/sorting/experiments.jsonl` for prior attempts.\n\n"
    "## Your Task\n"
    "1. Evaluate risk/reward for each alternative.\n"
    "2. Select ONE algorithmic change with lowest accuracy risk.\n"
    "3. Prefer incremental changes over wholesale replacements.\n"
    "4. Flag specific units that may be at risk.\n"
    "5. Write strategy to `.factory/strategy/current.md` with:\n"
    "   - Which algorithm/approach to change\n"
    "   - Risk assessment per unit type\n"
    "   - Fallback plan if accuracy drops\n"
    "   - Expected speed improvement\n"
)

_BUILDER_T3_PROMPT = (
    "You are a Builder implementing an algorithmic change for spike sorting.\n\n"
    "## Context\n"
    "- Speed is the optimization target. Accuracy is a hard floor.\n"
    "- Neuropixels-scale: 384+ channels, 30kHz, millions of samples, GPU-accelerated.\n"
    "- STRICT accuracy: 0.5% overall threshold, 5% per-unit drop maximum.\n"
    "- Read strategy at `.factory/strategy/current.md`.\n\n"
    "## STRICT RULES\n"
    "- Consider per-unit impact — some unit types may be more sensitive.\n"
    "- Prefer incremental changes over wholesale replacements.\n"
    "- Run existing tests after implementation.\n\n"
    "## Reloop Handling\n"
    "- If sent back from `gate_accuracy_t3`: overall accuracy dropped >0.5%. "
    "Investigate which part of the algorithm caused the regression.\n"
    "- If sent back from `gate_per_unit_accuracy`: specific units regressed >5%. "
    "The feedback will list which units. Adjust the algorithm to handle those "
    "unit types better, or add special-case handling.\n\n"
    "## Your Task\n"
    "1. Implement the algorithmic change from the strategy\n"
    "2. Run existing tests to verify correctness\n"
    "3. Write summary to `.factory/reviews/builder-latest.md`\n"
)


# ── workflow construction ─────────────────────────────────────────


def workflow() -> Workflow:
    """Build the optimize-sorting workflow graph.

    Returns a Workflow with 31 nodes, 39 edges, terminal=True.
    """
    nodes: dict[str, AgentNode | FnNode | GateNode] = {}
    edges: list[Edge] = []

    # ── Shared nodes (1-5) ────────────────────────────────────────

    nodes["lock_baseline"] = FnNode(
        id="lock_baseline",
        command=_lock_baseline_command(),
        reads=set(),
        writes={".factory/sorting/baseline.json"},
    )

    nodes["select_tier"] = FnNode(
        id="select_tier",
        command=_select_tier_command(),
        reads={".factory/sorting/baseline.json"},
        writes={".factory/sorting/tier-selection.json"},
    )

    nodes["gate_is_tier1"] = GateNode(
        id="gate_is_tier1",
        evaluator_type="fn",
        evaluator_command=_tier_gate_command(1),
        reads={".factory/sorting/tier-selection.json"},
        writes=set(),
    )

    nodes["gate_is_tier2"] = GateNode(
        id="gate_is_tier2",
        evaluator_type="fn",
        evaluator_command=_tier_gate_command(2),
        reads={".factory/sorting/tier-selection.json"},
        writes=set(),
    )

    nodes["gate_is_tier3"] = GateNode(
        id="gate_is_tier3",
        evaluator_type="fn",
        evaluator_command=_tier_gate_command(3),
        reads={".factory/sorting/tier-selection.json"},
        writes=set(),
    )

    # ── Tier 1 nodes (6-13) ───────────────────────────────────────

    nodes["researcher_discover_params"] = AgentNode(
        id="researcher_discover_params",
        role=AgentRole.RESEARCHER,
        prompt_template=_RESEARCHER_T1_PROMPT,
        reads={".factory/sorting/baseline.json"},
        writes={".factory/sorting/research-params.md"},
    )

    nodes["strategist_t1"] = AgentNode(
        id="strategist_t1",
        role=AgentRole.STRATEGIST,
        prompt_template=_STRATEGIST_T1_PROMPT,
        reads={".factory/sorting/research-params.md", ".factory/sorting/baseline.json"},
        writes={".factory/strategy/current.md"},
    )

    nodes["builder_config_change"] = AgentNode(
        id="builder_config_change",
        role=AgentRole.BUILDER,
        model="opus",
        timeout=3600,
        prompt_template=_BUILDER_T1_PROMPT,
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md", ".factory/sorting/config-diff.json"},
    )

    nodes["gate_no_code_changes"] = GateNode(
        id="gate_no_code_changes",
        evaluator_type="fn",
        evaluator_command=_config_gate_command(),
        reads={".factory/reviews/builder-latest.md"},
        writes=set(),
    )

    nodes["run_benchmark_t1"] = FnNode(
        id="run_benchmark_t1",
        command=_benchmark_command(),
        reads={".factory/sorting/baseline.json"},
        writes={".factory/sorting/benchmark-result.json"},
    )

    nodes["gate_catastrophic_t1"] = GateNode(
        id="gate_catastrophic_t1",
        evaluator_type="fn",
        evaluator_command=_catastrophic_gate_command(),
        reads={".factory/sorting/benchmark-result.json", ".factory/sorting/baseline.json"},
        writes=set(),
    )

    nodes["confirm_benchmark_t1"] = FnNode(
        id="confirm_benchmark_t1",
        command=_benchmark_3x_command(),
        reads={".factory/sorting/benchmark-result.json"},
        writes={".factory/sorting/benchmark-result.json"},
    )

    nodes["gate_accuracy_t1"] = GateNode(
        id="gate_accuracy_t1",
        evaluator_type="fn",
        evaluator_command=_accuracy_gate_command(1),
        reads={".factory/sorting/benchmark-result.json", ".factory/sorting/baseline.json"},
        writes=set(),
    )

    nodes["archive_result_t1"] = AgentNode(
        id="archive_result_t1",
        role=AgentRole.ARCHIVIST,
        prompt_template=_archive_prompt(tier=1),
        reads={".factory/sorting/benchmark-result.json", ".factory/sorting/baseline.json"},
        writes={".factory/sorting/experiments.jsonl"},
    )

    # ── Tier 2 nodes (13-19) ──────────────────────────────────────

    nodes["researcher_profile_pipeline"] = AgentNode(
        id="researcher_profile_pipeline",
        role=AgentRole.RESEARCHER,
        prompt_template=_RESEARCHER_T2_PROMPT,
        reads={".factory/sorting/baseline.json"},
        writes={".factory/sorting/research-profile.md", ".factory/sorting/stage-timing.json"},
    )

    nodes["strategist_t2"] = AgentNode(
        id="strategist_t2",
        role=AgentRole.STRATEGIST,
        prompt_template=_STRATEGIST_T2_PROMPT,
        reads={
            ".factory/sorting/research-profile.md",
            ".factory/sorting/stage-timing.json",
            ".factory/sorting/baseline.json",
        },
        writes={".factory/strategy/current.md"},
    )

    nodes["builder_optimize_hotpath"] = AgentNode(
        id="builder_optimize_hotpath",
        role=AgentRole.BUILDER,
        model="opus",
        timeout=3600,
        prompt_template=_BUILDER_T2_PROMPT,
        reads={".factory/strategy/current.md", ".factory/sorting/stage-timing.json"},
        writes={".factory/reviews/builder-latest.md"},
    )

    nodes["run_benchmark_t2"] = FnNode(
        id="run_benchmark_t2",
        command=_benchmark_command(),
        reads={".factory/sorting/baseline.json"},
        writes={".factory/sorting/benchmark-result.json"},
    )

    nodes["gate_catastrophic_t2"] = GateNode(
        id="gate_catastrophic_t2",
        evaluator_type="fn",
        evaluator_command=_catastrophic_gate_command(),
        reads={".factory/sorting/benchmark-result.json", ".factory/sorting/baseline.json"},
        writes=set(),
    )

    nodes["confirm_benchmark_t2"] = FnNode(
        id="confirm_benchmark_t2",
        command=_benchmark_3x_command(),
        reads={".factory/sorting/benchmark-result.json"},
        writes={".factory/sorting/benchmark-result.json"},
    )

    nodes["gate_accuracy_t2"] = GateNode(
        id="gate_accuracy_t2",
        evaluator_type="fn",
        evaluator_command=_accuracy_gate_command(2),
        reads={".factory/sorting/benchmark-result.json", ".factory/sorting/baseline.json"},
        writes=set(),
    )

    nodes["archive_result_t2"] = AgentNode(
        id="archive_result_t2",
        role=AgentRole.ARCHIVIST,
        prompt_template=_archive_prompt(tier=2),
        reads={".factory/sorting/benchmark-result.json", ".factory/sorting/baseline.json"},
        writes={".factory/sorting/experiments.jsonl"},
    )

    # ── Tier 3 nodes (20-28) ──────────────────────────────────────

    nodes["researcher_explore_alternatives"] = AgentNode(
        id="researcher_explore_alternatives",
        role=AgentRole.RESEARCHER,
        prompt_template=_RESEARCHER_T3_PROMPT,
        reads={".factory/sorting/baseline.json"},
        writes={".factory/sorting/research-alternatives.md"},
    )

    nodes["strategist_t3"] = AgentNode(
        id="strategist_t3",
        role=AgentRole.STRATEGIST,
        prompt_template=_STRATEGIST_T3_PROMPT,
        reads={".factory/sorting/research-alternatives.md", ".factory/sorting/baseline.json"},
        writes={".factory/strategy/current.md"},
    )

    nodes["builder_implement_alternative"] = AgentNode(
        id="builder_implement_alternative",
        role=AgentRole.BUILDER,
        model="opus",
        timeout=3600,
        prompt_template=_BUILDER_T3_PROMPT,
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    nodes["run_benchmark_t3"] = FnNode(
        id="run_benchmark_t3",
        command=_benchmark_command(),
        reads={".factory/sorting/baseline.json"},
        writes={".factory/sorting/benchmark-result.json"},
    )

    nodes["gate_catastrophic_t3"] = GateNode(
        id="gate_catastrophic_t3",
        evaluator_type="fn",
        evaluator_command=_catastrophic_gate_command(),
        reads={".factory/sorting/benchmark-result.json", ".factory/sorting/baseline.json"},
        writes=set(),
    )

    nodes["gate_per_unit_accuracy"] = GateNode(
        id="gate_per_unit_accuracy",
        evaluator_type="fn",
        evaluator_command=_per_unit_gate_command(),
        reads={".factory/sorting/benchmark-result.json", ".factory/sorting/baseline.json"},
        writes=set(),
    )

    nodes["confirm_benchmark_t3"] = FnNode(
        id="confirm_benchmark_t3",
        command=_benchmark_3x_command(),
        reads={".factory/sorting/benchmark-result.json"},
        writes={".factory/sorting/benchmark-result.json"},
    )

    nodes["gate_accuracy_t3"] = GateNode(
        id="gate_accuracy_t3",
        evaluator_type="fn",
        evaluator_command=_accuracy_gate_command(3),
        reads={".factory/sorting/benchmark-result.json", ".factory/sorting/baseline.json"},
        writes=set(),
    )

    nodes["archive_result_t3"] = AgentNode(
        id="archive_result_t3",
        role=AgentRole.ARCHIVIST,
        prompt_template=_archive_prompt(tier=3),
        reads={".factory/sorting/benchmark-result.json", ".factory/sorting/baseline.json"},
        writes={".factory/sorting/experiments.jsonl"},
    )

    # ── Edges (39 total) ──────────────────────────────────────────

    edges = [
        # Baseline + Tier Selection (2)
        Edge(source="lock_baseline", target="select_tier"),                          # 1
        Edge(source="select_tier", target="gate_is_tier1"),                          # 2

        # Tier Routing Chain (5)
        Edge(source="gate_is_tier1", target="researcher_discover_params",
             condition=VerdictType.PROCEED),                                         # 3
        Edge(source="gate_is_tier1", target="gate_is_tier2",
             condition=VerdictType.HALT),                                            # 4
        Edge(source="gate_is_tier2", target="researcher_profile_pipeline",
             condition=VerdictType.PROCEED),                                         # 5
        Edge(source="gate_is_tier2", target="gate_is_tier3",
             condition=VerdictType.HALT),                                            # 6
        Edge(source="gate_is_tier3", target="researcher_explore_alternatives",
             condition=VerdictType.PROCEED),                                         # 7

        # Tier 1 Subgraph — Config Sweep (11)
        # builder → code gate → benchmark → catastrophic gate → confirm → accuracy gate → archive
        Edge(source="researcher_discover_params", target="strategist_t1"),           # 8
        Edge(source="strategist_t1", target="builder_config_change"),                # 9
        Edge(source="builder_config_change", target="gate_no_code_changes"),         # 10
        Edge(source="gate_no_code_changes", target="run_benchmark_t1",
             condition=VerdictType.PROCEED),                                         # 11
        Edge(source="gate_no_code_changes", target="builder_config_change",
             condition=VerdictType.RELOOP),                                          # 12
        Edge(source="run_benchmark_t1", target="gate_catastrophic_t1"),              # 13
        Edge(source="gate_catastrophic_t1", target="confirm_benchmark_t1",
             condition=VerdictType.PROCEED),                                         # 14
        Edge(source="gate_catastrophic_t1", target="archive_result_t1",
             condition=VerdictType.HALT),                                            # 15
        Edge(source="confirm_benchmark_t1", target="gate_accuracy_t1"),              # 16
        Edge(source="gate_accuracy_t1", target="archive_result_t1",
             condition=VerdictType.PROCEED),                                         # 17
        Edge(source="gate_accuracy_t1", target="archive_result_t1",
             condition=VerdictType.HALT),                                            # 18

        # Tier 2 Subgraph — Code Optimization (9)
        # builder → benchmark → catastrophic gate → confirm → accuracy gate → archive
        Edge(source="researcher_profile_pipeline", target="strategist_t2"),          # 19
        Edge(source="strategist_t2", target="builder_optimize_hotpath"),             # 20
        Edge(source="builder_optimize_hotpath", target="run_benchmark_t2"),          # 21
        Edge(source="run_benchmark_t2", target="gate_catastrophic_t2"),              # 22
        Edge(source="gate_catastrophic_t2", target="confirm_benchmark_t2",
             condition=VerdictType.PROCEED),                                         # 23
        Edge(source="gate_catastrophic_t2", target="archive_result_t2",
             condition=VerdictType.HALT),                                            # 24
        Edge(source="confirm_benchmark_t2", target="gate_accuracy_t2"),              # 25
        Edge(source="gate_accuracy_t2", target="archive_result_t2",
             condition=VerdictType.PROCEED),                                         # 26
        Edge(source="gate_accuracy_t2", target="archive_result_t2",
             condition=VerdictType.HALT),                                            # 27

        # Tier 3 Subgraph — Algorithm Changes (12)
        # builder → benchmark → catastrophic gate → confirm → accuracy gate → per-unit gate → archive
        Edge(source="researcher_explore_alternatives", target="strategist_t3"),      # 28
        Edge(source="strategist_t3", target="builder_implement_alternative"),        # 29
        Edge(source="builder_implement_alternative", target="run_benchmark_t3"),     # 30
        Edge(source="run_benchmark_t3", target="gate_catastrophic_t3"),              # 31
        Edge(source="gate_catastrophic_t3", target="confirm_benchmark_t3",
             condition=VerdictType.PROCEED),                                         # 32
        Edge(source="gate_catastrophic_t3", target="archive_result_t3",
             condition=VerdictType.HALT),                                            # 33
        Edge(source="confirm_benchmark_t3", target="gate_accuracy_t3"),              # 34
        Edge(source="gate_accuracy_t3", target="gate_per_unit_accuracy",
             condition=VerdictType.PROCEED),                                         # 35
        Edge(source="gate_accuracy_t3", target="archive_result_t3",
             condition=VerdictType.HALT),                                            # 36
        Edge(source="gate_per_unit_accuracy", target="archive_result_t3",
             condition=VerdictType.PROCEED),                                         # 37
        Edge(source="gate_per_unit_accuracy", target="builder_implement_alternative",
             condition=VerdictType.RELOOP),                                          # 38
        Edge(source="gate_per_unit_accuracy", target="archive_result_t3",
             condition=VerdictType.HALT),                                            # 39
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
