---
role: archivist
updated: 2026-04-20
item_count: 3
---

## Behavioral Playbook — Archivist

### DO
- [arch-00001] helpful=0 harmful=0 :: Archival compliance is strong — 5 experiments properly recorded. Continue recording at all checkpoints

### DON'T
- [arch-00002] helpful=0 harmful=0 :: NEVER write to the user's personal Obsidian vault at the user's personal Obsidian vault path. The factory vault is ~/factory-vault/ — a completely separate vault. On a previous project cycle, the Archivist wrote 6 experiment/strategy notes into the user's personal Ideas/ folder and polluted Ideas.md and the Factory idea note. The user's vault is their personal knowledge base — factory experiment data does not belong there.
- [arch-00003] helpful=0 harmful=0 :: When falling back from obsidian-cli to direct file writes, double-check the target path starts with ~/factory-vault/. The vault confusion on cycle 7 likely happened because the agent saw the personal vault path in global CLAUDE.md and defaulted to it. Always verify the destination before writing.
