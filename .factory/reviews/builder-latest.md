# Builder Report — Fix 14 test_cli.py regressions

**Timestamp:** 2026-10-01T05:00:00Z
**Branch:** factory/run-a36f6ed5

---

## Summary

Fixed 14 test regressions in `tests/test_cli.py` caused by the `RuntimeError` change in `detect_default_branch` (PR #1557). The production code change is correct and was NOT modified.

## Root Cause

The `detect_default_branch` function in `factory/worktree.py` was changed from silently returning `'main'` to raising `RuntimeError` when no default branch can be detected. 14 CLI tests create bare temp directories (via `tmp_path`) without any git repo, so `_read_target_branch` → `detect_default_branch` now raises instead of falling back.

## Fix Applied

Added `patch("factory.cli.run._read_target_branch", return_value="main")` or `patch("factory.cli._ceo_helpers._read_target_branch", return_value="main")` to each of the 14 failing tests, matching the mock target to the module that imports `_read_target_branch`:

- **`factory.cli.run._read_target_branch`** — for tests using `main(["run", ...])`
  - `TestCmdRun.test_run_success`
  - `TestRunWithGitHubUrl.test_run_local_path_no_clone`
  - `TestRunWithGitHubUrl.test_run_design_mode`
  - `TestRunWithGitHubUrl.test_run_meta_mode`
  - `TestHeartbeatLoop.test_no_loop_single_run`
  - `TestHeartbeatLoop.test_loop_exits_after_max_cycles`
  - `TestHeartbeatLoop.test_loop_single_cycle`
  - `TestHeartbeatLoop.test_loop_graceful_sigterm`
  - `TestHeartbeatLoop.test_loop_graceful_sigint`
  - `TestHeartbeatLoop.test_loop_logs_sleep_message`

- **`factory.cli._ceo_helpers._read_target_branch`** — for tests using `main(["ceo", ..., "--headless"])`
  - `TestCmdCeo.test_ceo_headless_invokes_ceo_agent`
  - `TestCmdCeo.test_ceo_headless_meta_mode_task`
  - `TestCmdCeo.test_ceo_headless_timeout_is_2_hours`
  - `TestResearchMode.test_research_mode_task_text`

## Verification

- `uv run pytest tests/test_cli.py -x -q --tb=short` → **291 passed** ✅
- `uv run pytest tests/test_worktree.py -x -q --tb=short` → **110 passed** ✅
- `uv run ruff check tests/test_cli.py` → **All checks passed** ✅

## Files Modified

- `tests/test_cli.py` — Added `_read_target_branch` mocks to 14 tests
- `.factory/reviews/builder-latest.md` — This report
