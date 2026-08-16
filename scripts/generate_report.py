#!/usr/bin/env python3
"""Generate the outer loop v2 report from calibration and evolution data.

Supports both lv1 and lv2 calibration data. Reads whichever is available,
preferring lv2 (harder instances with actual variance).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUTER_LOOP_DIR = PROJECT_DIR / ".factory" / "outer_loop"
RESULTS_DIR = PROJECT_DIR / "results"


def main() -> None:
    # Read all available calibration data
    cal_lv1_path = OUTER_LOOP_DIR / "calibration.json"
    cal_lv1_v2_path = OUTER_LOOP_DIR / "calibration_v2.json"
    cal_lv2_path = OUTER_LOOP_DIR / "calibration_lv2.json"
    evo_path = OUTER_LOOP_DIR / "evolution_results.json"

    cal_lv1 = json.loads(cal_lv1_path.read_text()) if cal_lv1_path.exists() else {}
    cal_lv1_v2 = json.loads(cal_lv1_v2_path.read_text()) if cal_lv1_v2_path.exists() else {}
    cal_lv2 = json.loads(cal_lv2_path.read_text()) if cal_lv2_path.exists() else {}
    evolution = json.loads(evo_path.read_text()) if evo_path.exists() else {}

    # Use lv2 as primary calibration if available
    primary_cal = cal_lv2 if cal_lv2 else cal_lv1_v2 if cal_lv1_v2 else cal_lv1
    level = primary_cal.get("level", "lv1")
    seed_score = primary_cal.get("seed_score", 0.0)
    training = primary_cal.get("training", [])
    holdout = primary_cal.get("holdout", [])

    lines = [
        "# Outer Loop v2 — FeatureBench Evolution Report",
        "",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        "",
        "## 1. Key Finding: lv1 Instances Have Zero Variance",
        "",
        "Both the 4-node pipeline (researcher→builder→health_checker→gate) and the",
        "builder-only seed achieved **100% resolve rate** on all 10 lv1 instances.",
        "This means lv1 tasks are too easy for evolution — no room to improve.",
        "",
        "| Seed Type | lv1 Score | Instances Resolved |",
        "|-----------|-----------|-------------------|",
        f"| 4-node pipeline | {cal_lv1.get('seed_score', 'N/A')} | {cal_lv1.get('resolved_count', 'N/A')}/{cal_lv1.get('total', 'N/A')} |",
        f"| Builder-only | {cal_lv1_v2.get('seed_score', 'N/A')} | {cal_lv1_v2.get('resolved_count', 'N/A')}/{cal_lv1_v2.get('total', 'N/A')} |",
        "",
    ]

    # lv2 calibration results
    if cal_lv2:
        lines.extend([
            f"## 2. Calibration — Builder-Only on lv2 (Hard Instances)",
            "",
            f"- **Seed:** {cal_lv2.get('seed_name', 'builder-only')}",
            f"- **Level:** lv2 (multiple functions per task)",
            f"- **Seed score:** {cal_lv2.get('seed_score', 0):.0%}",
            f"- **Resolved:** {cal_lv2.get('resolved_count', 0)}/{cal_lv2.get('total', 0)}",
            f"- **Training set:** {len(training)} instances",
            f"- **Holdout set:** {len(holdout)} instances",
            f"- **Total elapsed:** {cal_lv2.get('total_elapsed_seconds', 0):.0f}s "
            f"({cal_lv2.get('total_elapsed_seconds', 0) / 60:.1f} min)",
            "",
            "### Per-Instance Results",
            "",
            "| Instance | Split | Score | Resolved | Time (s) |",
            "|----------|-------|-------|----------|----------|",
        ])
        instances = cal_lv2.get("instances", {})
        for iid, data in instances.items():
            if isinstance(data, dict):
                proj = iid.split("__")[1].split(".")[0] if "__" in iid else iid[:30]
                score = data.get("score", 0.0)
                resolved = "PASS" if data.get("resolved", False) else "FAIL"
                elapsed = data.get("elapsed_seconds", 0)
                split = "train" if iid in training else "holdout" if iid in holdout else "?"
                lines.append(f"| `{proj}` | {split} | {score:.2f} | {resolved} | {elapsed:.0f} |")
        lines.append("")

    # Evolution results
    if evolution:
        best_score = evolution.get("best_score", 0.0)
        holdout_score = evolution.get("holdout_score", 0.0)
        improvement = best_score - seed_score
        elapsed_s = evolution.get("elapsed_seconds", 0)

        lines.extend([
            f"## 3. Evolution Results",
            "",
            f"- **Seed type:** {evolution.get('seed_name', 'unknown')}",
            f"- **Best training score:** {best_score:.3f}",
            f"- **Holdout score:** {holdout_score:.3f}",
            f"- **Seed score:** {seed_score:.3f}",
            f"- **Improvement (train):** {improvement:+.3f}",
            f"- **Overfit flag:** {evolution.get('overfit_flag', False)}",
            f"- **Generations completed:** {evolution.get('generations_completed', 0)}",
            f"- **Total evaluations:** {evolution.get('total_evaluations', 0)}",
            f"- **Convergence reason:** {evolution.get('convergence_reason', 'N/A')}",
            f"- **Archive size:** {evolution.get('archive_size', 0)}",
            f"- **Elapsed:** {elapsed_s:.0f}s ({elapsed_s / 60:.1f} min)",
            "",
        ])

        # Score trajectory
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

        # Mutations
        if trajectory:
            lines.extend([
                "### Mutations Discovered",
                "",
                "| Generation | Operator | Target Node | Novel | Rejected Dupes |",
                "|------------|----------|-------------|-------|----------------|",
            ])
            for gen in trajectory:
                if isinstance(gen, dict):
                    mutations = gen.get("mutations_applied", [])
                    novel = gen.get("novel_count", 0)
                    rejected = gen.get("rejected_duplicates", 0)
                    for mut in mutations:
                        if isinstance(mut, dict):
                            lines.append(
                                f"| {gen.get('generation', '?')} "
                                f"| {mut.get('operator', '?')} "
                                f"| {mut.get('target_node', 'N/A')} "
                                f"| {novel} "
                                f"| {rejected} |"
                            )
                    if not mutations:
                        lines.append(
                            f"| {gen.get('generation', '?')} "
                            f"| (none) | - | {novel} | {rejected} |"
                        )
            lines.append("")

        # Pareto front
        pareto = evolution.get("pareto_front", [])
        if pareto:
            lines.extend([
                "### Pareto Front (Score vs Complexity)",
                "",
                "| ID | Score | Generation | Nodes | Parent |",
                "|----|-------|------------|-------|--------|",
            ])
            for ind in pareto:
                if isinstance(ind, dict):
                    wf_data = ind.get("workflow_data", {})
                    nodes = wf_data.get("nodes", {})
                    lines.append(
                        f"| `{ind.get('id', '?')[:12]}` "
                        f"| {ind.get('score', 0):.3f} "
                        f"| {ind.get('generation', '?')} "
                        f"| {len(nodes)} "
                        f"| `{(ind.get('parent_id') or '-')[:12]}` |"
                    )
            lines.append("")

        # Training vs holdout
        lines.extend([
            "## 4. Overfitting Analysis",
            "",
            "| Metric | Score |",
            "|--------|-------|",
            f"| Seed (calibration) | {seed_score:.3f} |",
            f"| Best training | {best_score:.3f} |",
            f"| Holdout | {holdout_score:.3f} |",
            f"| Train-Holdout delta | {best_score - holdout_score:+.3f} |",
            "",
        ])

        # Cost and time
        cost = evolution.get("total_cost_usd", 0.0)
        cal_time = primary_cal.get("total_elapsed_seconds", 0)
        lines.extend([
            "## 5. Cost and Time",
            "",
            f"- **Calibration time:** {cal_time:.0f}s ({cal_time / 60:.1f} min)",
            f"- **Evolution time:** {elapsed_s:.0f}s ({elapsed_s / 60:.1f} min)",
            f"- **Total time:** {cal_time + elapsed_s:.0f}s ({(cal_time + elapsed_s) / 60:.1f} min)",
            f"- **API cost (reported):** ${cost:.2f}",
            f"- **Total evaluations:** {evolution.get('total_evaluations', 0)}",
            "",
        ])

        # Summary
        lines.extend([
            "## 6. Summary",
            "",
            f"The builder-only seed achieved **{seed_score:.0%}** on {level} instances.",
            "",
            f"After {evolution.get('generations_completed', 0)} generation(s) of evolution "
            f"with {evolution.get('total_evaluations', 0)} total evaluations, "
            f"the best evolved workflow scored **{best_score:.3f}** on training "
            f"and **{holdout_score:.3f}** on holdout.",
            "",
            f"Improvement over seed: **{improvement:+.3f}**. "
            f"Overfit flag: **{evolution.get('overfit_flag', False)}**.",
            "",
            f"Convergence reason: **{evolution.get('convergence_reason', 'N/A')}**.",
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
