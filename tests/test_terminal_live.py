"""Manual test: simulate research mode events and watch the terminal status line.

Usage:
    uv run python tests/test_terminal_live.py

This writes fake events to a temp .factory/events.jsonl at realistic intervals
and starts a TerminalStatus watching it. No agents are spawned.
"""

from __future__ import annotations

import json
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from factory.terminal import TerminalStatus


def _emit(events_file: Path, event_type: str, agent: str | None = None, data: dict | None = None) -> None:
    event = {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": "test-project",
        "agent": agent,
        "data": data or {},
    }
    with open(events_file, "a") as f:
        f.write(json.dumps(event) + "\n")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp())
    factory_dir = tmpdir / ".factory"
    factory_dir.mkdir()
    events_file = factory_dir / "events.jsonl"
    events_file.touch()

    status = TerminalStatus(tmpdir, "research")
    # Force start even when not a TTY (for testing)
    status._active = True
    status._thread = __import__("threading").Thread(target=status._tail_loop, daemon=True)
    status._thread.start()

    try:
        print("\n--- Simulating research mode cycle ---\n")

        # Cycle starts
        _emit(events_file, "cycle.started", data={"cycle": 1, "mode": "research"})
        time.sleep(1)

        # Scrummaster
        _emit(events_file, "agent.started", agent="scrummaster", data={"task": "standup"})
        time.sleep(3)
        _emit(events_file, "agent.completed", agent="scrummaster", data={"return_code": 0})
        time.sleep(0.5)

        # CEO starts
        _emit(events_file, "agent.started", agent="ceo", data={"task": "research cycle"})
        time.sleep(2)

        # R1: failure_analyst
        _emit(events_file, "agent.started", agent="failure_analyst", data={"task": "analyze"})
        time.sleep(4)
        _emit(events_file, "agent.completed", agent="failure_analyst", data={"return_code": 0})
        time.sleep(0.5)

        # Archivist after failure_analyst
        _emit(events_file, "agent.started", agent="archivist", data={"task": "record"})
        time.sleep(1)
        _emit(events_file, "agent.completed", agent="archivist", data={"return_code": 0})
        time.sleep(0.5)

        # R1.5: researcher
        _emit(events_file, "agent.started", agent="researcher", data={"task": "research"})
        time.sleep(5)
        _emit(events_file, "agent.completed", agent="researcher", data={"return_code": 0})
        time.sleep(0.5)

        # Archivist after researcher
        _emit(events_file, "agent.started", agent="archivist", data={"task": "record"})
        time.sleep(1)
        _emit(events_file, "agent.completed", agent="archivist", data={"return_code": 0})
        time.sleep(0.5)

        # R2: strategist
        _emit(events_file, "agent.started", agent="strategist", data={"task": "strategize"})
        time.sleep(3)
        _emit(events_file, "agent.completed", agent="strategist", data={"return_code": 0})
        time.sleep(0.5)

        # Archivist after strategist
        _emit(events_file, "agent.started", agent="archivist", data={"task": "record"})
        time.sleep(1)
        _emit(events_file, "agent.completed", agent="archivist", data={"return_code": 0})
        time.sleep(0.5)

        # --- Experiment 1 ---
        _emit(events_file, "experiment.begin", data={"exp_id": 12, "hypothesis": "Q16.16 integer fixed-point wrapper class"})
        time.sleep(0.5)

        _emit(events_file, "agent.started", agent="builder", data={"task": "build"})
        time.sleep(5)
        _emit(events_file, "agent.completed", agent="builder", data={"return_code": 0})
        time.sleep(0.5)

        # Archivist after builder
        _emit(events_file, "agent.started", agent="archivist", data={"task": "record"})
        time.sleep(1)
        _emit(events_file, "agent.completed", agent="archivist", data={"return_code": 0})
        time.sleep(0.5)

        _emit(events_file, "agent.started", agent="evaluator", data={"task": "eval"})
        time.sleep(3)
        _emit(events_file, "agent.completed", agent="evaluator", data={"return_code": 0})
        time.sleep(0.5)

        # Experiment 1 finalized — REVERT
        _emit(events_file, "experiment.finalize", data={"exp_id": 12, "verdict": "revert"})
        time.sleep(1)

        # --- Experiment 2 ---
        _emit(events_file, "experiment.begin", data={"exp_id": 13, "hypothesis": "Loop merge + convergence deferral"})
        time.sleep(0.5)

        _emit(events_file, "agent.started", agent="builder", data={"task": "build"})
        time.sleep(6)
        _emit(events_file, "agent.completed", agent="builder", data={"return_code": 0})
        time.sleep(0.5)

        _emit(events_file, "agent.started", agent="evaluator", data={"task": "eval"})
        time.sleep(3)
        _emit(events_file, "agent.completed", agent="evaluator", data={"return_code": 0})
        time.sleep(0.5)

        # Experiment 2 finalized — KEEP
        _emit(events_file, "experiment.finalize", data={"exp_id": 13, "verdict": "keep"})
        time.sleep(1)

        # CEO completes
        _emit(events_file, "agent.completed", agent="ceo", data={"return_code": 0})
        time.sleep(2)

        print("\n\n--- Simulation complete ---")

    except KeyboardInterrupt:
        print("\n\nInterrupted.")
    finally:
        status.stop()
        # Cleanup
        events_file.unlink(missing_ok=True)
        factory_dir.rmdir()
        tmpdir.rmdir()


if __name__ == "__main__":
    main()
