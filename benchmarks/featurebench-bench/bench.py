#!/usr/bin/env python3
"""FeatureBench direct-workflow benchmarking script.

Runs factory's featurebench workflow directly on extracted task repos
and compares results against the standard FeatureBench baseline agent.

Usage:
    python bench.py --task-id <id1> <id2> --timeout 1800
    python bench.py --split val.jsonl --factory-only
    python bench.py --task-id <id> --baseline-only --model claude-opus-4-6
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

DATASET_NAME = "LiberCoders/FeatureBench"
DATASET_SPLIT = "full"
DEFAULT_TIMEOUT = 1800
DEFAULT_MODEL = "claude-opus-4-6"
DEFAULT_RESULTS_DIR = Path(__file__).parent / "results"


# ---------------------------------------------------------------------------
# 1. Task Setup
# ---------------------------------------------------------------------------


def load_task_metadata(task_id: str) -> dict:
    """Load task metadata from HuggingFace dataset."""
    try:
        from datasets import load_dataset

        ds = load_dataset(DATASET_NAME, split=DATASET_SPLIT)
        for row in ds:
            if row["instance_id"] == task_id:
                return dict(row)
        raise ValueError(f"Task {task_id!r} not found in {DATASET_NAME}")
    except ImportError:
        raise RuntimeError(
            "The 'datasets' library is required. Install with: pip install datasets"
        )


def load_task_ids_from_split(split_path: str | Path) -> list[str]:
    """Load task IDs from a JSONL split file."""
    split_path = Path(split_path)
    if not split_path.exists():
        # Try relative to featurebench-splits
        alt = Path(__file__).parent.parent / "featurebench-splits" / split_path
        if alt.exists():
            split_path = alt
        else:
            raise FileNotFoundError(f"Split file not found: {split_path}")

    ids = []
    for line in split_path.read_text().splitlines():
        line = line.strip()
        if line:
            ids.append(json.loads(line)["instance_id"])
    return ids


def setup_task(task_id: str, work_dir: Path | None = None) -> tuple[Path, str]:
    """Extract a task repo from its Docker image and prepare it for the agent.

    Mirrors FeatureBench's runtime.py _initialize_level1/_initialize_level2:
    - L1: copy /root/my_repo (clean base), apply masking patch, delete F2P tests
    - L2: empty /testbed with just a README.md

    Returns (task_dir, initial_commit_sha).
    """
    metadata = load_task_metadata(task_id)
    docker_image = metadata.get("image_name", metadata.get("docker_image", ""))
    if not docker_image:
        raise ValueError(f"No docker_image/image_name found for task {task_id!r}")

    level = _detect_level(task_id)

    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix=f"fb-{task_id[:20]}-"))
    else:
        work_dir.mkdir(parents=True, exist_ok=True)

    task_dir = work_dir / "testbed"
    task_dir.mkdir(parents=True, exist_ok=True)

    container_id = _docker_create(docker_image)
    try:
        if level == 1:
            _docker_cp(container_id, "/root/my_repo/.", str(task_dir))
        else:
            pass
    finally:
        _docker_rm(container_id)

    if level == 2:
        (task_dir / "README.md").write_text("put all codes in this folder\n")
    else:
        patch_content = metadata.get("patch", "")
        if patch_content and patch_content.strip():
            patch_file = work_dir / "mask.patch"
            patch_file.write_text(patch_content)
            result = subprocess.run(
                ["git", "apply", "--whitespace=fix", str(patch_file)],
                cwd=task_dir, capture_output=True, text=True, check=False,
            )
            if result.returncode != 0:
                log.warning("Failed to apply masking patch: %s", result.stderr)
            else:
                log.info("Applied masking patch (%d bytes)", len(patch_content))
            patch_file.unlink(missing_ok=True)

        fail_to_pass = metadata.get("FAIL_TO_PASS")
        if fail_to_pass:
            if isinstance(fail_to_pass, str):
                import json as _json
                try:
                    fail_to_pass = _json.loads(fail_to_pass)
                except (ValueError, TypeError):
                    fail_to_pass = [fail_to_pass]
            for f2p_test in fail_to_pass:
                f2p_path = task_dir / f2p_test.lstrip("/").removeprefix("testbed/")
                if f2p_path.exists():
                    f2p_path.unlink()
                    log.debug("Deleted F2P test file: %s", f2p_test)

    problem_stmt = task_dir / "problem_statement.md"
    ps_text = metadata.get("problem_statement", "")
    if ps_text:
        problem_stmt.write_text(ps_text)
    elif not problem_stmt.exists():
        log.warning("No problem_statement in dataset or container for %s", task_id)

    initial_sha = _git_init(task_dir)
    log.info("Task %s (L%d) extracted to %s (initial commit: %s)",
             task_id, level, task_dir, initial_sha)
    return task_dir, initial_sha


def _detect_level(task_id: str) -> int:
    """Detect task level from instance_id suffix."""
    if task_id.endswith(".lv2"):
        return 2
    return 1


def _docker_create(image: str) -> str:
    result = subprocess.run(
        ["docker", "create", image],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _docker_cp(container_id: str, src: str, dst: str) -> None:
    subprocess.run(
        ["docker", "cp", f"{container_id}:{src}", dst],
        capture_output=True, text=True, check=True,
    )


def _docker_rm(container_id: str) -> None:
    subprocess.run(
        ["docker", "rm", container_id],
        capture_output=True, text=True, check=False,
    )


def _git_init(task_dir: Path) -> str:
    subprocess.run(
        ["git", "init"], cwd=task_dir,
        capture_output=True, text=True, check=True,
    )
    subprocess.run(
        ["git", "add", "-A"], cwd=task_dir,
        capture_output=True, text=True, check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=bench@factory", "-c", "user.name=bench",
         "commit", "-m", "initial"],
        cwd=task_dir, capture_output=True, text=True, check=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=task_dir,
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# 2. Factory Workflow Execution
# ---------------------------------------------------------------------------


def run_factory(
    task_id: str,
    task_dir: Path,
    initial_sha: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Run the factory featurebench workflow on a task directory.

    Returns an output entry in FeatureBench-compatible format.
    """
    log.info("Running factory workflow on %s (timeout=%ds)", task_id, timeout)
    timed_out = False
    try:
        subprocess.run(
            ["factory", "workflow", "run", "featurebench", str(task_dir)],
            timeout=timeout,
            capture_output=True, text=True, check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("Factory workflow timed out for %s after %ds", task_id, timeout)
        timed_out = True

    # Commit any remaining changes
    subprocess.run(
        ["git", "add", "-A", "--", ".", ":(exclude).factory"],
        cwd=task_dir, capture_output=True, text=True, check=False,
    )
    subprocess.run(
        ["git", "-c", "user.email=bench@factory", "-c", "user.name=bench",
         "commit", "-m", "solution", "--allow-empty"],
        cwd=task_dir, capture_output=True, text=True, check=False,
    )

    patch = extract_patch(task_dir, initial_sha)

    return {
        "instance_id": task_id,
        "model_patch": patch,
        "agent": "factory_workflow",
        "model": "factory-featurebench",
        "success": not timed_out and bool(patch.strip()),
    }


def extract_patch(task_dir: Path, initial_sha: str) -> str:
    """Extract the git diff between initial commit and HEAD, excluding .factory/ artifacts."""
    result = subprocess.run(
        ["git", "diff", initial_sha, "HEAD", "--", ".", ":(exclude).factory"],
        cwd=task_dir, capture_output=True, text=True, check=True,
    )
    return result.stdout


# ---------------------------------------------------------------------------
# 3. Baseline Execution
# ---------------------------------------------------------------------------


def run_baseline(
    task_ids: list[str],
    model: str = DEFAULT_MODEL,
    results_dir: Path = DEFAULT_RESULTS_DIR,
) -> Path:
    """Run the standard FeatureBench baseline agent via fb CLI.

    Returns the baseline output directory.
    """
    baseline_dir = results_dir / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "fb", "infer",
        "--agent", "claude_code",
        "--model", model,
        "--output-dir", str(baseline_dir),
    ]
    for tid in task_ids:
        cmd.extend(["--task-id", tid])

    log.info("Running baseline: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)
    return baseline_dir


# ---------------------------------------------------------------------------
# 4. Evaluation
# ---------------------------------------------------------------------------


def evaluate(results_dir: Path) -> dict | None:
    """Run fb eval on output.jsonl in results_dir. Returns parsed results or None."""
    output_jsonl = results_dir / "output.jsonl"
    if not output_jsonl.exists():
        log.warning("No output.jsonl found in %s — skipping eval", results_dir)
        return None

    log.info("Running fb eval on %s", output_jsonl)
    result = subprocess.run(
        ["fb", "eval", "-p", str(output_jsonl)],
        capture_output=True, text=True, check=False,
    )

    if result.returncode != 0:
        log.error("fb eval failed: %s", result.stderr)
        return None

    # Try to find eval output — fb eval writes JSON to the same directory
    eval_files = sorted(results_dir.glob("*-featurebench-full.json"))
    if eval_files:
        return json.loads(eval_files[-1].read_text())

    # Fall back to parsing stdout
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    log.warning("Could not parse fb eval output")
    return None


# ---------------------------------------------------------------------------
# 5. Comparison
# ---------------------------------------------------------------------------


def compare(factory_results: dict, baseline_results: dict) -> dict:
    """Produce a side-by-side comparison report.

    Both inputs are dicts with "tasks" lists from fb eval output, or per-task
    entries from our output.jsonl keyed by instance_id.
    """
    f_tasks = _index_tasks(factory_results)
    b_tasks = _index_tasks(baseline_results)

    all_ids = sorted(set(f_tasks) | set(b_tasks))
    if not all_ids:
        return {"error": "no tasks found", "total_tasks": 0}

    per_task = []
    factory_resolved = 0
    baseline_resolved = 0
    only_factory = []
    only_baseline = []

    for iid in all_ids:
        f = f_tasks.get(iid, {})
        b = b_tasks.get(iid, {})
        f_res = f.get("resolved", f.get("success", False))
        b_res = b.get("resolved", b.get("success", False))

        row = {
            "instance_id": iid,
            "factory_resolved": f_res,
            "baseline_resolved": b_res,
            "factory_score": f.get("score", 0.0),
            "baseline_score": b.get("score", 0.0),
        }
        per_task.append(row)

        if f_res:
            factory_resolved += 1
        if b_res:
            baseline_resolved += 1
        if f_res and not b_res:
            only_factory.append(iid)
        if b_res and not f_res:
            only_baseline.append(iid)

    total = len(all_ids)
    report = {
        "total_tasks": total,
        "factory_resolved": factory_resolved,
        "baseline_resolved": baseline_resolved,
        "factory_resolve_rate": factory_resolved / total if total else 0,
        "baseline_resolve_rate": baseline_resolved / total if total else 0,
        "only_factory_solved": only_factory,
        "only_baseline_solved": only_baseline,
        "per_task": per_task,
    }

    _print_summary(report)
    return report


def _index_tasks(results: dict) -> dict[str, dict]:
    """Normalize results into {instance_id: task_data}."""
    if "tasks" in results:
        return {t["instance_id"]: t for t in results["tasks"] if "instance_id" in t}
    if "per_task" in results:
        return {t["instance_id"]: t for t in results["per_task"] if "instance_id" in t}
    if all(isinstance(v, dict) for v in results.values()):
        return results
    return {}


def _print_summary(report: dict) -> None:
    total = report["total_tasks"]
    print("=" * 80)
    print("FeatureBench Comparison: Factory Workflow vs Baseline")
    print("=" * 80)

    header = f"{'Instance ID':<55} {'Factory':>8} {'Baseline':>8}"
    print(header)
    print("-" * 80)

    for row in report["per_task"]:
        f_mark = "PASS" if row["factory_resolved"] else "FAIL"
        b_mark = "PASS" if row["baseline_resolved"] else "FAIL"
        print(f"{row['instance_id']:<55} {f_mark:>8} {b_mark:>8}")

    print("-" * 80)
    print(f"  Factory:  {report['factory_resolved']}/{total}"
          f" ({report['factory_resolve_rate']:.1%})")
    print(f"  Baseline: {report['baseline_resolved']}/{total}"
          f" ({report['baseline_resolve_rate']:.1%})")

    if report["only_factory_solved"]:
        print(f"\n  Only factory solved ({len(report['only_factory_solved'])}):")
        for iid in report["only_factory_solved"]:
            print(f"    + {iid}")
    if report["only_baseline_solved"]:
        print(f"\n  Only baseline solved ({len(report['only_baseline_solved'])}):")
        for iid in report["only_baseline_solved"]:
            print(f"    - {iid}")

    print("=" * 80)


# ---------------------------------------------------------------------------
# 6. CLI Interface
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FeatureBench direct-workflow benchmarking script",
    )
    parser.add_argument(
        "--task-id", nargs="+", dest="task_ids",
        help="One or more FeatureBench task IDs to run",
    )
    parser.add_argument(
        "--split", type=str,
        help="Path to a JSONL split file to load task IDs from",
    )
    parser.add_argument(
        "--factory-only", action="store_true",
        help="Skip baseline, only run factory workflow",
    )
    parser.add_argument(
        "--baseline-only", action="store_true",
        help="Skip factory workflow, only run baseline",
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help=f"Per-task timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--model", type=str, default=DEFAULT_MODEL,
        help=f"Model for baseline agent (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--results-dir", type=Path, default=DEFAULT_RESULTS_DIR,
        help="Output directory for results",
    )
    parser.add_argument(
        "--skip-eval", action="store_true",
        help="Skip the fb eval step",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose logging",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    task_ids = _resolve_task_ids(args)
    if not task_ids:
        parser.error("Provide --task-id or --split")

    if args.factory_only and args.baseline_only:
        parser.error("Cannot use --factory-only and --baseline-only together")

    results_dir = args.results_dir
    results_dir.mkdir(parents=True, exist_ok=True)

    factory_results = None
    baseline_results = None

    # --- Factory workflow ---
    if not args.baseline_only:
        factory_dir = results_dir / "factory"
        factory_dir.mkdir(parents=True, exist_ok=True)
        factory_entries = []

        for tid in task_ids:
            log.info("=== Factory: %s ===", tid)
            try:
                task_dir, initial_sha = setup_task(tid)
                entry = run_factory(tid, task_dir, initial_sha, timeout=args.timeout)
                factory_entries.append(entry)
                log.info(
                    "Task %s: success=%s, patch_size=%d bytes",
                    tid, entry["success"], len(entry["model_patch"]),
                )
            except Exception:
                log.exception("Failed to run factory on task %s", tid)
                factory_entries.append({
                    "instance_id": tid,
                    "model_patch": "",
                    "agent": "factory_workflow",
                    "model": "factory-featurebench",
                    "success": False,
                })
            finally:
                if "task_dir" in locals() and task_dir.exists():
                    shutil.rmtree(task_dir.parent, ignore_errors=True)

        output_jsonl = factory_dir / "output.jsonl"
        with output_jsonl.open("w") as f:
            for entry in factory_entries:
                f.write(json.dumps(entry) + "\n")
        log.info("Factory output written to %s", output_jsonl)

        if not args.skip_eval:
            factory_results = evaluate(factory_dir)
        if factory_results is None:
            factory_results = {"tasks": factory_entries}

    # --- Baseline ---
    if not args.factory_only:
        log.info("=== Running baseline ===")
        try:
            baseline_dir = run_baseline(task_ids, model=args.model, results_dir=results_dir)
            if not args.skip_eval:
                baseline_results = evaluate(baseline_dir)
        except Exception:
            log.exception("Baseline run failed")

    # --- Comparison ---
    if factory_results and baseline_results:
        report = compare(factory_results, baseline_results)
        report_path = results_dir / "comparison_report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        log.info("Comparison report written to %s", report_path)
    elif factory_results:
        log.info("Factory-only run complete. %d tasks processed.", len(task_ids))
    elif baseline_results:
        log.info("Baseline-only run complete. %d tasks processed.", len(task_ids))


def _resolve_task_ids(args: argparse.Namespace) -> list[str]:
    if args.task_ids:
        return args.task_ids
    if args.split:
        return load_task_ids_from_split(args.split)
    return []


if __name__ == "__main__":
    main()
