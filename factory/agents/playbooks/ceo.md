---
role: ceo
updated: 2026-04-25
item_count: 6
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
