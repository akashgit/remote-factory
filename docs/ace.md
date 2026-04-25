# ACE Self-Improvement

ACE (Autonomous Context Engineering) is the Factory's self-improvement loop. It evolves the agent playbooks — behavioral rules that guide each specialist agent — based on real experiment outcomes.

## How It Works

```
Experiment outcomes     Reflect        Curate         Inject
(results.tsv)      ──────────▶   ──────────▶   ──────────▶  Agent prompts
across all projects   Generate      Merge &       Auto-append
                      candidate     prune         at runtime
                      bullets       playbooks
```

### 1. Reflect (`factory/ace/reflector.py`)

Analyzes experiment outcomes across all factory-managed projects:
- Computes category success rates (which types of changes get kept vs reverted)
- Generates candidate playbook bullets for all 7 agent roles
- Each bullet is a behavioral rule: DO (reinforced pattern) or DON'T (anti-pattern)

### 2. Curate (`factory/ace/curator.py`)

Merges candidate bullets with existing playbooks:
- Deduplicates similar rules
- Increments helpful/harmful counters on existing bullets
- Prunes low-value bullets (low net score)
- Caps playbook size to prevent unbounded growth

### 3. Inject (`factory/ace/injector.py`)

At runtime, when an agent is spawned, evolved playbooks are automatically appended to the agent's prompt. This happens transparently in `factory/agents/runner.py`.

## Playbook Format

Factory ships clean default playbooks in `factory/agents/playbooks/<role>.md`. When ACE evolves playbooks from your experiment data, it writes to `~/.factory/playbooks/<role>.md` (user-local). The injector checks user-local first, then falls back to factory defaults. Your evolved playbooks are never committed to the factory repo — they're personal to your experiment history.

Example format:

```markdown
---
role: builder
updated: 2026-04-22
item_count: 5
---

## Behavioral Playbook — Builder

### DO
- [build-00001] helpful=12 harmful=1 :: Always run ruff + mypy after making changes

### DON'T
- [build-00002] helpful=3 harmful=0 :: Don't add type: ignore comments — fix the actual type error
```

Each bullet tracks:
- **ID**: Unique identifier (e.g. `build-00001`)
- **helpful/harmful counters**: How many times this rule correlated with kept vs reverted experiments
- **Net score**: `helpful - harmful` — rules with negative net scores get pruned

## Running ACE

```bash
# Run ACE on a specific project
factory ace ~/my-project

# Run ACE as part of meta mode (includes full improve cycle first)
factory ceo ~/my-project --mode meta
```

Meta mode runs the full improvement loop, then reflects on the outcomes to evolve all 7 agent playbooks. See [Self-Improvement Loop](self-improvement.md) for the full picture — including cross-project learning, CEO self-evaluation, and how the pieces fit together.

## What Gets Evolved

All 7 agent roles have playbooks:

| Role | What ACE learns |
|------|----------------|
| CEO | Keep/revert decision patterns, when to trust eval scores |
| Researcher | Which research approaches produce actionable insights |
| Strategist | Which hypothesis categories succeed in which contexts |
| Builder | Implementation patterns that pass review, common pitfalls |
| Reviewer | What to focus on in code review, false positive patterns |
| Evaluator | Score interpretation, when to flag anomalies |
| Archivist | What to record, vault organization patterns |

## Design Principles

- **Evidence-based**: Every playbook bullet is derived from real experiment outcomes, not hand-written rules
- **Self-correcting**: If a rule leads to reverted experiments, its harmful counter increases until it's pruned
- **Bounded**: Playbook size is capped to prevent prompt bloat
- **Transparent**: Playbooks are human-readable markdown — you can read, edit, or override them
- **Cross-project**: Learnings from one project inform behavior on others
