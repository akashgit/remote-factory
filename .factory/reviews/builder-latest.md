## Builder Review — Issue #1543

### What was built
Modified `factory/agents/prompts/builder.md` to add reloop-aware PR handling, preventing the Builder agent from overwriting PR title/body on follow-up commits.

### Changes
- **Step 7 (Task section):** Replaced single `gh pr create` instruction with a two-branch flow:
  1. PR existence check via `gh pr list --head $(git branch --show-current) --json number --jq '.[0].number'`
  2. First run (no PR): create PR as before with `gh pr create`
  3. Reloop (PR exists): `git push` only, with explicit rules forbidding `gh pr edit --title`, `gh pr edit --body`, and PR comments
- **Output section:** Updated PR format note to clarify it applies on first run only; on reloop the original body is preserved
- **Exit conditions:** Split into three cases: Success (first run), Success (reloop), and Blocked

### Tests
- 165/165 smoke tests pass
- No code changes (prompt-only fix), so no new tests required

### Scope verification
- Only file modified: `factory/agents/prompts/builder.md` (listed in factory.md modifiable scope as `factory/agents/prompts/*.md`)
