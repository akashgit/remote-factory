# Builder Review — Ephemeral Mode Registration for CEO Subprocess

## Summary

Fixed `_run_ceo_subprocess` in `InnerLoop` to register the candidate workflow as an ephemeral mode before spawning the CEO subprocess, preventing "Error: unknown mode task-eval" failures.

## Changes

### factory/inner_loop.py
- Added `_ensure_ephemeral_mode()` — registers `self.workflow` as an ephemeral mode by writing:
  - Mode JSON to `.factory/outer_loop/modes/eval-{mode}-{hash}.json`
  - Workflow wrapper to `.factory/workflows/eval-{mode}-{hash}.py`
  - Uses content-based hash in mode name to avoid collisions
- Added `_cleanup_ephemeral_mode()` — removes the ephemeral mode files after subprocess completes
- Updated `_run_ceo_subprocess()`:
  - Calls `_ensure_ephemeral_mode()` before spawning
  - Uses registered `mode_name` (not `self.mode`) in `--mode` flag
  - Cleans up in `finally` block (alongside prompt cleanup)
  - Checks both `mode_name` and `self.mode` paths for cycle_summary.json recovery

### tests/test_inner_loop_dispatch.py
- Added `TestEphemeralModeRegistration` class with 3 tests:
  - `test_ceo_subprocess_registers_ephemeral_mode` — verifies mode JSON + wrapper exist during subprocess.run
  - `test_ceo_subprocess_cleans_up_ephemeral_mode` — verifies files removed after completion
  - `test_ceo_subprocess_uses_registered_mode_name` — verifies `--mode` flag uses registered name

## Verification
- All 15 tests in test_inner_loop_dispatch.py pass
- ruff check: clean
- mypy: clean
- Smoke tests: 165 passed
