# Development Log

## 2026-08-14 — LangGraph workflow runtime

### Completed

- Replaced recursive workflow traversal with a direct Factory DSL → LangGraph compiler.
- Added SQLite checkpoints, exact workflow thread snapshots, interrupts/resume, and operation receipts.
- Preserved native fork/join, gate routing, reloops, HALT cleanup, and SubgraphFork worktree isolation.
- Rebuilt `workflow tool` as a thin interactive client over graph checkpoints; removed cursor/cache state.
- Made LangGraph the default for `factory ceo`, `factory run`, and tmux. Kept `--engine skill` as the explicit legacy fallback.
- Added scalar and interrupt-ID-map resume CLI contracts and preserved incomplete worktrees for recovery.
- Updated architecture, workflow, contributor, benchmark, agent, and CLI documentation.

### Decisions and gotchas

- `blocking=False` remains DSL metadata for legacy rendering; LangGraph executes every node durably.
- Checkpoints live at `.factory/langgraph/checkpoints.sqlite`; manifests and receipts live below the same directory.
- A `started` receipt without completion blocks automatic retry because the external side effect is ambiguous.
- Interrupted and failed graph worktrees are retained; completed and terminal worktrees follow normal cleanup.
- Subgraph branches use separate SQLite files to avoid concurrent writer contention.

### Verification

- `ruff check factory ...`: passed.
- `mypy factory/`: passed across 228 source files.
- Hermetic suite: 5,259 passed, 12 skipped, 25 deselected.
- Unfiltered suite also exposed existing environment-only cases: LegacyBench expects GNU `timeout` on macOS, and marked slow runner tests make real authenticated agent calls.
