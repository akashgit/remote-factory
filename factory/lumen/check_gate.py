"""Check gate logic for LUMEN workflow."""

import json
import re
import sys
from pathlib import Path


def main():
    """Check if training should continue or halt."""
    import argparse
    parser = argparse.ArgumentParser(description="LUMEN check gate")
    parser.add_argument("--run-dir", default=None, help="Run directory path")
    args = parser.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else Path(".factory/lumen/.running")
    cfg = json.load(open(run_dir / "config.json"))
    state = json.load(open(run_dir / "state.json"))

    task_name = cfg["task_name"]
    current_it = state["iteration"]
    max_iterations = cfg.get("max_iterations", 3)

    # Find global best across all iterations
    task_dir = cfg.get("task_dir", f"benchmarks/einsteinarena/{task_name}")
    md = open(f"{task_dir}/instruction.md").read()
    dir_match = re.search(r"Scoring Direction.*?(MAXIMIZE|MINIMIZE)", md, re.DOTALL)
    direction = dir_match.group(1) if dir_match else "MAXIMIZE"

    global_best_score = None
    global_best_solution = {}
    global_best_iteration = None

    for it in range(current_it + 1):
        eval_file = run_dir / f"iteration_{it}" / "evaluation_results.json"
        if not eval_file.exists():
            continue

        results = json.load(open(eval_file))
        # Read from overall stats (aggregates sm + fm rollouts)
        overall = results.get("overall", results)  # Fallback to old format if no "overall" key
        score = overall["best_score"]
        solution = overall.get("best_solution", {})

        if global_best_score is None:
            global_best_score = score
            global_best_solution = solution
            global_best_iteration = it
        else:
            is_better = (score > global_best_score) if direction == "MAXIMIZE" else (score < global_best_score)
            if is_better:
                global_best_score = score
                global_best_solution = solution
                global_best_iteration = it

    # Update state with global best
    state["best_score"] = global_best_score
    state["best_iteration"] = global_best_iteration
    state["best_solution"] = global_best_solution

    # Parse SOTA thresholds
    sota_match = re.search(r"Current best score.*?(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", md)
    min_imp_match = re.search(r"Minimum improvement.*?(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", md)

    sota = float(sota_match.group(1)) if sota_match else None
    min_imp = float(min_imp_match.group(1)) if min_imp_match else 1e-10
    best = global_best_score
    best_solution = global_best_solution

    verdict = {
        "best_score": best,
        "best_solution": best_solution,
        "best_iteration": global_best_iteration,
        "current_iteration": current_it,
        "sota": sota,
        "direction": direction,
    }

    if sota is None:
        verdict["outcome"] = "no_sota"
        print("pass: No SOTA yet, any valid score is success")
    else:
        success = (
            (best > sota + min_imp)
            if direction == "MAXIMIZE"
            else (best < sota - min_imp)
        )
        if success:
            verdict["outcome"] = "sota_beaten"
            print("pass: Score improved beyond SOTA")
        elif current_it >= max_iterations - 1:
            verdict["outcome"] = "max_iterations"
            print("pass: Max iterations reached without improvement")
        else:
            verdict["outcome"] = "reloop"
            state["iteration"] = current_it + 1
            print(f"reloop({max_iterations}): Need more iterations")

    # Write updated state (with global best_score and best_iteration)
    json.dump(state, open(run_dir / "state.json", "w"))
    json.dump(verdict, open(run_dir / "verdict.json", "w"), indent=2)


if __name__ == "__main__":
    main()
