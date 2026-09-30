# Builder Report — Fix aggregate_report SyntaxError

**Builder:** builder-agent
**Date:** 2026-09-30
**Branch:** factory/run-da1796b9

---

## Summary

Fixed the critical `SyntaxError` in the `aggregate_report` FnNode command string in `.factory/workflows/batch_doc_scorer.py`.

## Root Cause

The `aggregate_report` node's `command` field used Python implicit string concatenation (adjacent string literals), which produces a single-line string with **no actual newlines**. All statements were separated by semicolons, but Python does not allow compound statements (`if`, `for`, `def`) after semicolons on a single line. Five compound statements caused the `SyntaxError`:

1. `;if not files:` — compound `if` after semicolon
2. `;for f in files:` — compound `for` after semicolon
3. `;def stats(vals):` — compound `def` after semicolon
4. `;for f in sorted(...)` — compound `for` after semicolon
5. `;if needs_review:` — compound `if` after semicolon

## Fix Applied

Replaced semicolons with explicit `\n` newline characters between all Python statements in the `aggregate_report` command string. The executor's `_run_shell_or_exec` method (executor.py:1525) uses `re.DOTALL` when extracting code from `python3 -c "..."`, so multi-line code with actual newlines is fully supported.

**validate_scan GateNode** was verified to be NOT affected — it uses only simple statements (imports, assignments, function calls) separated by semicolons, which Python handles correctly on a single line. No changes needed.

## Verification

1. **Compilation test**: Extracted the Python code using the same regex as the executor (`re.DOTALL`). Both `aggregate_report` and `validate_scan` commands compile successfully.

2. **End-to-end test (happy path)**: Created a mock `scores.json` with 3 files (varying quality scores), ran the aggregate_report command via `python3 -c`. Verified:
   - Exit code: 0
   - `doc-quality-report.md` — contains header, statistics, per-file table, and "Needs Review" section
   - `doc-quality-report.json` — contains `files_scored`, `statistics` (mean/median/min/max per dimension), and sorted file list

3. **End-to-end test (empty files)**: Tested with `{"files": []}`. Verified:
   - Exit code: 0
   - `doc-quality-report.md` — contains "No files scored."
   - `doc-quality-report.json` — contains `{"files_scored": 0}`

4. **Existing tests**: All 34 tests pass (`pytest tests/test_workflow_batch_doc_scorer.py -v`)

5. **Linting**: `ruff check .factory/workflows/batch_doc_scorer.py` — all checks passed

## Files Changed

| File | Change |
|------|--------|
| `.factory/workflows/batch_doc_scorer.py` | Replaced `;` with `\n` between statements in aggregate_report command |

## Status

✅ Fix complete — aggregate_report command compiles and executes correctly.
