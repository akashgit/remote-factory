## Builder Review — H1: Replace hardcoded "main" fallback with RuntimeError

**PR:** #1561
**Issue:** #1341
**Branch:** factory/run-c1aac926
**Status:** ✅ Complete

### Changes Made

| File | Change |
|------|--------|
| `factory/worktree.py` | Replaced `log.debug` + `return "main"` fallback with `log.warning("detect_default_branch.failed")` + `raise RuntimeError(...)` |
| `factory/worktree.py` | Updated docstring to document `RuntimeError` raise behavior |
| `tests/test_worktree.py` | Updated `test_fallback_when_all_detection_fails` to use `pytest.raises(RuntimeError)` |
| `tests/test_worktree.py` | Added `test_detached_head_no_main_master_no_origin_raises` — real git repo in detached state |

### Acceptance Criteria Verification

| # | Criterion | Result |
|---|-----------|--------|
| 1 | L910-911 no longer returns `"main"` | ✅ `grep -n 'return "main"' factory/worktree.py` — no matches |
| 2 | `RuntimeError` raised with `project_path` in message | ✅ `raise RuntimeError(f"Could not detect…{project_path}…")` |
| 3 | Error message mentions both workarounds | ✅ Contains `target_branch` and `check out a named branch` |
| 4 | `log.warning` emitted before raise | ✅ `log.warning("detect_default_branch.failed", project_path=…)` precedes raise |
| 5 | `test_fallback_when_all_detection_fails` expects RuntimeError | ✅ Uses `pytest.raises(RuntimeError, match=…)` |
| 6 | New test: detached HEAD + no main/master + no origin → RuntimeError | ✅ `test_detached_head_no_main_master_no_origin_raises` |
| 7 | No try/except added in callers | ✅ No new `except RuntimeError` in `factory/cli/` |
| 8 | No custom exception class created | ✅ No new `class …Error` in `factory/worktree.py` |
| 9 | `pytest tests/test_worktree.py -x` passes | ✅ 110 passed |
| 10 | `ruff check` passes | ✅ All checks passed |

### Anti-patterns Avoided

- ❌ No configurable strict/lenient mode
- ❌ No caller-side try/except wrappers
- ❌ No custom exception class
- ❌ No verbose detection-method listing in error message
