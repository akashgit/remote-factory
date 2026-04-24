---
role: archivist
updated: 2026-04-22
item_count: 3
---

## Behavioral Playbook — Archivist

### DO
- [arch-00001] helpful=33 harmful=0 :: Archival compliance is strong — 5 experiments properly recorded. Continue recording at all checkpoints

### DON'T
- [arch-00002] helpful=1 harmful=0 :: NEVER write to the user's personal Obsidian vault. The factory vault is at $FACTORY_VAULT_PATH (default ~/obsidian-vaults/factory/) — a completely separate vault. On a previous project cycle, the Archivist wrote experiment notes into the user's personal vault. Always verify the destination matches $FACTORY_VAULT_PATH before writing.
- [arch-00003] helpful=1 harmful=0 :: When falling back from obsidian-cli to direct file writes, double-check the target path starts with $FACTORY_VAULT_PATH. The vault confusion on cycle 7 likely happened because the agent saw a personal vault path and defaulted to it. Always verify the destination before writing.
