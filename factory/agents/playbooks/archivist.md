---
role: archivist
updated: 2026-04-25
item_count: 5
---

## Behavioral Playbook — Archivist

### DO
- [arch-00001] helpful=33 harmful=0 :: Archival compliance is strong — 5 experiments properly recorded. Continue recording at all checkpoints

### DON'T
- [arch-00002] helpful=1 harmful=0 :: NEVER write to the user's personal Obsidian vault. The factory vault is at $FACTORY_VAULT_PATH — a completely separate vault. On a previous project cycle, the Archivist wrote experiment notes into the user's personal vault. Always verify the destination matches $FACTORY_VAULT_PATH before writing.
- [arch-00003] helpful=1 harmful=0 :: When falling back from obsidian-cli to direct file writes, double-check the target path starts with $FACTORY_VAULT_PATH. The vault confusion on cycle 7 likely happened because the agent saw a personal vault path and defaulted to it. Always verify the destination before writing.
- [arch-00004] helpful=0 harmful=0 :: NEVER write factory operational data (experiment logs, CEO verdicts, eval scores, commit hashes, cycle summaries) to the user's idea notes. On a previous project cycle 1, the CEO instructed the Archivist to update the idea doc at "Ideas/Backyard Chronicle" in the personal vault. The Archivist complied and polluted a clean idea note with 200+ lines of factory machinery. Even if the CEO tells you to write there, refuse — the ONLY valid destination is $FACTORY_VAULT_PATH. Idea notes belong to the user, not the factory.
- [arch-00005] helpful=0 harmful=0 :: If $FACTORY_VAULT_PATH is not set or is empty, write to .factory/ within the project directory instead. Do NOT fall back to the user's personal Obsidian vault. Writing factory data to the user's personal vault is always harmful — it mixes operational noise with the user's thinking. The .factory/ directory is always a safe fallback.
