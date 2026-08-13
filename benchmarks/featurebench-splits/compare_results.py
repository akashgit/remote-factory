#!/usr/bin/env python3
"""Compare two FeatureBench eval runs and produce a per-task diff + aggregate stats.

Usage:
    python compare_results.py --baseline runs/baseline/ --factory runs/factory/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_results(results_dir: Path) -> dict[str, dict]:
    """Load fb eval results from a directory, keyed by instance_id."""
    tasks: dict[str, dict] = {}
    for f in sorted(results_dir.glob("*-featurebench-full.json")):
        data = json.loads(f.read_text())
        for task in data.get("tasks", []):
            iid = task.get("instance_id", "")
            if iid:
                tasks[iid] = task
    if not tasks:
        for f in sorted(results_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(data, dict) and "tasks" in data:
                for task in data["tasks"]:
                    iid = task.get("instance_id", "")
                    if iid:
                        tasks[iid] = task
    return tasks


def compare(baseline_dir: Path, factory_dir: Path) -> dict:
    baseline = _load_results(baseline_dir)
    factory = _load_results(factory_dir)

    all_ids = sorted(set(baseline) | set(factory))
    if not all_ids:
        print("No tasks found in either directory.", file=sys.stderr)
        return {"error": "no tasks found"}

    rows: list[dict] = []
    baseline_resolved = 0
    factory_resolved = 0
    only_baseline: list[str] = []
    only_factory: list[str] = []

    for iid in all_ids:
        b = baseline.get(iid, {})
        f = factory.get(iid, {})
        b_res = b.get("resolved", False)
        f_res = f.get("resolved", False)
        b_f2p = b.get("score", 0.0)
        f_f2p = f.get("score", 0.0)
        b_time = b.get("time", 0.0)
        f_time = f.get("time", 0.0)

        row = {
            "instance_id": iid,
            "baseline_resolved": b_res,
            "factory_resolved": f_res,
            "baseline_f2p": b_f2p,
            "factory_f2p": f_f2p,
            "baseline_time": b_time,
            "factory_time": f_time,
        }
        rows.append(row)

        if b_res:
            baseline_resolved += 1
        if f_res:
            factory_resolved += 1
        if b_res and not f_res:
            only_baseline.append(iid)
        if f_res and not b_res:
            only_factory.append(iid)

    total = len(all_ids)
    b_f2p_vals = [r["baseline_f2p"] for r in rows if r["baseline_f2p"] > 0]
    f_f2p_vals = [r["factory_f2p"] for r in rows if r["factory_f2p"] > 0]
    b_time_vals = [r["baseline_time"] for r in rows if r["baseline_time"] > 0]
    f_time_vals = [r["factory_time"] for r in rows if r["factory_time"] > 0]

    result = {
        "total_tasks": total,
        "baseline_resolve_rate": baseline_resolved / total if total else 0,
        "factory_resolve_rate": factory_resolved / total if total else 0,
        "baseline_resolved": baseline_resolved,
        "factory_resolved": factory_resolved,
        "mean_baseline_f2p": sum(b_f2p_vals) / len(b_f2p_vals) if b_f2p_vals else 0,
        "mean_factory_f2p": sum(f_f2p_vals) / len(f_f2p_vals) if f_f2p_vals else 0,
        "mean_baseline_time": sum(b_time_vals) / len(b_time_vals) if b_time_vals else 0,
        "mean_factory_time": sum(f_time_vals) / len(f_time_vals) if f_time_vals else 0,
        "only_baseline_solved": only_baseline,
        "only_factory_solved": only_factory,
        "per_task": rows,
    }

    _print_table(result)
    return result


def _print_table(result: dict) -> None:
    print("=" * 100)
    print("FeatureBench Comparison: Baseline vs Factory")
    print("=" * 100)

    header = f"{'Instance ID':<55} {'B-Res':>5} {'F-Res':>5} {'B-F2P':>6} {'F-F2P':>6}"
    print(header)
    print("-" * 100)

    for row in result["per_task"]:
        b_res = "Y" if row["baseline_resolved"] else "N"
        f_res = "Y" if row["factory_resolved"] else "N"
        print(
            f"{row['instance_id']:<55} {b_res:>5} {f_res:>5} "
            f"{row['baseline_f2p']:>6.2f} {row['factory_f2p']:>6.2f}"
        )

    print("-" * 100)
    total = result["total_tasks"]
    print(f"\n{'Aggregate Statistics':^100}")
    print("-" * 100)
    print(f"  Total tasks:           {total}")
    print(f"  Baseline resolve rate: {result['baseline_resolved']}/{total}"
          f" ({result['baseline_resolve_rate']:.1%})")
    print(f"  Factory resolve rate:  {result['factory_resolved']}/{total}"
          f" ({result['factory_resolve_rate']:.1%})")
    print(f"  Mean baseline F2P:     {result['mean_baseline_f2p']:.3f}")
    print(f"  Mean factory F2P:      {result['mean_factory_f2p']:.3f}")
    print(f"  Mean baseline time:    {result['mean_baseline_time']:.1f}s")
    print(f"  Mean factory time:     {result['mean_factory_time']:.1f}s")

    if result["only_factory_solved"]:
        print(f"\n  Only factory solved ({len(result['only_factory_solved'])}):")
        for iid in result["only_factory_solved"]:
            print(f"    + {iid}")

    if result["only_baseline_solved"]:
        print(f"\n  Only baseline solved ({len(result['only_baseline_solved'])}):")
        for iid in result["only_baseline_solved"]:
            print(f"    - {iid}")

    print("=" * 100)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two FeatureBench eval runs")
    parser.add_argument("--baseline", required=True, help="Path to baseline eval results dir")
    parser.add_argument("--factory", required=True, help="Path to factory eval results dir")
    parser.add_argument("--output", help="Write JSON results to this file")
    args = parser.parse_args()

    result = compare(Path(args.baseline), Path(args.factory))

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
        print(f"\nJSON written to {args.output}")


if __name__ == "__main__":
    main()
