"""Cross-experiment comparison and single-experiment impact analysis."""

from __future__ import annotations

import json

import structlog

from factory.models import ExperimentRecord
from factory.store import ExperimentStore
from factory.strategy import categorize_hypothesis

log = structlog.get_logger()


# ── helpers ─────────────────────────────────────────────────────


def _load_experiment_record(store: ExperimentStore, exp_id: int) -> ExperimentRecord | None:
    """Find an experiment record by ID from results.tsv history."""
    import asyncio

    records = asyncio.run(store.load_history())
    for r in records:
        if r.id == exp_id:
            return r
    return None


def _load_eval_json(store: ExperimentStore, exp_id: int, phase: str) -> dict | None:
    """Load eval_before.json or eval_after.json for an experiment, or None if missing."""
    eval_path = store.factory_dir / "experiments" / f"{exp_id:03d}" / f"eval_{phase}.json"
    if not eval_path.exists():
        log.debug("eval_json_not_found", exp_id=exp_id, phase=phase, path=str(eval_path))
        return None
    return json.loads(eval_path.read_text())


def _load_hypothesis_text(store: ExperimentStore, exp_id: int) -> str | None:
    """Load the hypothesis.md text for an experiment."""
    hyp_path = store.factory_dir / "experiments" / f"{exp_id:03d}" / "hypothesis.md"
    if not hyp_path.exists():
        return None
    return hyp_path.read_text()


# ── public API ──────────────────────────────────────────────────


def dimension_diff(eval_before: dict, eval_after: dict) -> list[dict]:
    """Compare two eval result dicts and return per-dimension score changes.

    Each eval dict is expected to have a ``results`` key containing a list
    of ``{"name": ..., "score": ..., ...}`` entries (the ``EvalResult`` shape).

    Returns a list of dicts with keys: name, before, after, delta.
    """
    before_map: dict[str, float] = {}
    for r in eval_before.get("results", []):
        before_map[r["name"]] = r["score"]

    after_map: dict[str, float] = {}
    for r in eval_after.get("results", []):
        after_map[r["name"]] = r["score"]

    all_dims = sorted(set(before_map) | set(after_map))
    diffs: list[dict] = []
    for name in all_dims:
        b = before_map.get(name, 0.0)
        a = after_map.get(name, 0.0)
        diffs.append({
            "name": name,
            "before": b,
            "after": a,
            "delta": round(a - b, 6),
        })

    log.debug("dimension_diff", dimensions=len(diffs))
    return diffs


def compare_experiments(
    store: ExperimentStore,
    id_a: int,
    id_b: int,
) -> dict:
    """Compare two experiments side-by-side.

    Returns a dict with keys: experiment_a, experiment_b, dimension_diffs.
    Each experiment entry contains: id, hypothesis, verdict, score_before,
    score_after, delta, feec_category.
    """
    log.info("compare_experiments", id_a=id_a, id_b=id_b)

    record_a = _load_experiment_record(store, id_a)
    record_b = _load_experiment_record(store, id_b)

    if record_a is None:
        raise ValueError(f"Experiment {id_a} not found in results.tsv")
    if record_b is None:
        raise ValueError(f"Experiment {id_b} not found in results.tsv")

    def _experiment_summary(record: ExperimentRecord) -> dict:
        cat = categorize_hypothesis(record.hypothesis)
        return {
            "id": record.id,
            "hypothesis": record.hypothesis,
            "verdict": record.verdict,
            "score_before": record.score_before,
            "score_after": record.score_after,
            "delta": record.delta,
            "feec_category": cat.name,
        }

    result: dict = {
        "experiment_a": _experiment_summary(record_a),
        "experiment_b": _experiment_summary(record_b),
        "dimension_diffs": None,
    }

    # Try to compute dimension-level diffs between the two experiments'
    # eval_after results (most useful comparison)
    eval_a = _load_eval_json(store, id_a, "after")
    eval_b = _load_eval_json(store, id_b, "after")

    if eval_a and eval_b:
        result["dimension_diffs"] = dimension_diff(eval_a, eval_b)

    return result


def explain_experiment(
    store: ExperimentStore,
    exp_id: int,
) -> dict:
    """Structured analysis of a single experiment.

    Returns a dict with keys: id, hypothesis, hypothesis_full, verdict,
    score_before, score_after, delta, feec_category, dimension_breakdown.
    """
    log.info("explain_experiment", exp_id=exp_id)

    record = _load_experiment_record(store, exp_id)
    if record is None:
        raise ValueError(f"Experiment {exp_id} not found in results.tsv")

    cat = categorize_hypothesis(record.hypothesis)
    full_text = _load_hypothesis_text(store, exp_id)

    result: dict = {
        "id": record.id,
        "hypothesis": record.hypothesis,
        "hypothesis_full": full_text,
        "verdict": record.verdict,
        "score_before": record.score_before,
        "score_after": record.score_after,
        "delta": record.delta,
        "feec_category": cat.name,
        "dimension_breakdown": None,
    }

    # If both before/after evals exist, compute dimension breakdown
    eval_before = _load_eval_json(store, exp_id, "before")
    eval_after = _load_eval_json(store, exp_id, "after")

    if eval_before and eval_after:
        result["dimension_breakdown"] = dimension_diff(eval_before, eval_after)

    return result


# ── formatters ──────────────────────────────────────────────────


def format_comparison(comparison: dict) -> str:
    """Format a comparison dict as a human-readable string."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("Experiment Comparison")
    lines.append("=" * 60)

    for label, key in [("A", "experiment_a"), ("B", "experiment_b")]:
        exp = comparison[key]
        lines.append("")
        lines.append(f"  [{label}] Experiment #{exp['id']}")
        lines.append(f"      Hypothesis: {exp['hypothesis']}")
        lines.append(f"      FEEC:       {exp['feec_category']}")
        lines.append(f"      Verdict:    {exp['verdict']}")
        if exp["score_before"] is not None:
            lines.append(f"      Score:      {exp['score_before']:.4f} → {exp['score_after']:.4f} (Δ {exp['delta']:+.4f})")
        else:
            lines.append("      Score:      n/a")

    diffs = comparison.get("dimension_diffs")
    if diffs:
        lines.append("")
        lines.append("  Dimension Diffs (A eval_after → B eval_after):")
        lines.append(f"    {'Dimension':<30} {'A':>8} {'B':>8} {'Δ':>8}")
        lines.append(f"    {'-' * 54}")
        for d in diffs:
            delta_str = f"{d['delta']:+.4f}" if d["delta"] != 0 else "  0.0000"
            lines.append(f"    {d['name']:<30} {d['before']:>8.4f} {d['after']:>8.4f} {delta_str:>8}")

    lines.append("")
    return "\n".join(lines)


def format_explanation(explanation: dict) -> str:
    """Format an explanation dict as a human-readable string."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append(f"Experiment #{explanation['id']} — Explanation")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"  Hypothesis:     {explanation['hypothesis']}")
    lines.append(f"  FEEC Category:  {explanation['feec_category']}")
    lines.append(f"  Verdict:        {explanation['verdict']}")

    if explanation["score_before"] is not None:
        lines.append(
            f"  Score:          {explanation['score_before']:.4f} → "
            f"{explanation['score_after']:.4f} (Δ {explanation['delta']:+.4f})"
        )
    else:
        lines.append("  Score:          n/a")

    if explanation["hypothesis_full"] and explanation["hypothesis_full"] != explanation["hypothesis"]:
        lines.append("")
        lines.append("  Full Hypothesis:")
        for line in explanation["hypothesis_full"].splitlines():
            lines.append(f"    {line}")

    breakdown = explanation.get("dimension_breakdown")
    if breakdown:
        lines.append("")
        lines.append("  Dimension Breakdown:")
        lines.append(f"    {'Dimension':<30} {'Before':>8} {'After':>8} {'Δ':>8}")
        lines.append(f"    {'-' * 54}")
        for d in breakdown:
            delta_str = f"{d['delta']:+.4f}" if d["delta"] != 0 else "  0.0000"
            lines.append(f"    {d['name']:<30} {d['before']:>8.4f} {d['after']:>8.4f} {delta_str:>8}")

    lines.append("")
    return "\n".join(lines)
