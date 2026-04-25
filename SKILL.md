---
name: factory
description: Launch the Factory CEO agent to autonomously evolve any software project through systematic experimentation. Detects project state, spawns specialist agents, runs evals, and archives learnings.
user_invocable: true
---

# Factory Skill v2 — CEO Launcher

This skill spawns the **Factory CEO Agent** — a dedicated autonomous orchestrator that manages the full factory workflow. The CEO delegates all execution to 7 specialist agents: Researcher, Strategist, Builder, Reviewer, Evaluator, Archivist, and (recursively) itself.

**Do not attempt to run the factory workflow yourself.** Delegate entirely to the CEO agent.

## Usage

```bash
# Resolve the factory installation root
FACTORY_HOME="$(uv run python -m factory home)"

# Launch the CEO agent (default: improve mode)
uv run python -m factory ceo "$(pwd)"

# Or specify a mode
uv run python -m factory ceo "$(pwd)" --mode discover   # Auto-detect evals
uv run python -m factory ceo "$(pwd)" --mode improve    # Improvement loop (default)
uv run python -m factory ceo "$(pwd)" --mode meta       # Self-improvement only (ACE)
```

## What the CEO Does

1. **Detects project state** — routes to Build, Discover, Review, or Improve mode
2. **Spawns specialist agents** — each gets fresh context with evolved playbooks auto-injected
3. **Manages experiments** — begin, evaluate, implement, guard, evaluate, keep/revert
4. **Mandatory archival** — Archivist fires at every checkpoint (research, strategy, experiment, final)
5. **Self-learning** — CEO decisions feed ACE playbook evolution for all 7 agent roles

## Alternative Invocations

```bash
# Via factory run (same thing, supports heartbeat loop mode)
uv run python -m factory run /path/to/project --loop --interval 1800

# Via factory agent (invoke any specialist directly)
uv run python -m factory agent researcher --task "Research the project" --project /path

# In a detached tmux session
uv run python -m factory tmux /path/to/project --loop
```

## Agent Roles

| Role       | Prompt                                  | Purpose                              |
|------------|-----------------------------------------|--------------------------------------|
| CEO        | `factory/agents/prompts/ceo.md`         | Orchestrate, decide, delegate        |
| Researcher | `factory/agents/prompts/researcher.md`  | Observe, research, synthesize        |
| Strategist | `factory/agents/prompts/strategist.md`  | Generate prioritized hypotheses      |
| Builder    | `factory/agents/prompts/builder.md`     | Implement changes on feature branch  |
| Reviewer   | `factory/agents/prompts/reviewer.md`    | Guard rules, scope, code quality     |
| Evaluator  | `factory/agents/prompts/evaluator.md`   | Score before/after, interpret        |
| Archivist  | `factory/agents/prompts/archivist.md`   | Record learnings to Obsidian vault   |

All prompts support two-tier override: project-specific (`.factory/agents/<role>.md`) takes precedence over factory default. Evolved playbooks from `~/.factory/playbooks/<role>.md` (user-local, ACE-generated) are auto-injected, falling back to factory defaults in `factory/agents/playbooks/<role>.md`.
