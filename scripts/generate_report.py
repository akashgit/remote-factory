#!/usr/bin/env python3
"""Generate the outer loop v2 report from calibration and evolution data."""

from __future__ import annotations

import json
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUTER_LOOP_DIR = PROJECT_DIR / ".factory" / "outer_loop"
RESULTS_DIR = PROJECT_DIR / "results"


def main() -> None:
    cal_path = OUTER_LOOP_DIR / "calibration.json"
    evo_path = OUTER_LOOP_DIR / "evolution_results.json"
    smoke_path = OUTER_LOOP_DIR / "smoke_test.json"

    calibration = json.loads(cal_path.read_text()) if cal_path.exists() else {}
    evolution = json.loads(evo_path.read_text()) if evo_path.exists() else {}
    smoke = json.loads(smoke_path.read_text()) if smoke_path.exists() else {}

    lines = [
        "# Outer Loop v2 — FeatureBench Evolution Report",
        "",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        "",
        "## Smoke Test",
        "",
        f"- **Instance:** `{smoke.get('instance', 'N/A')}`",
        f"- **Resolved:** {smoke.get('resolved', False)}",
        f"- **Score:** {smoke.get('score', 0.0)}",
        f"- **Elapsed:** {smoke.get('elapsed_seconds', 0)}s",
        "",
        "## Calibration (Seed Workflow Baseline)",
        "",
        f"- **Seed score:** {calibration.get('seed_score', 0.0):.2f}",
        f"- **Resolved:** {calibration.get('resolved_count', 0)} / {calibration.get('total', 0)}",
        f"- **Training set:** {len(calibration.get('training', []))} instances",
        f"- **Holdout set:** {len(calibration.get('holdout', []))} instances",
        f"- **Total elapsed:** {calibration.get('total_elapsed_seconds', 0)}s",
        "",
        "### Per-Instance Results",
        "",
        "| Instance | Score | Resolved | Time (s) |",
        "|----------|-------|----------|----------|",
    ]

    instances = calibration.get("instances", {})
    for iid, data in instances.items():
        if isinstance(data, dict):
            proj = iid.split("__")[1].split(".")[0] if "__" in iid else iid[:30]
            score = data.get("score", 0.0)
            resolved = "Yes" if data.get("resolved", False) else "No"
            elapsed = data.get("elapsed_seconds", 0)
            lines.append(f"| `{proj}` | {score:.2f} | {resolved} | {elapsed} |")

    if evolution:
        seed_score = calibration.get("seed_score", 0.0)
        best_score = evolution.get("best_score", 0.0)
        improvement = best_score - seed_score

        lines.extend([
            "",
            "## Evolution",
            "",
            f"- **Best score:** {best_score:.3f}",
            f"- **Holdout score:** {evolution.get('holdout_score', 0.0):.3f}",
            f"- **Overfit flag:** {evolution.get('overfit_flag', False)}",
            f"- **Generations:** {evolution.get('generations_completed', 0)}",
            f"- **Total evaluations:** {evolution.get('total_evaluations', 0)}",
            f"- **Convergence reason:** {evolution.get('convergence_reason', 'N/A')}",
            f"- **Total elapsed:** {evolution.get('elapsed_seconds', 0)}s",
            "",
        ])

        trajectory = evolution.get("trajectory", [])
        if trajectory:
            lines.extend([
                "### Score Trajectory",
                "",
                "| Generation | Best Score | Mean Score | Diversity | Holdout |",
                "|------------|-----------|------------|-----------|---------|",
            ])
            for gen in trajectory:
                if isinstance(gen, dict):
                    lines.append(
                        f"| {gen.get('generation', '?')} "
                        f"| {gen.get('best_score', 0):.3f} "
                        f"| {gen.get('mean_score', 0):.3f} "
                        f"| {gen.get('diversity', 0):.3f} "
                        f"| {gen.get('holdout_score', 0):.3f} |"
                    )
            lines.append("")

        pareto = evolution.get("pareto_front", [])
        if pareto:
            lines.extend([
                "### Pareto Front",
                "",
                "| ID | Score | Generation | Nodes |",
                "|----|-------|------------|-------|",
            ])
            for ind in pareto:
                if isinstance(ind, dict):
                    wf_data = ind.get("workflow_data", {})
                    nodes = wf_data.get("nodes", {})
                    lines.append(
                        f"| `{ind.get('id', '?')[:12]}` "
                        f"| {ind.get('score', 0):.3f} "
                        f"| {ind.get('generation', '?')} "
                        f"| {len(nodes)} |"
                    )
            lines.append("")

        lines.extend([
            "## Summary",
            "",
            f"- **Seed score:** {seed_score:.3f}",
            f"- **Evolved score:** {best_score:.3f}",
            f"- **Improvement:** {improvement:+.3f}",
            f"- **Archive size:** {evolution.get('archive_size', 0)}",
            "",
        ])
    else:
        lines.extend(["", "## Evolution", "", "_Not yet run._", ""])

    report_text = "\n".join(lines)
    report_path = RESULTS_DIR / "outer_loop_v2_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text)
    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
