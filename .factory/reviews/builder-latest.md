# Builder Agent Output

- **timestamp:** 2026-09-15
- **exit_code:** 0
- **branch:** factory/run-e3ddbac6
- **pr:** #1494 (existing — pushed fixes to branch)

## Changes

### Fix A — `tests/test_compose.py` (path resolution)
- Added module-level constant `_CHESS_EVOLVE_TOML` using `Path(__file__).resolve().parent.parent / ...` to resolve the chess-evolve.toml path absolutely (stable under pytest-xdist `-n auto` where CWD differs from repo root)
- Updated both `test_chess_evolve_toml_no_builder_required` and `test_chess_evolve_toml_passes_any_workflow` to use the constant instead of the relative path string

### Fix B — `factory/outer_loop/similarity.py` (NoveltyFilter GED=0 logic)
- Modified the GED loop in `NoveltyFilter.is_novel()` to `continue` when `ged == 0` (identical topology)
- Rationale: When GED=0, topology is identical to an archived workflow. If the structural hash check above already passed (hash is novel), the difference must be content-only (prompts, params). Content-only mutations are intentionally novel — exact duplicates are caught by the hash dedup. The GED loop should only reject when topology distance is non-zero but below threshold.

## Verification

- 3 previously-failing tests now pass:
  - `test_chess_evolve_toml_no_builder_required` ✅
  - `test_chess_evolve_toml_passes_any_workflow` ✅
  - `test_prompt_only_mutation_passes_is_novel` ✅
- Full test suites pass with no regressions:
  - `tests/test_compose.py`: 51/51 passed
  - `tests/test_outer_loop/test_similarity.py`: 18/18 passed
