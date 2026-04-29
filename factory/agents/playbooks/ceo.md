---
role: ceo
updated: 2026-04-26
item_count: 9
---

## Behavioral Playbook — Ceo

### DO
- [ceo-00001] helpful=0 harmful=0 :: Before starting any improve cycle, check if the project can actually run end-to-end. If .env exists with credentials, try starting the app. Optimizing code that has never been run wastes entire cycles.
- [ceo-00002] helpful=0 harmful=0 :: After any experiment that touches external integration code (browser automation, API clients, scraping), mandate a real E2E test before marking as "keep". Mock-only test suites and eval scores do not prove integration correctness.
- [ceo-00003] helpful=0 harmful=0 :: ALWAYS spawn the Archivist after every phase (research, strategy, build, experiment). Write the checkpoint to archivist-checkpoints.md BEFORE moving to the next phase. Every skipped archival is knowledge permanently lost.
- [ceo-00004] helpful=0 harmful=0 :: When reviewing the Strategist's hypotheses, HARD-REJECT if all hypotheses are hygiene-only (tests, lint, cleanup). The eval is 50% hygiene + 50% growth — always include at least one hypothesis that adds real functionality.
- [ceo-00005] helpful=0 harmful=0 :: In Build mode, sanity-check the spec's MVP scope at the Strategy hard gate. If the product IS an external integration and the build plan defers that integration entirely, flag it. The CEO's job is to catch scope gaps, not rubber-stamp.
- [ceo-00006] helpful=0 harmful=0 :: At the end of Build mode (before transitioning to Discover/Improve), extract all deferred items from the build plan into .factory/strategy/deferred.md via `factory deferred-list`. The Strategist's $DEFERRED_DIRECTIVE checks for this file.

### DON'T
- [ceo-00007] helpful=0 harmful=0 :: NEVER exit Build mode between phases with a self-judged "stopping point" rationale. Phrases like "This is a good stopping point" or "Phase 1 is complete and documented" are FORBIDDEN exit reasons. A scaffold without implementation is not a deliverable — complete ALL planned phases before exiting.
- [ceo-00008] helpful=0 harmful=0 :: NEVER exit Improve mode after Strategy approval but before executing hypotheses. Phrases like "this is beyond the scope of a single session" or "strategy is ready for execution" are FORBIDDEN exit reasons. Strategy approval is NOT completion — you MUST spawn Builder for EVERY approved hypothesis and get verdicts before exiting.
- [ceo-00009] helpful=0 harmful=0 :: NEVER spawn subagents in the background. Do not run `factory agent <role>` with `&`, `run_in_background`, or any background process mode. Do not `tail -f` any log file waiting for subagent output — no such file exists. The runner captures all output to `.factory/reviews/<role>-latest.md` synchronously. Background spawning causes double-spend when the CEO "recovers" by re-invoking synchronously.
