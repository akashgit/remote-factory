## Builder Review: Fix optimize-sorting SKILL.md Regeneration

**Branch:** factory/run-a26d6e43
**Date:** 2026-09-02

### Problem
The optimize-sorting workflow's SKILL.md was stale — missing gap detection and I/O profiling content from the Python workflow definition added in commit 93d2cd46. The `factory workflow export-skills` command had regenerated the SKILL.md to `workflow-optimize-sorting/` at the project root (104KB, 2521 lines) but this wasn't committed, and the `skills/` cache wasn't updated.

### Changes Made
1. **Copied regenerated SKILL files to skills/ cache**: `skills/workflow-optimize-sorting/SKILL.md` and `SKILL.annotations.yaml` now contain gap detection (1 match) and capture_io (42 matches) content
2. **Added root-level workflow-optimize-sorting/ SKILL files to git tracking**: Matches the pattern of `workflow-design-v2/` which has its SKILL.md tracked at the root level
3. **Reverted unrelated design-v2 changes**: `git checkout main -- workflow-design-v2/` removed unrelated SKILL regeneration diffs from the working tree
4. **Did NOT force-push or amend**: PR #1435 does not exist; created a new commit and PR instead

### Verification
- `grep -c 'Gap Detection' skills/workflow-optimize-sorting/SKILL.md` → 1 ✓
- `grep -c 'capture_io' skills/workflow-optimize-sorting/SKILL.md` → 42 ✓
- `workflow-design-v2/` changes reverted to main ✓
- No fixed_surfaces or eval/score.py files modified ✓

### File Size Gate
- `workflow-optimize-sorting/SKILL.md`: 2521 lines — exceeds 500-line limit but is a generated file (export-skills output), splitting would break tooling. Justified.
- `workflow-optimize-sorting/SKILL.annotations.yaml`: 805 lines — generated YAML, same justification.

### Notes
- `skills/workflow-*/SKILL.md` files are gitignored (auto-generated cache). The local cache was refreshed but the git-tracked copy lives at `workflow-optimize-sorting/SKILL.md` at the root level.
- Root-level `workflow-optimize-sorting/` directory was NOT removed since its contents are being committed as the tracked SKILL files.
