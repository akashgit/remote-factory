"""Statistical analysis for the statefulness eval benchmark.

Reads per-iteration JSON results from .factory/experiments/statefulness/
and produces:
- analysis-report.md (human-readable markdown)
- analysis-stats.json (machine-readable statistics)

Statistical methods:
- Cohen's d effect size per metric
- Bootstrap 95% CI for mean difference (10,000 resamples)
- Wilcoxon signed-rank test per project (paired by iteration)
- Descriptive stats (median, mean, stddev, range)
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import stats

RESULTS_DIR = Path(".factory/experiments/statefulness")

METRICS = [
    "factory_read_count",
    "factory_files_read_count",
    "agent_reinvocations",
    "time_to_first_meaningful_action_s",
    "total_tool_calls",
]

METRIC_LABELS = {
    "factory_read_count": ".factory/ Read Count",
    "factory_files_read_count": "Unique .factory/ Files Read",
    "agent_reinvocations": "Agent Re-invocations",
    "time_to_first_meaningful_action_s": "Time to First Meaningful Action (s)",
    "total_tool_calls": "Total Tool Calls",
}


@dataclass
class IterationResult:
    project: str
    condition: str
    iteration: int
    exit_code: int
    duration_s: float
    metrics: dict


def load_results(results_dir: Path) -> list[IterationResult]:
    """Load all per-iteration JSON files from the results directory."""
    results = []
    for json_path in sorted(results_dir.rglob("iter-*.json")):
        data = json.loads(json_path.read_text())
        metrics = data.get("metrics", {})
        if "factory_files_read" in metrics:
            metrics["factory_files_read_count"] = len(metrics["factory_files_read"])
        results.append(
            IterationResult(
                project=data["project"],
                condition=data["condition"],
                iteration=data["iteration"],
                exit_code=data["exit_code"],
                duration_s=data["duration_s"],
                metrics=metrics,
            )
        )
    return results


def _get_values(results: list[IterationResult], condition: str, metric: str) -> list[float]:
    """Extract metric values for a given condition."""
    values = []
    for r in results:
        if r.condition == condition:
            val = r.metrics.get(metric)
            if val is not None:
                values.append(float(val))
    return values


def _get_paired_values(
    results: list[IterationResult],
    project: str,
    metric: str,
) -> tuple[list[float], list[float]]:
    """Get paired control/treatment values for a project, matched by iteration."""
    control_by_iter = {}
    treatment_by_iter = {}
    for r in results:
        if r.project != project:
            continue
        val = r.metrics.get(metric)
        if val is None:
            continue
        if r.condition == "control":
            control_by_iter[r.iteration] = float(val)
        else:
            treatment_by_iter[r.iteration] = float(val)

    shared_iters = sorted(set(control_by_iter) & set(treatment_by_iter))
    control = [control_by_iter[i] for i in shared_iters]
    treatment = [treatment_by_iter[i] for i in shared_iters]
    return control, treatment


def cohens_d(control: list[float], treatment: list[float]) -> float | None:
    """Compute Cohen's d effect size (treatment - control)."""
    if len(control) < 2 or len(treatment) < 2:
        return None
    c = np.array(control)
    t = np.array(treatment)
    pooled_std = np.sqrt(
        ((len(c) - 1) * np.var(c, ddof=1) + (len(t) - 1) * np.var(t, ddof=1))
        / (len(c) + len(t) - 2)
    )
    if pooled_std == 0:
        return 0.0
    return float((np.mean(t) - np.mean(c)) / pooled_std)


def bootstrap_ci(
    control: list[float],
    treatment: list[float],
    n_resamples: int = 10_000,
    confidence_level: float = 0.95,
) -> tuple[float, float] | None:
    """Compute bootstrap 95% CI for mean difference (treatment - control)."""
    if len(control) < 2 or len(treatment) < 2:
        return None
    c = np.array(control)
    t = np.array(treatment)

    def mean_diff(x: np.ndarray, y: np.ndarray) -> float:
        return float(np.mean(y) - np.mean(x))

    result = stats.bootstrap(
        (c, t),
        statistic=mean_diff,
        n_resamples=n_resamples,
        confidence_level=confidence_level,
        method="percentile",
        paired=False,
    )
    return (float(result.confidence_interval.low), float(result.confidence_interval.high))


def wilcoxon_test(control: list[float], treatment: list[float]) -> tuple[float, float] | None:
    """Run Wilcoxon signed-rank test on paired samples."""
    if len(control) < 5 or len(treatment) < 5:
        return None
    if len(control) != len(treatment):
        return None
    diffs = [t - c for c, t in zip(control, treatment)]
    if all(d == 0 for d in diffs):
        return None
    try:
        result = stats.wilcoxon(diffs, alternative="two-sided")
        return (float(result.statistic), float(result.pvalue))
    except ValueError:
        return None


def descriptive_stats(values: list[float]) -> dict:
    """Compute descriptive statistics for a list of values."""
    if not values:
        return {"n": 0, "median": None, "mean": None, "stddev": None, "min": None, "max": None}
    arr = np.array(values)
    return {
        "n": len(values),
        "median": float(np.median(arr)),
        "mean": float(np.mean(arr)),
        "stddev": float(np.std(arr, ddof=1)) if len(values) > 1 else 0.0,
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
    }


def _effect_size_label(d: float | None) -> str:
    if d is None:
        return "insufficient data"
    abs_d = abs(d)
    if abs_d < 0.2:
        return "negligible"
    elif abs_d < 0.5:
        return "small"
    elif abs_d < 0.8:
        return "medium"
    else:
        return "large"


def analyze(results_dir: Path) -> dict:
    """Run full statistical analysis and return results dict."""
    results = load_results(results_dir)
    if not results:
        print(f"No results found in {results_dir}", file=sys.stderr)
        return {}

    projects = sorted({r.project for r in results})
    analysis: dict = {"projects": projects, "metrics": {}, "per_project": {}}

    for metric in METRICS:
        control_vals = _get_values(results, "control", metric)
        treatment_vals = _get_values(results, "treatment", metric)

        d = cohens_d(control_vals, treatment_vals)
        ci = bootstrap_ci(control_vals, treatment_vals)
        w_test = None
        if len(projects) == 1:
            c_paired, t_paired = _get_paired_values(results, projects[0], metric)
            w_test = wilcoxon_test(c_paired, t_paired)

        analysis["metrics"][metric] = {
            "label": METRIC_LABELS.get(metric, metric),
            "control": descriptive_stats(control_vals),
            "treatment": descriptive_stats(treatment_vals),
            "cohens_d": d,
            "effect_size": _effect_size_label(d),
            "bootstrap_ci_95": {"low": ci[0], "high": ci[1]} if ci else None,
            "wilcoxon": {"statistic": w_test[0], "p_value": w_test[1]} if w_test else None,
        }

    for project in projects:
        project_results = [r for r in results if r.project == project]
        per_project: dict = {}
        for metric in METRICS:
            c_vals = _get_values(project_results, "control", metric)
            t_vals = _get_values(project_results, "treatment", metric)
            c_paired, t_paired = _get_paired_values(results, project, metric)
            w = wilcoxon_test(c_paired, t_paired)
            per_project[metric] = {
                "control": descriptive_stats(c_vals),
                "treatment": descriptive_stats(t_vals),
                "cohens_d": cohens_d(c_vals, t_vals),
                "wilcoxon": {"statistic": w[0], "p_value": w[1]} if w else None,
            }
        analysis["per_project"][project] = per_project

    return analysis


def generate_report(analysis: dict) -> str:
    """Generate a markdown analysis report."""
    lines = ["# Statefulness Eval — Analysis Report\n"]

    lines.append("## Overall Results\n")
    lines.append(
        "| Metric | Control (mean ± sd) | Treatment (mean ± sd) | Cohen's d | Effect | 95% CI |"
    )
    lines.append(
        "|--------|--------------------|-----------------------|-----------|--------|--------|"
    )

    for metric in METRICS:
        data = analysis["metrics"].get(metric, {})
        label = data.get("label", metric)
        c = data.get("control", {})
        t = data.get("treatment", {})

        c_str = f"{c.get('mean', 0):.1f} ± {c.get('stddev', 0):.1f}" if c.get("n", 0) > 0 else "—"
        t_str = f"{t.get('mean', 0):.1f} ± {t.get('stddev', 0):.1f}" if t.get("n", 0) > 0 else "—"
        d = data.get("cohens_d")
        d_str = f"{d:.3f}" if d is not None else "—"
        effect = data.get("effect_size", "—")
        ci = data.get("bootstrap_ci_95")
        ci_str = f"[{ci['low']:.2f}, {ci['high']:.2f}]" if ci else "—"

        lines.append(f"| {label} | {c_str} | {t_str} | {d_str} | {effect} | {ci_str} |")

    lines.append("\n## Per-Project Wilcoxon Signed-Rank Tests\n")
    for project in analysis.get("projects", []):
        lines.append(f"### {project}\n")
        pp = analysis.get("per_project", {}).get(project, {})
        lines.append("| Metric | W statistic | p-value | Significant (α=0.05) |")
        lines.append("|--------|------------|---------|---------------------|")
        for metric in METRICS:
            label = METRIC_LABELS.get(metric, metric)
            w = pp.get(metric, {}).get("wilcoxon")
            if w:
                sig = "Yes" if w["p_value"] < 0.05 else "No"
                lines.append(f"| {label} | {w['statistic']:.1f} | {w['p_value']:.4f} | {sig} |")
            else:
                lines.append(f"| {label} | — | — | insufficient data |")

    lines.append("\n## Interpretation Guide\n")
    lines.append("- **Cohen's d**: < 0.2 negligible, 0.2–0.5 small, 0.5–0.8 medium, > 0.8 large")
    lines.append(
        "- **Bootstrap CI**: 95% confidence interval for mean difference (treatment − control)"
    )
    lines.append(
        "- **Wilcoxon**: non-parametric paired test; p < 0.05 suggests significant difference"
    )
    lines.append(
        "- Positive Cohen's d means treatment > control (more of that metric with statefulness)"
    )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    """CLI entry point."""
    results_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else RESULTS_DIR

    if not results_dir.exists():
        print(f"Results directory not found: {results_dir}", file=sys.stderr)
        print(
            "Run the benchmark first: pytest benchmarks/statefulness-eval/ -m slow", file=sys.stderr
        )
        sys.exit(1)

    analysis = analyze(results_dir)
    if not analysis:
        sys.exit(1)

    report = generate_report(analysis)
    report_path = results_dir / "analysis-report.md"
    report_path.write_text(report)
    print(f"Report written to {report_path}")

    stats_path = results_dir / "analysis-stats.json"
    stats_path.write_text(json.dumps(analysis, indent=2, default=str) + "\n")
    print(f"Stats written to {stats_path}")


if __name__ == "__main__":
    main()
