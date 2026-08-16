"""Check gate logic for LUMEN workflow."""

import json
import re
import sys
from pathlib import Path


def main():
    """Check if training should continue or halt."""
    # Read configuration and state
    cfg = json.load(open(".factory/lumen/current_run/config.json"))
    state = json.load(open(".factory/lumen/current_run/state.json"))

    task_name = cfg["task_name"]
    it = state["iteration"]
    max_iterations = cfg.get("max_iterations", 3)  # Default to 3 iterations

    # Read evaluation results
    results = json.load(
        open(f".factory/lumen/current_run/iteration_{it}/evaluation_results.json")
    )

    # Parse instruction.md for SOTA and scoring direction
    md = open(f"benchmarks/einsteinarena/{task_name}/instruction.md").read()
    sota_match = re.search(r"Current best score.*?([0-9.eE+-]+)", md)
    min_imp_match = re.search(r"Minimum improvement.*?([0-9.eE+-]+)", md)
    dir_match = re.search(r"Scoring Direction.*?(MAXIMIZE|MINIMIZE)", md)

    sota = float(sota_match.group(1)) if sota_match else None
    min_imp = float(min_imp_match.group(1)) if min_imp_match else 1e-10
    direction = dir_match.group(1) if dir_match else "MAXIMIZE"
    best = results["best_score"]

    # Decision logic
    if sota is None:
        print("pass: No SOTA yet, any valid score is success")
    else:
        success = (
            (best > sota + min_imp)
            if direction == "MAXIMIZE"
            else (best < sota - min_imp)
        )
        if success:
            print("pass: Score improved beyond SOTA")
        elif it >= max_iterations - 1:
            print(f"halt: Max iterations ({max_iterations}) reached without improvement")
        else:
            state["iteration"] = it + 1
            json.dump(state, open(".factory/lumen/current_run/state.json", "w"))
            print("reloop: Need more iterations")


if __name__ == "__main__":
    main()
