# /playbook — ACE Playbook Evolution

Use this skill to manage and evolve agent playbooks via the ACE (Automated Capability Evolution) system.

## Trigger Playbook Evolution

```bash
factory ace
```
Evolves all agent playbooks from accumulated experiment data. ACE analyzes experiment outcomes (KEEP vs REVERT), extracts behavioral patterns, and distills them into DO/DON'T rules in each role's playbook.

## Check Evolution Stats

```bash
factory ace-stats
```
Shows which rules were added, removed, or updated in the latest evolution run. Use this to verify that evolution produced sensible changes.

## Read Current Playbooks

Playbooks live at `~/.factory/playbooks/<role>.md` — one per agent role:
- `researcher.md`, `strategist.md`, `builder.md`, `qa.md`
- `archivist.md`, `refiner.md`, `failure_analyst.md`, `ceo.md`

Each playbook contains empirically-derived DO/DON'T rules with helpful/harmful counts. Higher helpful counts indicate stronger confidence in a rule.

## When to Evolve

Trigger `factory ace` when:
- **3+ experiments** have completed across any project since the last evolution
- **Agent mistakes repeat** — you observe the same failure pattern across experiments (e.g., builder keeps making the same type of error)
- **User requests it** — "improve how the builder works", "agents keep doing X wrong"
- **After a meta mode run** — meta mode already runs ACE, but you may want a follow-up evolution after reviewing the results

## Targeted Review for Underperforming Roles

If a specific agent role is underperforming:

1. **Read its playbook:** `~/.factory/playbooks/<role>.md`
2. **Check experiment archives:** Read `.factory/archive/experiments/` in relevant projects for patterns of failure
3. **Read agent outputs:** Check `.factory/reviews/<role>-latest.md` across projects to spot recurring issues
4. **Trigger evolution:** Run `factory ace` — ACE will incorporate the latest experiment data
5. **Verify changes:** Run `factory ace-stats` and read the updated playbook to confirm the new rules address the observed issues

## Manual Playbook Editing

Playbooks are plain markdown. If ACE misses a pattern or you need an immediate fix, you can edit `~/.factory/playbooks/<role>.md` directly. ACE will preserve manual edits on subsequent evolutions as long as the format is maintained.
