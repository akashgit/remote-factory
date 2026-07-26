#!/usr/bin/env python3
"""CLI runner for spike-sort stages.

This script is designed to be called from a shell command in the workflow executor,
allowing the stages to run in a different Python environment (e.g., one with
dartsort/spikeinterface installed).

Usage:
    python spike_sort_runner.py <stage> --project-path /path/to/project --output-dir /path/to/output
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add ds_ref to path
import os
_DS_REF = os.environ.get("DS_REF_PATH", "/workspace/home/churwitz/ds_ref")
if _DS_REF not in sys.path:
    sys.path.insert(0, _DS_REF)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run spike-sort stages")
    parser.add_argument(
        "stage",
        choices=[
            "preprocess", "detect_trial", "detect", "localize", "cluster",
            "compute_cluster_metrics", "apply_cluster_actions",
            "templates", "compute_template_metrics", "apply_template_actions",
            "match", "compute_final_metrics", "apply_final_actions",
        ],
        help="Stage name to execute",
    )
    parser.add_argument("--project-path", required=True, help="Path to the project")
    parser.add_argument("--output-dir", required=True, help="Path to output directory")
    args = parser.parse_args()

    from factory.workflow.spike_sort_stages import (
        preprocess,
        detect_trial,
        detect,
        localize,
        cluster,
        compute_cluster_metrics,
        apply_cluster_actions,
        compute_templates,
        compute_template_metrics,
        apply_template_actions,
        template_match,
        compute_final_metrics,
        apply_final_actions,
    )

    stage_map = {
        "preprocess": preprocess,
        "detect_trial": detect_trial,
        "detect": detect,
        "localize": localize,
        "cluster": cluster,
        "compute_cluster_metrics": compute_cluster_metrics,
        "apply_cluster_actions": apply_cluster_actions,
        "templates": compute_templates,
        "compute_template_metrics": compute_template_metrics,
        "apply_template_actions": apply_template_actions,
        "match": template_match,
        "compute_final_metrics": compute_final_metrics,
        "apply_final_actions": apply_final_actions,
    }

    func = stage_map[args.stage]
    try:
        func(project_path=args.project_path, output_dir=args.output_dir)
        print(f"{args.stage} completed successfully")
        return 0
    except Exception as exc:
        print(f"Error in {args.stage}: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
