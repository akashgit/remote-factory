# Hook-Driven Checkpointing Design

**Date:** 2026-04-29
**Status:** Draft
**Supersedes:** Prompt-based checkpoint saving from 2026-04-26 spec (loading/resume flow unchanged)

## Problem

The existing checkpoint system relies on the CEO LLM agent executing `factory checkpoint --save` shell commands embedded in its prompt. Live testing proved this is completely unreliable — the CEO never executes these commands. Over a full Research → Strategy → Builder → Eval cycle, zero checkpoint.json files were written.

The checkpoint *infrastructure* works (load, format, clear, resume context injection), but the *saving* mechanism is broken because it depends on LLM compliance.

## Goal

Achieve reliable task/workflow resumption: if a factory session is killed mid-run, the next `factory ceo` or `factory run` invocation automatically resumes from the last known state. Checkpoints must be saved deterministically by infrastructure, not by LLM behavior.

## Design

### Architecture: Two-Layer Checkpoint Saving

**Layer 1 — Hooks (intra-CEO-session):** Claude Code hooks fire deterministically after tool calls, on session end, and on agent completion. A hook command runs a Python script that reconstructs the current state from disk artifacts and writes `checkpoint.json`.

**Layer 2 — Python infra (inter-cycle):** The `cmd_run --loop` heartbeat loop saves a checkpoint between cycles. This covers the case where the CEO subprocess crashes hard (SIGKILL) before hooks can fire.

### Component 1: Checkpoint Reconstruction Script

**File:** `factory/checkpoint_hook.py`

A standalone script that reconstructs `CheckpointState` from disk truth sources. Called by hooks and by the Python infra. Idempotent — calling it N times produces the same result.

**Input:** Project path (positional arg) + optional JSON on stdin (hook context from Claude Code).

**Reconstruction logic:**

1. Read `events.jsonl` — parse `agent.started`, `agent.completed`, `eval.started`, `eval.completed`, `experiment.begin`, `experiment.finalize` events since the last `cycle.started` event (or session start).
2. Read `reviews/*-latest.md` — confirm which agent roles have completed (researcher, strategist, builder, evaluator).
3. Read `reviews/archivist-checkpoints.md` — confirm archivist ran for each phase.
4. Read `experiments/*/verdict.json` — identify completed hypotheses (have verdict) vs active experiment (hypothesis.md exists, no verdict.json).
5. Read `strategy/current.md` — confirm strategy phase complete.
6. Read `results.tsv` — get latest eval scores and experiment history.
7. Detect mode from `.factory/config.json` or existing checkpoint.

**Output:** Writes `.factory/checkpoint.json` via `save_checkpoint()`.

**Performance:** The script reads only the tail of `events.jsonl` (from last `cycle.started`), not the entire file. For a typical cycle with <50 events, this takes <100ms.

**Filtering:** When called from a `PostToolUse` hook, the script receives `tool_input.command` on stdin. It checks if the command matches a factory CLI pattern (`factory agent|eval|begin|finalize|guard|precheck`). For non-factory commands, it exits 0 immediately without reading any files. This avoids unnecessary work on every `ls` or `cat`.

### Component 2: Hook Configuration

**File:** `.claude/settings.json` (project-level, committed to the factory repo)

**Important:** `$CLAUDE_PROJECT_DIR` points to the *factory* repo, not the target project. The CEO runs with `cwd` set to the target project (via `os.chdir(project_path)` in `cmd_ceo`), so hooks use `$(pwd)` to get the target project path. The hooks call the factory's script by absolute path using `$CLAUDE_PROJECT_DIR`.

Two thin shell wrappers handle the hook logic:

**File:** `.claude/hooks/save-checkpoint.sh`
```bash
#!/bin/bash
# Called by Claude Code hooks to save checkpoint state.
# Uses cwd (the target project), not CLAUDE_PROJECT_DIR (the factory repo).
TARGET_DIR="$(pwd)"
[ -d "$TARGET_DIR/.factory" ] || exit 0  # Not a factory-managed project
uv run python -m factory.checkpoint_hook "$TARGET_DIR" 2>/dev/null
exit 0  # Never block the CEO
```

**File:** `.claude/hooks/save-checkpoint-filtered.sh`
```bash
#!/bin/bash
# PostToolUse variant: only checkpoints after factory CLI commands.
TARGET_DIR="$(pwd)"
[ -d "$TARGET_DIR/.factory" ] || exit 0
INPUT=$(cat)
CMD=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")
if echo "$CMD" | grep -qE "factory (agent|eval|begin|finalize|guard|precheck)"; then
    uv run python -m factory.checkpoint_hook "$TARGET_DIR" 2>/dev/null
fi
exit 0
```

**Revised settings.json:**
```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": ""$CLAUDE_PROJECT_DIR"/.claude/hooks/save-checkpoint-filtered.sh",
            "timeout": 10
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": ""$CLAUDE_PROJECT_DIR"/.claude/hooks/save-checkpoint.sh",
            "timeout": 10,
            "statusMessage": "Saving checkpoint..."
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": ""$CLAUDE_PROJECT_DIR"/.claude/hooks/save-checkpoint.sh",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

### Component 3: Loop-Level Checkpoint Saving

**File:** `factory/cli.py` — `cmd_run()` function

After each cycle completes (success or failure), save a checkpoint before sleeping:

```python
# In the heartbeat loop, after _run_single_cycle returns:
from factory.checkpoint_hook import reconstruct_and_save
reconstruct_and_save(project_path)
```

This provides a safety net for hard kills (SIGKILL) that prevent hooks from firing. The checkpoint reflects the state at the end of the last completed cycle.

### Component 4: CEO Prompt Changes

**File:** `factory/agents/prompts/ceo.md`

**Remove:** All `factory checkpoint "$PROJECT_PATH" --save` and `factory checkpoint "$PROJECT_PATH" --clear` instructions from the Improve mode flow (4 locations: after Research, after Strategy, after each hypothesis, after Final Archive).

**Keep:** The "Resuming from a Crash" section (lines 124-135) — the CEO still needs to know how to read and act on `## Resume Context`.

**Add:** A note explaining that checkpoints are saved automatically by infrastructure hooks:
```markdown
> **Note:** Checkpoint saving is handled automatically by infrastructure hooks.
> You do not need to run `factory checkpoint --save` or `--clear`.
> Focus on the workflow — checkpoints are saved after each phase and cleared on success.
```

### Component 5: Resume Flow (Unchanged)

The existing resume flow is proven to work:
1. `_run_single_cycle` / `cmd_ceo` calls `load_checkpoint(project_path)` on startup
2. If found, appends `## Resume Context\n\n{format_checkpoint(state)}` to the CEO task
3. CEO reads the resume context and skips completed phases
4. On success (`code == 0`), `clear_checkpoint()` deletes the file

No changes required.

## What Survives a Crash

| Artifact | Mechanism | Survives SIGTERM? | Survives SIGKILL? |
|----------|-----------|-------------------|-------------------|
| `checkpoint.json` | Hooks (Stop, SessionEnd) | Yes | No |
| `checkpoint.json` | Loop-level save (cmd_run) | Yes (between cycles) | Yes (from previous cycle) |
| `events.jsonl` | Python infra (append-only) | Yes | Partial (last line may truncate) |
| `strategy/current.md` | CEO file writes | Yes | Partial |
| `reviews/*-latest.md` | Agent runner stdout capture | Yes | Yes (written atomically per agent) |
| `experiments/*/verdict.json` | ExperimentStore.finalize() | Yes | Partial |
| `results.tsv` | ExperimentStore.finalize() | Yes | Partial |

## Out of Scope

- **Atomic file writes** for `results.tsv` / eval JSON (separate concern, not checkpoint-specific)
- **Orphan process cleanup** (requires process group management, separate PR)
- **File locking** for concurrent access (separate concern)
- **Cost budget checkpointing** (low priority, separate feature)
- **Build/Discover mode-specific resume logic in CEO prompt** (the checkpoint captures state for all modes; resume instructions can be extended later)

## Testing Plan

1. **Unit tests for `checkpoint_hook.py`:** Given various `events.jsonl` / reviews / experiments states, verify correct `CheckpointState` reconstruction.
2. **Integration test:** Start a factory run, verify hooks write `checkpoint.json` after each phase, kill, restart, verify resume.
3. **Edge cases:** Empty events.jsonl, truncated events, missing reviews, corrupt files — all should produce best-effort checkpoints or graceful no-ops.
4. **Hook firing verification:** Use `/hooks` menu to verify hooks are configured, then check `checkpoint.json` timestamps during a live run.
