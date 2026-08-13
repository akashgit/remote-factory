"""LUMEN workflow preflight — environment check, GPU probe, run directory setup.

LUMEN: Learning-based Universal Modeling and Evolution eNgine
RL training system for scientific discovery tasks.

Environment: Uses uv virtual environment (default: factory/lumen/.venv)
Override via: LUMEN_PYTHON environment variable

Usage:
    python3 -m factory.lumen.preflight --project-path /path
    python3 -m factory.lumen.preflight --project-path /path --task-dir benchmarks/einsteinarena/circle-packing

Reads:
    - .factory/lumen/config.json                   (task_name + user overrides)
    - benchmarks/einsteinarena/{task}/config.json   (per-task defaults)

Writes:
    - .factory/lumen/run-{timestamp}/config.json   (resolved config for this run)
    - .factory/lumen/current_run -> run-{timestamp} (symlink to active run)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


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

    # Check critical packages
    check_cmd = [
        str(python_exe), "-c",
        "import sys; "
        "missing = []; "
        "try: import torch\n"
        "except ImportError: missing.append('torch')\n"
        "try: import vllm\n"
        "except ImportError: missing.append('vllm')\n"
        "try: import verl\n"
        "except ImportError: missing.append('verl')\n"
        "try: import numpy\n"
        "except ImportError: missing.append('numpy')\n"
        "try: import pandas\n"
        "except ImportError: missing.append('pandas')\n"
        "if missing: print(f'MISSING:{','.join(missing)}'); sys.exit(1)\n"
        "else: print('OK')"
    ]
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Lumen preflight check")
    parser.add_argument("--project-path", required=True, help="Project root")
    parser.add_argument("--task-dir", default=None, help="Einstein Arena task directory (overrides config)")
    parser.add_argument("--mock", action="store_true", help="Mock mode (skip conda/GPU checks)")
    args = parser.parse_args()

    project_path = Path(args.project_path).resolve()

    # Resolve task_dir: CLI arg > .factory/lumen/config.json
    lumen_dir = project_path / ".factory" / "lumen"
    user_config_path = lumen_dir / "config.json"
    if args.task_dir:
        task_dir = Path(args.task_dir)
    elif user_config_path.exists():
        with open(user_config_path) as f:
            launch_cfg = json.load(f)
        task_name = launch_cfg.get("task_name", "")
        task_dir = Path(launch_cfg.get("task_dir", f"benchmarks/einsteinarena/{task_name}"))
        if launch_cfg.get("mock", False):
            args.mock = True
    else:
        print("[FAIL] No --task-dir and no .factory/lumen/config.json found")
        sys.exit(1)

    if not task_dir.is_absolute():
        task_dir = project_path / task_dir

    print("=== Lumen Preflight ===")

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

    # 3. Load per-task defaults
    task_config_path = task_dir / "config.json"
    if not task_config_path.exists():
        print(f"[FAIL] Task config not found: {task_config_path}")
        sys.exit(1)

    with open(task_config_path) as f:
        task_defaults = json.load(f)
    print(f"[OK] Task config loaded: {task_config_path}")

    # 4. Load user overrides (optional)
    user_overrides = {}
    if user_config_path.exists():
        with open(user_config_path) as f:
            user_overrides = json.load(f)
        print(f"[OK] User overrides loaded: {user_config_path}")

    # 5. Merge: task defaults ← user overrides ← GPU detection
    resolved = dict(task_defaults)
    resolved.update(user_overrides)
    resolved = derive_training_params(gpu_info, resolved)

    # Add metadata
    task_name = task_dir.name
    resolved["task_name"] = task_name
    resolved["task_dir"] = str(task_dir)
    resolved["mock"] = args.mock
    resolved["gpu_info"] = gpu_info

    # 6. Create run directory
    lumen_dir.mkdir(parents=True, exist_ok=True)
    run_tag = make_run_tag()
    run_dir = lumen_dir / f"run-{run_tag}"
    run_dir.mkdir()

    # Write resolved config
    config_out = run_dir / "config.json"
    with open(config_out, "w") as f:
        json.dump(resolved, f, indent=2)

    # Write initial state
    state = {"iteration": 0, "best_score": None}
    with open(run_dir / "state.json", "w") as f:
        json.dump(state, f, indent=2)

    # 7. Update "current_run" symlink
    current_link = lumen_dir / "current_run"
    if current_link.is_symlink() or current_link.exists():
        current_link.unlink()
    current_link.symlink_to(f"run-{run_tag}")

    print()
    print(f"Run directory: {run_dir}")
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
