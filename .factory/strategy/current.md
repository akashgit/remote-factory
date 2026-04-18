## Strategy — 2026-04-18 (Cycle 6 — Fix & Grow)

### Context

Composite score: 0.802. The eval was restructured since Cycle 5 (hygiene dimensions reweighted, growth dimensions merged at 50/50). One dimension is completely broken: config_parser scores 0.0 due to an `asyncio.run()` bug in `eval/score.py`. Capability surface is at 0.604 (169/280 target) — the largest growth gap. The dashboard module (`factory/dashboard/app.py`) has 9 functions with zero logging, dragging observability to 0.783.

CEO priorities: (1) fix config_parser, (2) expand capability surface, (3) instrument or add features.

FEEC classification: H1 is Fix, H2 is Explore, H3 is Exploit.

---

#### H1: Fix config_parser eval — convert to async-aware execution

- **Category:** Fix
- **What:** In `eval/score.py`, change `eval_config_parser()` to `async def eval_config_parser()` and replace `asyncio.run(store.reparse_config())` with `await store.reparse_config()`. Update `main()` to handle mixed sync/async eval functions — use `asyncio.iscoroutinefunction(fn)` to dispatch correctly, then wrap `main()` itself with `asyncio.run()` at the `__main__` entrypoint. The eval threshold check should also be updated: factory.md says `0.8` and config has `0.8`, but the check hardcodes `0.8` which is correct — just verify it parses after the async fix.
- **Why:** config_parser is scoring 0.0 because `asyncio.run()` crashes when called inside an already-running event loop (the factory's eval runner is async). This is the only dimension at 0.0 — a complete floor. The research report diagnosed this as a 30-minute fix and the CEO flagged it as CRITICAL priority #1. Every composite score since the restructure has been suppressed by this dead dimension.
- **Expected impact:** config_parser 0.0 -> 1.0 (weight 0.05), composite +0.05 (0.802 -> ~0.852). Unblocks accurate composite measurement for all subsequent experiments.
- **Priority:** high
- **Scope:**
  - `eval/score.py` (convert eval_config_parser to async, update main to handle async evals)

---

#### H2: Add `factory export` command — portable project snapshot

- **Category:** Explore
- **What:** Add a new `factory export` CLI command that dumps a complete project snapshot as a single JSON file to stdout. The export includes: config, eval profile, experiment history, latest eval scores, strategy, and cross-project insights summary. This is a new capability — not duplicating any existing command. Implementation: add `cmd_export()` in `factory/cli.py`, register in the handler dict and argparse subcommands. The function reads from `ExperimentStore` and assembles the snapshot. Add tests in `tests/test_cli_export.py`.
- **Growth dimension:** capability_surface
- **Why:** Capability surface is at 0.604 (169/280). Each new CLI command adds 1 to the surface count. More importantly, `export` enables new workflows: sharing factory state between machines, feeding snapshots to other agents, and archiving complete project state outside of git. The research report identified output format expansion (JSON/YAML/CSV) as a high-impact gap. This is scoped to one PR — a single new command with tests, no architectural changes.
- **Expected impact:** capability_surface improves by +1 command (169->170 surface, marginal score bump ~0.004). But the real value is enabling downstream features (import, compare, MCP server) that each add more surface. Coverage improves slightly from new test file.
- **Priority:** medium
- **Scope:**
  - `factory/cli.py` (add cmd_export, register in handlers + argparse)
  - `tests/test_cli_export.py` (new — test export output format, missing .factory handling)

---

#### H3: Add structured logging to dashboard module

- **Category:** Exploit
- **What:** Add `structlog` logging to all 9 functions in `factory/dashboard/app.py`: `create_app`, `_sse_generator`, `_project_summary`, `_load_tsv`, and the 5 FastAPI route handlers (index, list_projects, project_history, project_events, event_stream). Log: endpoint hits with project names, SSE client connections/disconnections, project scan counts, TSV parse errors, and file I/O failures. Follow existing patterns from `factory/store.py` — `log = structlog.get_logger()` at module level, structured key-value pairs.
- **Growth dimension:** observability
- **Why:** The dashboard module has 9 functions and 0 log statements — it is the single largest uninstrumented module. Research report identifies 46% function coverage as the gap. Adding logging here raises the instrumented function count from 79 to 88 (79/173 -> 88/173 = 51%), improving the observability score. This is Exploit because we are applying a proven pattern (structlog instrumentation, which worked in experiments #27, #30) to an uninstrumented module.
- **Expected impact:** observability 0.783 -> ~0.83 (9 more functions instrumented, ~18 new log statements). Composite +0.005. Also improves debugging of dashboard issues (SSE disconnects, missing projects, TSV parse errors) which have been reported in user sessions.
- **Priority:** medium
- **Scope:**
  - `factory/dashboard/app.py` (add structlog to all 9 functions)

---

### Execution Order

1. **H1** first (Fix priority, highest impact, unblocks accurate scoring)
2. **H2** second (new capability, growth dimension target)
3. **H3** third (exploit proven pattern, quick win)

### Anti-patterns to Avoid
- Don't use `nest_asyncio` as a workaround for the async bug — use proper async/await (research recommendation)
- Don't add excessive logging to pure data models (lesson from Cycle 5)
- Don't make `export` too ambitious (no import, no format options yet — just JSON stdout)
