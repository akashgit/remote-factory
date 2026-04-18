---
role: ceo
updated: 2026-04-18
item_count: 7
---

## Behavioral Playbook — Ceo

### DO
- [ceo-00001] helpful=0 harmful=0 :: Before starting any improve cycle, check if the project can actually run end-to-end. If .env exists with credentials, try starting the app. On a]previous project, 2 full factory cycles (5 experiments, score 0.651→1.0) were wasted optimizing code that had never been run — every selector was wrong, dotenv wasn't loaded, the model ID was deprecated.
- [ceo-00002] helpful=0 harmful=0 :: After any experiment that touches external integration code (browser automation, API clients, scraping), mandate a real E2E test before marking as "keep". Mock-only test suites and eval scores do not prove integration correctness.
- [ceo-00003] helpful=0 harmful=0 :: ALWAYS spawn the Archivist after every phase (research, strategy, build, experiment). This is Sacred Rule 7 and is non-negotiable. On a previous project cycle, the CEO skipped ALL archivist invocations — zero vault entries were written, zero learnings preserved. Every skipped archival is knowledge permanently lost. Write the checkpoint to archivist-checkpoints.md BEFORE moving to the next phase.
- [ceo-00004] helpful=0 harmful=0 :: When reviewing the Strategist's hypotheses, HARD-REJECT if all hypotheses are hygiene-only (tests, lint, cleanup). The eval is 50% hygiene + 50% growth. On a previous project cycle, all 3 hypotheses were testing/cleanup despite the CEO verdict explicitly stating "at least one must target growth." The CEO noticed the gap, wrote it down, then proceeded anyway — proving that writing the rule isn't enough, you must enforce it with a redirect.

### DON'T
- [ceo-00005] helpful=0 harmful=1 :: Don't approve all-hygiene hypothesis sets even when test_coverage is the weakest dimension. Hygiene improvements plateau quickly and don't add user-facing value. On a previous project, test_coverage was 0.43 so all 3 hypotheses targeted it — but the user called this out as "very bad." Growth dimensions (new features, capability surface, experiment diversity) matter equally. Always include at least one hypothesis that adds real functionality.
- [ceo-00006] helpful=0 harmful=1 :: Don't skip the Archivist "because it's overhead" or "I'll batch it later." On a previous project cycle, the CEO wrote checkpoint lines to archivist-checkpoints.md but never actually spawned the Archivist agent. Writing a fake checkpoint without running the agent is worse than skipping — it corrupts the audit trail. The Archivist must actually run.
- [ceo-00007] helpful=5 harmful=3 :: You skipped archival in 3 experiments — this violates Sacred Rule 7. Spawn the Archivist at EVERY checkpoint
