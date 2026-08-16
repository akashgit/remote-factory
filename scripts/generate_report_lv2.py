#!/usr/bin/env python3
"""Generate the outer loop lv2 report from calibration and evolution data."""

from __future__ import annotations

import json
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUTER_LOOP_DIR = PROJECT_DIR / ".factory" / "outer_loop"
RESULTS_DIR = PROJECT_DIR / "results"


def main() -> None:
    cal_path = OUTER_LOOP_DIR / "calibration_lv2.json"
    evo_path = OUTER_LOOP_DIR / "evolution_results_lv2.json"

    calibration = json.loads(cal_path.read_text()) if cal_path.exists() else {}
    evolution = json.loads(evo_path.read_text()) if evo_path.exists() else {}

    seed_score = calibration.get("seed_score", 0.0)
    training = calibration.get("training", [])
    holdout = calibration.get("holdout", [])

    lines = [
        "# Outer Loop v2 — lv2 FeatureBench Evolution Report",
        "",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        f"**Level:** lv2 (from-scratch implementation — harder than lv1)",
        "",
        "## 1. Calibration (Builder-Only Seed Baseline)",
        "",
        f"- **Seed score:** {seed_score:.2%}",
        f"- **Resolved:** {calibration.get('resolved_count', 0)} / {calibration.get('total', 0)}",
        f"- **Total elapsed:** {calibration.get('total_elapsed_seconds', 0):.0f}s "
        f"({calibration.get('total_elapsed_seconds', 0) / 60:.1f} min)",
        "",
        "### Per-Instance Calibration Results",
        "",
        "| Instance | Split | Result | Time (s) |",
        "|----------|-------|--------|----------|",
    ]

    instances = calibration.get("instances", {})
    for iid, data in instances.items():
        if isinstance(data, dict):
            proj = iid.split("__")[1].split(".")[0] if "__" in iid else iid[:30]
            resolved = "PASS" if data.get("resolved", False) else "FAIL"
            elapsed = data.get("elapsed_seconds", 0)
            split = "train" if iid in training else "holdout" if iid in holdout else "?"
            lines.append(f"| `{proj}` | {split} | {resolved} | {elapsed:.0f} |")

    if evolution:
        best_score = evolution.get("best_score", 0.0)
        holdout_score = evolution.get("holdout_score", 0.0)
        improvement = best_score - seed_score
        elapsed_s = evolution.get("elapsed_seconds", 0)

        lines.extend([
            "",
            "## 2. Evolution Results",
            "",
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

        if trajectory:
            lines.extend([
                "### Mutations Applied",
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

        pareto = evolution.get("pareto_front", [])
        if pareto:
            lines.extend([
                "### Pareto Front",
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

        lines.extend([
            "## 3. Training vs Holdout Comparison",
            "",
            "| Metric | Score |",
            "|--------|-------|",
            f"| Seed (calibration) | {seed_score:.3f} |",
            f"| Best training | {best_score:.3f} |",
            f"| Holdout | {holdout_score:.3f} |",
            f"| Train-Holdout delta | {best_score - holdout_score:+.3f} |",
            "",
        ])

        # Per-instance before/after comparison
        best_wf = evolution.get("pareto_front", [{}])
        best_ind = best_wf[0] if best_wf else {}
        best_instance_results = best_ind.get("instance_results", {})
        if best_instance_results:
            lines.extend([
                "### Per-Instance: Seed vs Best Evolved",
                "",
                "| Instance | Seed | Evolved | Change |",
                "|----------|------|---------|--------|",
            ])
            all_iids = set(instances.keys()) | set(best_instance_results.keys())
            for iid in sorted(all_iids):
                proj = iid.split("__")[1].split(".")[0] if "__" in iid else iid[:30]
                seed_res = "PASS" if instances.get(iid, {}).get("resolved", False) else "FAIL"
                evo_res = "PASS" if best_instance_results.get(iid, False) else "FAIL"
                change = "=" if seed_res == evo_res else ("+" if evo_res == "PASS" else "-")
                lines.append(f"| `{proj}` | {seed_res} | {evo_res} | {change} |")
            lines.append("")

        cost = evolution.get("total_cost_usd", 0.0)
        cal_time = calibration.get("total_elapsed_seconds", 0)
        lines.extend([
            "## 4. Cost and Time",
            "",
            f"- **Calibration time:** {cal_time:.0f}s ({cal_time / 60:.1f} min)",
            f"- **Evolution time:** {elapsed_s:.0f}s ({elapsed_s / 60:.1f} min)",
            f"- **Total time:** {cal_time + elapsed_s:.0f}s "
            f"({(cal_time + elapsed_s) / 60:.1f} min)",
            f"- **API cost (reported):** ${cost:.2f}",
            f"- **Total evaluations:** {evolution.get('total_evaluations', 0)}",
            "",
        ])

        lines.extend([
            "## 5. Summary",
            "",
            f"The builder-only seed achieved **{seed_score:.0%}** resolve rate on "
            f"{calibration.get('total', 0)} lv2 instances "
            f"({calibration.get('resolved_count', 0)}/{calibration.get('total', 0)} resolved).",
            "",
            "lv2 instances are significantly harder than lv1: the agent starts from an "
            "empty testbed and must create all code from scratch (vs patching existing code "
            "in lv1).",
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
    report_path = RESULTS_DIR / "outer_loop_lv2_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text)
    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
