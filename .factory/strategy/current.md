## Strategy — 2026-04-12 (Cycle 3 — Persistent & Self-Educating)

### Source: User Feedback + Paperclip Research + Cycle 2 Observations
Cycle 2 was successful (6/6 kept, 0.9125 → 0.9725) but revealed structural gaps:
1. The factory only reads its own logs — it never searches externally for inspiration
2. Obsidian integration is dead code — 12 tests pass but nothing calls it
3. `factory run` only accepts local paths — can't bootstrap from a GitHub URL
4. No persistent loop — each `factory run` is one-shot, human must re-invoke
5. Only 2 of 6 agent roles actually run (strategist + builder) — reviewer, evaluator, archivist, researcher are unused

Paperclip (github.com/paperclipai/paperclip) patterns worth adopting:
- **Heartbeat scheduling**: agents wake on timer, get fresh context, work, sleep
- **Atomic task checkout**: GitHub issues as work queue with label-based claiming
- **Goal inheritance**: all work traces to top-level goal through parent chain

### Observations
- Current composite: 0.9725 (all dimensions passing)
- 215 tests, 89% coverage, lint + mypy clean
- 6 experiments total, all kept
- Obsidian notes.py + templates.py at 100% coverage but 0 production callers
- factory/agents/runner.py at 48% coverage (invoke_agent never called from CLI)

### Hypotheses

#### H1: Accept GitHub URL in `factory run` — auto-clone and bootstrap
- **What:** Modify `cmd_run` to detect if the path argument is a GitHub URL (matches `https://github.com/` or `git@github.com:`). If so, clone the repo to a temp directory, then proceed with the normal flow. Also add `--mode` flag to specify which mode to run (discover/review/improve).
- **Why:** User asked "how does the factory start?" — it should work with just a GitHub URL. Currently requires manual clone + cd + run.
- **Expected impact:** Factory can bootstrap any public repo from a URL. New feature.
- **Priority:** high
- **Files:** factory/cli.py

#### H2: Wire up Obsidian integration — add `factory archive` command
- **What:** Add `cmd_archive` CLI handler that:
  - Reads experiment history from the store
  - Calls `write_experiment_note()` for each unarchived experiment
  - Calls `write_project_dashboard()` with current state
  - Calls `write_strategy_note()` with current strategy
  - Tracks which experiments have been archived (avoid duplicates)
  - Make vault path configurable via `OBSIDIAN_VAULT_PATH` env var (default: ~/obsidian-vaults/factory/)
- **Why:** Obsidian modules exist with 12 passing tests but are never called. The Archivist agent role references them. This is the knowledge base that enables cross-project learning.
- **Expected impact:** Factory preserves institutional knowledge across cycles. Enables the Researcher to read prior learnings.
- **Priority:** high
- **Files:** factory/cli.py, factory/obsidian/notes.py, tests/test_cli.py

#### H3: Add web search to `factory study` — Researcher finds inspiration
- **What:** Extend `factory study` to also:
  - Search GitHub for similar projects (using `gh search repos` or web search)
  - Read Obsidian notes from prior cycles (cross-project knowledge)
  - Write a "research" section in observations.md with external findings
  - The Strategist then reads this alongside interaction logs when generating hypotheses
- **Why:** User said "after studying its own code, factory should search to find other projects for inspiration." Currently the study phase only reads its own logs — it's myopic.
- **Expected impact:** Better hypotheses informed by external best practices. This is the Researcher agent role made real.
- **Priority:** high
- **Files:** factory/study.py, factory/cli.py, tests/test_study.py

#### H4: Add heartbeat loop to `factory run` — persistent autonomous operation
- **What:** Add `--loop` flag to `factory run` that:
  - Runs the Improve cycle
  - Sleeps for configurable interval (default 30 min, via `--interval`)
  - Wakes up, checks for open issues (atomic checkout via labels)
  - Runs next cycle
  - Logs each heartbeat with timestamp
  - Handles SIGTERM gracefully (finish current work, then exit)
  Inspired by Paperclip's heartbeat pattern.
- **Why:** Currently one-shot. User wants "an army of continuous worker agents." The heartbeat pattern from Paperclip is the right model — discrete execution cycles with state persistence between wakeups.
- **Expected impact:** Factory runs autonomously as a background daemon. Combined with `factory archive`, creates a self-improving loop with institutional memory.
- **Priority:** medium
- **Files:** factory/cli.py

#### H5: Wire up all 6 agent roles in SKILL.md Improve mode
- **What:** Update SKILL.md to actually invoke:
  - Researcher (before Strategist): `factory study` + web search
  - Evaluator (before/after each experiment): `factory eval`
  - Reviewer (after builder): `factory guard`
  - Archivist (after cycle): `factory archive`
  Currently only Strategist and Builder run. The other 4 are described but never executed.
- **Why:** The agent topology was designed but not implemented. Each role adds value: Researcher informs hypotheses, Evaluator measures impact, Reviewer enforces safety, Archivist preserves knowledge.
- **Expected impact:** Full 6-agent loop working end-to-end.
- **Priority:** medium
- **Files:** SKILL.md

### Anti-patterns to Avoid
- Don't add external dependencies for web search — use `gh search repos` and the existing WebSearch/WebFetch tools in Claude
- Don't make the heartbeat loop too complex — start with sleep + re-invoke, not a full event system
- Don't break the existing one-shot `factory run` — `--loop` is opt-in

### Session State
- **Mode:** Improve (Cycle 3 — Persistent & Self-Educating)
- **Current phase:** Execute hypotheses — spawn workers
- **Active experiments:** None yet
- **Next action:** Create issues and spawn workers for H1-H5
