## Builder Review

### Changes

- **factory/worktree.py**: In `detect_default_branch()`, replaced the final `return "main"` fallback with `raise RuntimeError(...)` containing an actionable error message pointing at two workarounds (set `target_branch` in config, or check out a branch). Updated `log.debug` to `log.warning` on the error path. Updated docstring cascade description.

- **tests/test_worktree.py**: In `TestDetectDefaultBranchFallback`:
  - Updated `test_fallback_when_all_detection_fails` to expect `pytest.raises(RuntimeError)` instead of `assert == "main"`
  - Added `test_error_message_contains_workaround_hints` verifying both hints are present in the error message
  - Added `test_critical_path_raise_prevents_opaque_git_error` — a behavioral test exercising the full critical path with a real detached-HEAD repo (no main/master, no origin), proving the raise prevents the opaque git rev-parse exit-code-128 error from ever occurring

### Verification

- All 10 `DefaultBranch` tests pass
- All 3 new/updated fallback tests pass
- `ruff check` passes on both files
