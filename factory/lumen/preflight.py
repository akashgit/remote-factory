"""LUMEN workflow preflight — environment check, GPU probe, run directory setup.

LUMEN: Learning-based Universal Modeling and Evolution eNgine
RL training system for scientific discovery tasks.

Environment: Uses uv virtual environment (default: factory/lumen/.venv)
Override via: LUMEN_PYTHON environment variable

Usage:
    # Mode A: explicit task argument
    python3 -m factory.lumen.preflight --project-path /path --task circle-packing

    # Mode B: infer task from directory name
    python3 -m factory.lumen.preflight --project-path /path/to/circle-packing

Reads (in priority order):
    - benchmarks/einsteinarena/{task}/config.json          (per-task custom config, if exists)
    - benchmarks/einsteinarena/{task}/default_config.json  (per-task defaults, fallback)

Writes (to --run-dir, or .factory/lumen/.running/ if not specified):
    - {run_dir}/config.json    (resolved config)
    - {run_dir}/state.json     (iteration state)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def cleanup_gpu_processes():
    """Clean up any lingering Ray/vLLM processes from previous runs."""
    try:
        # Find Ray and vLLM processes owned by current user
        result = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-f", "ray::"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

        # Clean up vLLM processes
        result = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-f", "VLLM"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

        print("[OK] GPU processes cleaned")
    except Exception as e:
        print(f"[WARN] GPU cleanup failed: {e}")


def check_uv_env(python_path: str | None = None) -> tuple[bool, str]:
    """Check if the uv virtual environment exists and has critical packages.

    Args:
        python_path: Path to Python executable. Defaults to LUMEN_PYTHON env var
                     or factory/lumen/.venv/bin/python (relative to project root)
    """
    if python_path is None:
        python_path = os.getenv("LUMEN_PYTHON", "factory/lumen/.venv/bin/python")

    python_exe = Path(python_path)
    if not python_exe.is_absolute():
        # Resolve relative path from current directory
        python_exe = Path.cwd() / python_exe
    python_exe = python_exe.expanduser()

    # Check if Python executable exists
    if not python_exe.exists():
        return False, f"uv venv Python not found at {python_exe}"

    if not python_exe.is_file():
        return False, f"{python_exe} is not a file"

    # Check critical packages using importlib
    check_code = """
import sys
import importlib.util
missing = []
for pkg in ['torch', 'vllm', 'verl', 'numpy', 'pandas']:
    if importlib.util.find_spec(pkg) is None:
        missing.append(pkg)
if missing:
    print('MISSING:' + ','.join(missing))
    sys.exit(1)
else:
    print('OK')
"""
    check_cmd = [str(python_exe), "-c", check_code]
    result = subprocess.run(check_cmd, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        output = result.stdout.strip()
        if output.startswith("MISSING:"):
            missing = output.split(":")[1]
            return False, f"uv venv at {python_exe} missing packages: {missing}"
        return False, f"uv venv at {python_exe} package check failed: {result.stderr}"

    return True, f"uv venv verified at {python_exe}"


def detect_gpus() -> dict:
    """Detect GPU hardware via nvidia-smi."""
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return {"gpu_count": 0, "gpu_type": "none", "gpu_memory_mb": 0}

    lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    if not lines:
        return {"gpu_count": 0, "gpu_type": "none", "gpu_memory_mb": 0}

    name, mem = lines[0].split(", ")
    return {
        "gpu_count": len(lines),
        "gpu_type": name.strip(),
        "gpu_memory_mb": int(float(mem.strip())),
    }


def derive_training_params(gpu_info: dict, task_defaults: dict) -> dict:
    """Override GPU-dependent defaults based on detected hardware."""
    params = dict(task_defaults)
    detected_gpus = gpu_info["gpu_count"]

    if detected_gpus > 0 and detected_gpus != params.get("num_gpus"):
        params["num_gpus"] = detected_gpus

    if detected_gpus > 0:
        tp = params.get("rollout_tp", 4)
        if tp > detected_gpus:
            params["rollout_tp"] = max(1, detected_gpus // 2)

    return params


def make_run_tag() -> str:
    """Generate a timestamp-based run tag (UTC), e.g. '20260813-143022'."""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# Supported tasks
SUPPORTED_TASKS = [
    "circle-packing",
    "first-autocorrelation-inequality",
    "second-autocorrelation-inequality",
    "erdos-min-overlap",
]


def validate_task(task_name: str) -> tuple[bool, str]:
    """Validate task name."""
    if task_name not in SUPPORTED_TASKS:
        return False, (
            f"Unknown task: {task_name}\n"
            f"Supported tasks:\n" + "\n".join(f"  - {t}" for t in SUPPORTED_TASKS)
        )
    return True, ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Lumen preflight check")
    parser.add_argument("--project-path", required=True, help="Project root")
    parser.add_argument("--run-dir", default=None, help="Run directory (created by executor). Falls back to .factory/lumen/.running/")
    parser.add_argument("--task", required=False, default=None, help="Task name (e.g., circle-packing). If omitted, inferred from project directory name.")
    parser.add_argument("--config", default=None, help="Custom config file path (highest priority)")
    parser.add_argument("--mock", action="store_true", help="Mock mode (skip venv/GPU checks)")

    args = parser.parse_args()

    project_path = Path(args.project_path).resolve()

    # ── Dual-mode task detection ────────────────────────────────
    # Mode A: explicit --task argument
    # Mode B: infer task name from project directory name
    task_from_dir = False
    if args.task is not None:
        task_name = args.task
    else:
        candidate = project_path.name
        ok, _ = validate_task(candidate)
        if ok:
            task_name = candidate
            task_from_dir = True
        else:
            print("[FAIL] --task not provided and directory name "
                  f"{candidate!r} is not a supported task.\n"
                  f"Supported tasks:\n" + "\n".join(f"  - {t}" for t in SUPPORTED_TASKS))
            sys.exit(1)

    print("=== Lumen Preflight ===")
    print(f"Task: {task_name}")

    # ── Validation Step 1: Task name ─────────────────────────────
    ok, msg = validate_task(task_name)
    if not ok:
        print(f"[FAIL] {msg}")
        sys.exit(1)
    print(f"[OK] Task name validated: {task_name}")

    # Clean up any lingering GPU processes from previous runs
    cleanup_gpu_processes()

    # ── Resolve task_dir ─────────────────────────────────────────
    if task_from_dir:
        # Mode B: project_path IS the instance directory
        task_dir = project_path
    else:
        # Mode A: project_path is the factory root, look up task subdirectory
        task_dir = project_path / "benchmarks" / "einsteinarena" / task_name
        if not task_dir.exists():
            task_dir = project_path / task_name
            if not task_dir.exists():
                print(f"[FAIL] Task directory not found: {task_dir}")
                sys.exit(1)

    # 1. Check uv venv (skip in mock mode)
    if args.mock:
        print("[SKIP] Python env check (mock mode)")
    else:
        ok, msg = check_uv_env()
        print(f"[{'OK' if ok else 'FAIL'}] {msg}")
        if not ok:
            print("\n" + "=" * 60)
            print("ERROR: Lumen training environment not ready")
            print("=" * 60)
            print("\nThe Lumen workflow requires a pre-configured Python environment.")
            print("Please follow the installation steps in:")
            print("\n  factory/lumen/README.md")
            print("\nAfter installation, verify with:")
            print("  factory/lumen/.venv/bin/python factory/lumen/env_specs/verify_env.py")
            print("=" * 60)
            sys.exit(1)

    # 2. Detect GPUs (skip in mock mode)
    if args.mock:
        gpu_info = {"gpu_count": 0, "gpu_type": "mock", "gpu_memory_mb": 0}
        print("[SKIP] GPU detection (mock mode)")
    else:
        gpu_info = detect_gpus()
        if gpu_info["gpu_count"] == 0:
            print("[WARN] No GPUs detected — training will fail in real mode")
        else:
            print(f"[OK] {gpu_info['gpu_count']}x {gpu_info['gpu_type']} "
                  f"({gpu_info['gpu_memory_mb']} MB each)")

    # 3. Load per-task config (prefer config.json, fallback to default_config.json)
    task_config_path = task_dir / "config.json"
    if not task_config_path.exists():
        task_config_path = task_dir / "default_config.json"
        if not task_config_path.exists():
            print(f"[FAIL] Task config not found: {task_dir}/config.json or {task_dir}/default_config.json")
            sys.exit(1)

    with open(task_config_path) as f:
        task_defaults = json.load(f)
    print(f"[OK] Task config loaded: {task_config_path}")

    # 4. Load custom config (if provided via --config)
    custom_config = {}
    if args.config:
        custom_config_path = Path(args.config)
        if not custom_config_path.exists():
            print(f"[FAIL] Custom config not found: {custom_config_path}")
            sys.exit(1)
        with open(custom_config_path) as f:
            custom_config = json.load(f)
        print(f"[OK] Custom config loaded: {custom_config_path}")

    # 5. Merge: task defaults ← custom config ← GPU detection
    resolved = dict(task_defaults)
    resolved.update(custom_config)
    resolved = derive_training_params(gpu_info, resolved)

    # Add metadata
    resolved["task_name"] = task_name
    resolved["task_dir"] = str(task_dir)
    resolved["gpu_info"] = gpu_info

    # 6. Resolve run directory
    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
    else:
        lumen_dir = project_path / ".factory" / "lumen"
        lumen_dir.mkdir(parents=True, exist_ok=True)
        run_dir = lumen_dir / ".running"
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write resolved config
    resolved["run_started"] = make_run_tag()
    config_out = run_dir / "config.json"
    with open(config_out, "w") as f:
        json.dump(resolved, f, indent=2)

    # Write initial state
    state = {"iteration": 0, "best_score": None, "best_iteration": None, "best_solution": {}}
    with open(run_dir / "state.json", "w") as f:
        json.dump(state, f, indent=2)

    print()
    print(f"Run directory: {run_dir} (started {resolved['run_started']})")
    print(f"Resolved config: {config_out}")
    print(f"  task_name: {task_name}")
    print(f"  model_path: {resolved.get('model_path')}")
    print(f"  num_gpus: {resolved.get('num_gpus')}")
    print(f"  rollout_tp: {resolved.get('rollout_tp')}")
    print(f"  mock: {args.mock}")
    print()
    print("Preflight complete.")


if __name__ == "__main__":
    main()
