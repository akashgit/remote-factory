"""Finalize a LUMEN run — archive iteration data and optionally model checkpoint.

Always archives: config, state, verdict, iteration data (prompts, rollouts, eval results), transcripts.
Only archives checkpoint/ when SOTA was beaten (verdict.outcome == "sota_beaten").
"""

import json
import shutil
import sys
from pathlib import Path


def main():
    run_dir = Path(".factory/lumen/.running")
    if not run_dir.exists():
        print("[FAIL] No .running directory found")
        sys.exit(1)

    cfg = json.load(open(run_dir / "config.json"))
    run_tag = cfg.get("run_started", "unknown")

    verdict_path = run_dir / "verdict.json"
    if not verdict_path.exists():
        print("[WARN] No verdict.json found — archiving without checkpoint")
        verdict = {"outcome": "unknown"}
    else:
        verdict = json.load(open(verdict_path))

    sota_beaten = verdict.get("outcome") == "sota_beaten"

    lumen_dir = run_dir.parent
    archive_dir = lumen_dir / f"run-{run_tag}"
    if archive_dir.exists():
        shutil.rmtree(archive_dir)
    archive_dir.mkdir()

    for item in run_dir.iterdir():
        dest = archive_dir / item.name
        if item.name == "checkpoint":
            if sota_beaten:
                shutil.copytree(item, dest)
                print("[OK] Checkpoint archived (SOTA beaten)")
            else:
                print("[SKIP] Checkpoint not archived (SOTA not beaten)")
            continue
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    print(f"[OK] Run archived to {archive_dir}")


if __name__ == "__main__":
    main()
