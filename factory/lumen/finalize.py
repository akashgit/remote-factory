"""Finalize a LUMEN run — clean up checkpoint if SOTA was not beaten.

The run directory is created by the workflow executor, so all data is already
in the right place. Finalize only handles selective checkpoint cleanup:
checkpoint/ is kept when SOTA was beaten, deleted otherwise to save disk space.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Finalize LUMEN run")
    parser.add_argument("--run-dir", default=None, help="Run directory path")
    args = parser.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else Path(".factory/lumen/.running")
    if not run_dir.exists():
        print(f"[FAIL] Run directory not found: {run_dir}")
        sys.exit(1)

    verdict_path = run_dir / "verdict.json"
    if not verdict_path.exists():
        print("[WARN] No verdict.json found")
        verdict = {"outcome": "unknown"}
    else:
        verdict = json.load(open(verdict_path))

    checkpoint_dir = run_dir / "checkpoint"
    if checkpoint_dir.exists():
        if verdict.get("outcome") == "sota_beaten":
            print("[OK] Checkpoint kept (SOTA beaten)")
        else:
            shutil.rmtree(checkpoint_dir)
            print("[OK] Checkpoint removed (SOTA not beaten)")

    print(f"[OK] Run finalized: {run_dir}")


if __name__ == "__main__":
    main()
