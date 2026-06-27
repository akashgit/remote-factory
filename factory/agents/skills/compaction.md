# /compaction — Context Preservation for CEO Sessions

Use this skill to manage compaction and context loss in long-running CEO sessions.

## Why Compaction Matters

CEO sessions running long `--loop` cycles will hit Claude Code's context compaction. When this happens, the CEO loses track of its strategy, repeats work, or makes contradictory decisions. You are the persistent memory layer — you know what the CEO was doing and can help recover context.

## Checkpoint Before Long Runs

Before dispatching a long `--loop` run, save a recovery point:
```bash
factory checkpoint <project_path>
```
This captures the current strategy state so you can resume if the session crashes.

## Resume from Crashes

If a CEO session dies unexpectedly:
```bash
factory resume <project_path>
```
This restarts from the last checkpoint, preserving strategy and experiment state.

## Context Injection Pattern

When a CEO session has compacted or needs context refreshed, gather and compose state:

1. **Generate fresh observations:**
   ```bash
   factory study <project_path>
   ```

2. **Read current strategy:**
   Read `.factory/strategy/current.md` — contains hypotheses, priorities, and the design space assessment.

3. **Read pending work:**
   Read `.factory/strategy/backlog.md` — items the CEO should be working on.

4. **Read latest agent outputs:**
   Read `.factory/reviews/` — `ceo-latest.md` and other agent review files show what was last attempted.

5. **Compose a summary** of the above and inject it via the CEO's next `--focus` or `--prompt` flag to restore awareness.

## Proactive Monitoring

While CEO runs are active, periodically check on them:

```bash
factory tmux-ls                    # are sessions still running?
factory status <project_path>      # project state and recent activity
factory history <project_path>     # latest experiment outcomes
```

Signs of compaction trouble:
- A CEO cycle takes much longer than usual
- The user reports the CEO seems confused or is repeating work
- History shows consecutive REVERTs with similar hypotheses

When you detect these signals, checkpoint the project, stop the session, and dispatch a fresh CEO with context injected via `--focus` or `--prompt`.
