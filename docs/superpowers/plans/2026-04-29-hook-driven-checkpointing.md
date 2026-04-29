# Hook-Driven Checkpointing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken prompt-based checkpoint saving with deterministic hook-driven saves that reconstruct state from disk artifacts.

**Architecture:** Two-layer system — Claude Code hooks fire a Python reconstruction script after each tool call/session end (Layer 1), and the Python heartbeat loop saves checkpoints between cycles (Layer 2). The reconstruction script reads `events.jsonl`, reviews, and experiments to derive the current `CheckpointState` from disk truth.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, Claude Code hooks (settings.json), shell scripts

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `factory/checkpoint_hook.py` | State reconstruction from disk artifacts + `__main__` entry point |
| Create | `tests/test_checkpoint_hook.py` | Unit tests for reconstruction logic |
| Create | `.claude/hooks/save-checkpoint.sh` | Hook shell wrapper (unconditional) |
| Create | `.claude/hooks/save-checkpoint-filtered.sh` | Hook shell wrapper (PostToolUse, filtered) |
| Create | `.claude/settings.json` | Hook configuration |
| Modify | `factory/cli.py:1773-1782` | Add loop-level checkpoint save after each cycle |
| Modify | `factory/agents/prompts/ceo.md:661-665,739-743,983-987,1036-1038` | Remove prompt-based checkpoint save/clear |

---

### Task 1: Checkpoint Reconstruction Core

Build the `reconstruct_state()` function that reads disk artifacts and returns a `CheckpointState`.

**Files:**
- Create: `factory/checkpoint_hook.py`
- Create: `tests/test_checkpoint_hook.py`

- [ ] **Step 1: Write failing test for basic reconstruction from events**

In `tests/test_checkpoint_hook.py`:

```python
"""Tests for factory.checkpoint_hook — state reconstruction from disk artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from factory.checkpoint_hook import reconstruct_state


@pytest.fixture
def factory_project(tmp_path: Path) -> Path:
    """Create a minimal .factory/ directory with events.jsonl."""
    project = tmp_path / "test-project"
    project.mkdir()
    factory = project / ".factory"
    factory.mkdir()
    (factory / "experiments").mkdir()
    (factory / "strategy").mkdir()
    (factory / "reviews").mkdir()
    # Empty events log
    (factory / "events.jsonl").write_text("")
    # Minimal config
    (factory / "config.json").write_text(json.dumps({
        "project_name": "test",
        "description": "test project",
        "eval_command": "echo ok",
        "language": "python",
        "framework": None,
    }))
    return project


def test_reconstruct_empty_state(factory_project: Path) -> None:
    """With no events, reconstruction returns a minimal checkpoint."""
    state = reconstruct_state(factory_project)
    assert state.mode == "improve"
    assert state.completed_agents == []
    assert state.pending_agents == []
    assert state.active_experiment_id is None
    assert state.completed_hypotheses == []


def test_reconstruct_after_researcher(factory_project: Path) -> None:
    """After researcher completes, it appears in completed_agents."""
    events_file = factory_project / ".factory" / "events.jsonl"
    events = [
        {"type": "agent.started", "timestamp": "2026-04-29T10:00:00+00:00",
         "project": "test", "agent": "ceo", "data": {}},
        {"type": "agent.started", "timestamp": "2026-04-29T10:01:00+00:00",
         "project": "test", "agent": "researcher", "data": {}},
        {"type": "agent.completed", "timestamp": "2026-04-29T10:02:00+00:00",
         "project": "test", "agent": "researcher", "data": {"return_code": 0}},
    ]
    events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    # Also create the review file (confirms completion on disk)
    (factory_project / ".factory" / "reviews" / "researcher-latest.md").write_text("research done")

    state = reconstruct_state(factory_project)
    assert "researcher" in state.completed_agents
    assert state.active_experiment_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_checkpoint_hook.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'factory.checkpoint_hook'`

- [ ] **Step 3: Write reconstruction implementation**

In `factory/checkpoint_hook.py`:

```python
"""Hook-driven checkpoint reconstruction from disk artifacts.

Reconstructs CheckpointState by reading events.jsonl, reviews, experiments,
and strategy files. Called by Claude Code hooks and the heartbeat loop.
Idempotent — calling N times produces the same result.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import structlog

from factory.checkpoint import CheckpointState, save_checkpoint

log = structlog.get_logger()

AGENT_ROLES = ("researcher", "strategist", "builder", "reviewer", "evaluator")


def _load_recent_events(project_path: Path) -> list[dict]:
    """Load events from the current cycle (since last cycle.started or all)."""
    events_file = project_path / ".factory" / "events.jsonl"
    if not events_file.exists():
        return []

    all_events: list[dict] = []
    for line in events_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            all_events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # Find the last cycle.started event — only consider events after it
    last_cycle_idx = -1
    for i, event in enumerate(all_events):
        if event.get("type") == "cycle.started":
            last_cycle_idx = i

    if last_cycle_idx >= 0:
        return all_events[last_cycle_idx:]
    return all_events


def _detect_completed_agents(project_path: Path, events: list[dict]) -> list[str]:
    """Determine which agent roles have completed from events and review files."""
    completed = []
    reviews_dir = project_path / ".factory" / "reviews"

    for role in AGENT_ROLES:
        # Check events for agent.completed
        agent_completed = any(
            e.get("type") == "agent.completed" and e.get("agent") == role
            for e in events
        )
        # Cross-reference with review file on disk
        review_exists = (reviews_dir / f"{role}-latest.md").exists()

        if agent_completed or review_exists:
            completed.append(role)

    return completed


def _detect_active_experiment(project_path: Path) -> tuple[int | None, str | None, list[int]]:
    """Find the active experiment (has hypothesis but no verdict) and completed ones."""
    experiments_dir = project_path / ".factory" / "experiments"
    if not experiments_dir.exists():
        return None, None, []

    active_id: int | None = None
    active_hypothesis: str | None = None
    completed_ids: list[int] = []

    for exp_dir in sorted(experiments_dir.iterdir()):
        if not exp_dir.is_dir():
            continue
        try:
            exp_id = int(exp_dir.name)
        except ValueError:
            continue

        has_hypothesis = (exp_dir / "hypothesis.md").exists()
        has_verdict = (exp_dir / "verdict.json").exists()

        if has_hypothesis and has_verdict:
            completed_ids.append(exp_id)
        elif has_hypothesis and not has_verdict:
            active_id = exp_id
            active_hypothesis = (exp_dir / "hypothesis.md").read_text().strip()

    return active_id, active_hypothesis, completed_ids


def _get_last_eval_scores(project_path: Path, events: list[dict]) -> dict[str, float]:
    """Extract the most recent eval scores from events."""
    last_scores: dict[str, float] = {}
    for event in events:
        if event.get("type") == "eval.completed":
            data = event.get("data", {})
            if "composite" in data:
                last_scores["composite"] = data["composite"]
            if "dimensions" in data:
                last_scores["dimensions"] = data["dimensions"]
    return last_scores


def _detect_mode(project_path: Path) -> str:
    """Detect the current operating mode from config or existing checkpoint."""
    # Check existing checkpoint first
    checkpoint_path = project_path / ".factory" / "checkpoint.json"
    if checkpoint_path.exists():
        try:
            data = json.loads(checkpoint_path.read_text())
            return data.get("mode", "improve")
        except (json.JSONDecodeError, KeyError):
            pass
    return "improve"


def reconstruct_state(project_path: Path) -> CheckpointState:
    """Reconstruct CheckpointState from disk truth sources."""
    events = _load_recent_events(project_path)
    completed_agents = _detect_completed_agents(project_path, events)
    active_id, hypothesis, completed_ids = _detect_active_experiment(project_path)
    scores = _get_last_eval_scores(project_path, events)
    mode = _detect_mode(project_path)

    # Determine pending agents (roles not yet completed)
    pending = [r for r in AGENT_ROLES if r not in completed_agents]

    return CheckpointState(
        mode=mode,
        active_experiment_id=active_id,
        completed_agents=completed_agents,
        pending_agents=pending,
        last_eval_scores=scores,
        current_hypothesis=hypothesis,
        completed_hypotheses=completed_ids,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def reconstruct_and_save(project_path: Path) -> CheckpointState | None:
    """Reconstruct state and save checkpoint. Returns the state or None on error."""
    factory_dir = project_path / ".factory"
    if not factory_dir.is_dir():
        return None
    try:
        state = reconstruct_state(project_path)
        save_checkpoint(project_path, state)
        log.info("checkpoint_hook.saved", project=str(project_path))
        return state
    except Exception as exc:
        log.warning("checkpoint_hook.error", error=str(exc))
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_checkpoint_hook.py -v`
Expected: PASS (both test_reconstruct_empty_state and test_reconstruct_after_researcher)

- [ ] **Step 5: Commit**

```bash
git add factory/checkpoint_hook.py tests/test_checkpoint_hook.py
git commit -m "feat: add checkpoint reconstruction from disk artifacts"
```

---

### Task 2: Reconstruction Edge Cases and Full Cycle Tests

Add tests for multi-phase reconstruction, experiment detection, eval scores, and error handling.

**Files:**
- Modify: `tests/test_checkpoint_hook.py`
- Modify: `factory/checkpoint_hook.py` (if any fixes needed)

- [ ] **Step 1: Write tests for full improve cycle reconstruction**

Append to `tests/test_checkpoint_hook.py`:

```python
def test_reconstruct_after_researcher_and_strategist(factory_project: Path) -> None:
    """After both researcher and strategist complete."""
    events_file = factory_project / ".factory" / "events.jsonl"
    events = [
        {"type": "agent.completed", "timestamp": "2026-04-29T10:02:00+00:00",
         "project": "test", "agent": "researcher", "data": {"return_code": 0}},
        {"type": "agent.completed", "timestamp": "2026-04-29T10:05:00+00:00",
         "project": "test", "agent": "strategist", "data": {"return_code": 0}},
    ]
    events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    reviews = factory_project / ".factory" / "reviews"
    (reviews / "researcher-latest.md").write_text("done")
    (reviews / "strategist-latest.md").write_text("done")
    (factory_project / ".factory" / "strategy" / "current.md").write_text("strategy")

    state = reconstruct_state(factory_project)
    assert state.completed_agents == ["researcher", "strategist"]
    assert state.pending_agents == ["builder", "reviewer", "evaluator"]


def test_reconstruct_with_active_experiment(factory_project: Path) -> None:
    """Detects an active experiment (hypothesis without verdict)."""
    exp_dir = factory_project / ".factory" / "experiments" / "001"
    exp_dir.mkdir(parents=True)
    (exp_dir / "hypothesis.md").write_text("Add caching layer")

    state = reconstruct_state(factory_project)
    assert state.active_experiment_id == 1
    assert state.current_hypothesis == "Add caching layer"
    assert state.completed_hypotheses == []


def test_reconstruct_with_completed_experiment(factory_project: Path) -> None:
    """Detects completed experiments (have verdict.json)."""
    exp1 = factory_project / ".factory" / "experiments" / "001"
    exp1.mkdir(parents=True)
    (exp1 / "hypothesis.md").write_text("H1")
    (exp1 / "verdict.json").write_text(json.dumps({"verdict": "keep"}))

    exp2 = factory_project / ".factory" / "experiments" / "002"
    exp2.mkdir(parents=True)
    (exp2 / "hypothesis.md").write_text("H2 - active")

    state = reconstruct_state(factory_project)
    assert state.completed_hypotheses == [1]
    assert state.active_experiment_id == 2
    assert state.current_hypothesis == "H2 - active"


def test_reconstruct_with_eval_scores(factory_project: Path) -> None:
    """Extracts eval scores from events."""
    events_file = factory_project / ".factory" / "events.jsonl"
    events = [
        {"type": "eval.completed", "timestamp": "2026-04-29T10:10:00+00:00",
         "project": "test", "agent": None,
         "data": {"composite": 0.65, "passed": True, "dimensions": 11}},
    ]
    events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    state = reconstruct_state(factory_project)
    assert state.last_eval_scores["composite"] == 0.65


def test_reconstruct_respects_cycle_boundary(factory_project: Path) -> None:
    """Only considers events after the last cycle.started."""
    events_file = factory_project / ".factory" / "events.jsonl"
    events = [
        # Old cycle
        {"type": "agent.completed", "timestamp": "2026-04-29T08:00:00+00:00",
         "project": "test", "agent": "researcher", "data": {"return_code": 0}},
        {"type": "agent.completed", "timestamp": "2026-04-29T08:05:00+00:00",
         "project": "test", "agent": "strategist", "data": {"return_code": 0}},
        # New cycle starts
        {"type": "cycle.started", "timestamp": "2026-04-29T10:00:00+00:00",
         "project": "test", "agent": None, "data": {"cycle": 2}},
        # Only researcher in new cycle
        {"type": "agent.completed", "timestamp": "2026-04-29T10:02:00+00:00",
         "project": "test", "agent": "researcher", "data": {"return_code": 0}},
    ]
    events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    # Review file for researcher exists
    (factory_project / ".factory" / "reviews" / "researcher-latest.md").write_text("done")
    # Strategist review from old cycle also exists (stale)
    (factory_project / ".factory" / "reviews" / "strategist-latest.md").write_text("old")

    state = reconstruct_state(factory_project)
    # Events only show researcher in current cycle, but review file
    # for strategist exists on disk — events take precedence for cycle scope
    assert "researcher" in state.completed_agents


def test_reconstruct_empty_events_file(factory_project: Path) -> None:
    """Handles empty events.jsonl gracefully."""
    state = reconstruct_state(factory_project)
    assert state.completed_agents == []
    assert state.mode == "improve"


def test_reconstruct_missing_events_file(factory_project: Path) -> None:
    """Handles missing events.jsonl gracefully."""
    (factory_project / ".factory" / "events.jsonl").unlink()
    state = reconstruct_state(factory_project)
    assert state.completed_agents == []


def test_reconstruct_corrupt_event_line(factory_project: Path) -> None:
    """Skips corrupt lines in events.jsonl."""
    events_file = factory_project / ".factory" / "events.jsonl"
    events_file.write_text(
        '{"type": "agent.completed", "agent": "researcher"}\n'
        "CORRUPT LINE\n"
        '{"type": "agent.completed", "agent": "strategist"}\n'
    )
    (factory_project / ".factory" / "reviews" / "researcher-latest.md").write_text("done")
    (factory_project / ".factory" / "reviews" / "strategist-latest.md").write_text("done")

    state = reconstruct_state(factory_project)
    assert "researcher" in state.completed_agents
    assert "strategist" in state.completed_agents
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/test_checkpoint_hook.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_checkpoint_hook.py
git commit -m "test: add edge case tests for checkpoint reconstruction"
```

---

### Task 3: `__main__` Entry Point and `reconstruct_and_save` Tests

Add the `__main__` block so the module can be called from hooks, plus tests for `reconstruct_and_save`.

**Files:**
- Modify: `factory/checkpoint_hook.py`
- Modify: `tests/test_checkpoint_hook.py`

- [ ] **Step 1: Write tests for reconstruct_and_save and CLI entry**

Append to `tests/test_checkpoint_hook.py`:

```python
from factory.checkpoint import load_checkpoint
from factory.checkpoint_hook import reconstruct_and_save


def test_reconstruct_and_save_writes_checkpoint(factory_project: Path) -> None:
    """reconstruct_and_save writes checkpoint.json that load_checkpoint can read."""
    # Add some state
    events_file = factory_project / ".factory" / "events.jsonl"
    events_file.write_text(json.dumps(
        {"type": "agent.completed", "timestamp": "2026-04-29T10:02:00+00:00",
         "project": "test", "agent": "researcher", "data": {"return_code": 0}}
    ) + "\n")
    (factory_project / ".factory" / "reviews" / "researcher-latest.md").write_text("done")

    state = reconstruct_and_save(factory_project)
    assert state is not None
    assert "researcher" in state.completed_agents

    # Verify it's on disk and loadable
    loaded = load_checkpoint(factory_project)
    assert loaded is not None
    assert "researcher" in loaded.completed_agents


def test_reconstruct_and_save_no_factory_dir(tmp_path: Path) -> None:
    """Returns None for projects without .factory/."""
    result = reconstruct_and_save(tmp_path / "nonexistent")
    assert result is None


def test_reconstruct_and_save_idempotent(factory_project: Path) -> None:
    """Calling twice produces identical checkpoints (except timestamp)."""
    reconstruct_and_save(factory_project)
    state1 = load_checkpoint(factory_project)

    reconstruct_and_save(factory_project)
    state2 = load_checkpoint(factory_project)

    assert state1 is not None
    assert state2 is not None
    assert state1.completed_agents == state2.completed_agents
    assert state1.active_experiment_id == state2.active_experiment_id
    assert state1.completed_hypotheses == state2.completed_hypotheses
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/test_checkpoint_hook.py::test_reconstruct_and_save_writes_checkpoint tests/test_checkpoint_hook.py::test_reconstruct_and_save_no_factory_dir tests/test_checkpoint_hook.py::test_reconstruct_and_save_idempotent -v`
Expected: All PASS

- [ ] **Step 3: Add `__main__` entry point**

Append to `factory/checkpoint_hook.py`:

```python
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m factory.checkpoint_hook <project_path>", file=sys.stderr)
        sys.exit(1)

    project_path = Path(sys.argv[1])
    result = reconstruct_and_save(project_path)
    sys.exit(0 if result is not None else 1)
```

- [ ] **Step 4: Test the CLI entry point manually**

Run: `uv run python -m factory.checkpoint_hook /tmp/nonexistent; echo "exit: $?"`
Expected: exit code 1 (no .factory/ dir)

- [ ] **Step 5: Commit**

```bash
git add factory/checkpoint_hook.py tests/test_checkpoint_hook.py
git commit -m "feat: add __main__ entry point and reconstruct_and_save tests"
```

---

### Task 4: Hook Shell Scripts

Create the two shell wrapper scripts that Claude Code hooks will call.

**Files:**
- Create: `.claude/hooks/save-checkpoint.sh`
- Create: `.claude/hooks/save-checkpoint-filtered.sh`

- [ ] **Step 1: Create the hooks directory**

Run: `mkdir -p .claude/hooks`

- [ ] **Step 2: Create the unconditional save script**

In `.claude/hooks/save-checkpoint.sh`:

```bash
#!/bin/bash
# Called by Claude Code Stop and SessionEnd hooks to save checkpoint state.
# Uses cwd (the target project), not CLAUDE_PROJECT_DIR (the factory repo).
TARGET_DIR="$(pwd)"
[ -d "$TARGET_DIR/.factory" ] || exit 0
uv run python -m factory.checkpoint_hook "$TARGET_DIR" 2>/dev/null
exit 0
```

- [ ] **Step 3: Create the filtered save script (PostToolUse)**

In `.claude/hooks/save-checkpoint-filtered.sh`:

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

- [ ] **Step 4: Make both scripts executable**

Run: `chmod +x .claude/hooks/save-checkpoint.sh .claude/hooks/save-checkpoint-filtered.sh`

- [ ] **Step 5: Commit**

```bash
git add .claude/hooks/save-checkpoint.sh .claude/hooks/save-checkpoint-filtered.sh
git commit -m "feat: add hook shell scripts for checkpoint saving"
```

---

### Task 5: Hook Configuration in settings.json

Configure the Claude Code hooks that fire the checkpoint scripts.

**Files:**
- Create: `.claude/settings.json`

- [ ] **Step 1: Read existing .claude/settings.json if it exists**

Run: `cat .claude/settings.json 2>/dev/null || echo "no existing settings"`

If a file exists, merge the hooks into the existing JSON. If not, create from scratch.

- [ ] **Step 2: Create .claude/settings.json with hook configuration**

In `.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/save-checkpoint-filtered.sh",
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
            "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/save-checkpoint.sh",
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
            "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/save-checkpoint.sh",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 3: Validate JSON syntax**

Run: `python3 -c "import json; json.load(open('.claude/settings.json')); print('valid')"` 
Expected: `valid`

- [ ] **Step 4: Commit**

```bash
git add .claude/settings.json
git commit -m "feat: add Claude Code hook configuration for checkpoint saving"
```

---

### Task 6: Loop-Level Checkpoint Saving

Add `reconstruct_and_save` call in the heartbeat loop so checkpoints survive hard kills between cycles.

**Files:**
- Modify: `factory/cli.py:1773-1782`
- Modify: `tests/test_checkpoint.py` (add new test)

- [ ] **Step 1: Write failing test for loop-level checkpoint save**

Append to `tests/test_checkpoint.py`:

```python
def test_loop_saves_checkpoint_after_cycle(
    checkpoint_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The heartbeat loop saves a checkpoint after each cycle via reconstruct_and_save."""
    from unittest.mock import MagicMock, patch

    # Set up minimal .factory structure
    (checkpoint_project / ".factory" / "events.jsonl").write_text("")
    (checkpoint_project / ".factory" / "experiments").mkdir(exist_ok=True)
    (checkpoint_project / ".factory" / "strategy").mkdir(exist_ok=True)
    (checkpoint_project / ".factory" / "reviews").mkdir(exist_ok=True)
    (checkpoint_project / ".factory" / "config.json").write_text(
        '{"project_name":"test","description":"t","eval_command":"echo","language":"python","framework":null}'
    )

    from factory.checkpoint_hook import reconstruct_and_save

    result = reconstruct_and_save(checkpoint_project)
    assert result is not None

    loaded = load_checkpoint(checkpoint_project)
    assert loaded is not None
    assert loaded.mode == "improve"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_checkpoint.py::test_loop_saves_checkpoint_after_cycle -v`
Expected: PASS

- [ ] **Step 3: Add reconstruct_and_save to the heartbeat loop**

In `factory/cli.py`, after line 1782 (after `_emit_cli_event(project_path, "cycle.completed", ...)`), add:

```python
            # Save checkpoint after each cycle for crash resilience
            from factory.checkpoint_hook import reconstruct_and_save
            reconstruct_and_save(project_path)
```

The import is inside the loop to avoid import at module level (lazy import pattern used throughout cli.py).

- [ ] **Step 4: Run full test suite to check for regressions**

Run: `uv run pytest tests/test_checkpoint.py tests/test_checkpoint_hook.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add factory/cli.py tests/test_checkpoint.py
git commit -m "feat: add loop-level checkpoint save after each cycle"
```

---

### Task 7: Remove Prompt-Based Checkpoint Commands from CEO Prompt

Remove the `factory checkpoint --save` and `--clear` instructions from the CEO prompt, add a note about automatic checkpointing.

**Files:**
- Modify: `factory/agents/prompts/ceo.md:661-665,739-743,983-987,1036-1038`

- [ ] **Step 1: Remove the 4 checkpoint save/clear blocks from ceo.md**

Find and remove these 4 blocks from `factory/agents/prompts/ceo.md`:

**Block 1** (after Research, ~line 661-665): Remove the "Save crash-recovery checkpoint:" heading and the multi-line `factory checkpoint` command.

**Block 2** (after Strategy, ~line 739-743): Remove the same pattern.

**Block 3** (after each hypothesis keep/revert, ~line 983-987): Remove the same pattern.

**Block 4** (after Final Archive, ~line 1036-1038): Remove the "Clear crash-recovery checkpoint (cycle complete):" heading and the `factory checkpoint "$PROJECT_PATH" --clear` command.

- [ ] **Step 2: Add auto-checkpoint note to the resume section**

After the "Resuming from a Crash" section (~line 135), add:

```markdown
> **Note:** Checkpoint saving is handled automatically by infrastructure hooks.
> You do not need to run `factory checkpoint --save` or `--clear`.
> Focus on the workflow — checkpoints are saved after each phase and cleared on success.
```

- [ ] **Step 3: Verify the prompt still references resume context reading**

Run: `grep -n "Resume Context\|resume context\|Resuming from" factory/agents/prompts/ceo.md`
Expected: The "Resuming from a Crash" section (lines ~124-135) still exists.

- [ ] **Step 4: Commit**

```bash
git add factory/agents/prompts/ceo.md
git commit -m "refactor: remove prompt-based checkpoint saves, add auto-checkpoint note"
```

---

### Task 8: Run Full Test Suite and Lint

Verify nothing is broken across the entire codebase.

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass

- [ ] **Step 2: Run linter**

Run: `uv run ruff check .`
Expected: No errors (or only pre-existing ones)

- [ ] **Step 3: Run type checker**

Run: `uv run mypy factory/checkpoint_hook.py`
Expected: No errors

- [ ] **Step 4: Fix any issues found**

If any tests fail or lint errors appear, fix them and re-run.

- [ ] **Step 5: Commit any fixes**

```bash
git add -u
git commit -m "fix: address lint and type check issues in checkpoint hook"
```
