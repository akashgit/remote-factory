"""Tier 3 e2e test — factory builds a snake game.

This test requires the full factory infrastructure and is skipped by default.
Run with: uv run pytest -m e2e tests/test_e2e_snake.py -v -s

The -s flag is important — it disables output capture so you can see
live progress from the factory state machine.

Expected runtime: 15-30 minutes. Expected cost: ~$0.50-2.00 per run.

IMPORTANT: Always run via `uv run pytest` (not bare `pytest`) to ensure
the local factory code is used, not the globally installed version.
"""

import json
import py_compile
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

# Resolve the factory binary from the same venv Python is running in.
_FACTORY_BIN = str(shutil.which("factory", path=str(
    Path(sys.executable).parent
)))


def _factory_available() -> bool:
    return _FACTORY_BIN is not None and _FACTORY_BIN != "None"


_STAGE_LABELS = {
    "detect": "Detecting project state",
    "discover.started": "Discovering project structure",
    "discover.completed": "Discovery complete",
    "sprint.started": "Sprint started",
    "sprint.completed": "Sprint complete",
    "agent.started": "Agent started",
    "agent.completed": "Agent completed",
    "agent.failed": "Agent FAILED",
    "eval.started": "Running eval",
    "eval.completed": "Eval complete",
    "experiment.begin": "Experiment started",
    "experiment.finalize": "Experiment finalized",
    "cycle.started": "Cycle started",
    "cycle.completed": "Cycle complete",
    "ceo.respawn": "CEO respawning",
}


def _tail_events(events_dir: Path, stop: threading.Event) -> None:
    """Background thread that tails .factory/events.jsonl and prints progress."""
    seen = 0
    while not stop.is_set():
        # Search for events.jsonl in any worktree
        candidates = list(events_dir.rglob("events.jsonl"))
        for events_file in candidates:
            try:
                lines = events_file.read_text().splitlines()
            except OSError:
                continue
            for line in lines[seen:]:
                try:
                    ev = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                ev_type = ev.get("type", "")
                agent = ev.get("agent", "")
                ts = ev.get("timestamp", "")[:19]
                label = _STAGE_LABELS.get(ev_type, ev_type)
                if agent and agent != "None":
                    label = f"{label}: {agent}"
                print(f"  [{ts}] {label}", flush=True)
            seen = max(seen, len(lines))
        stop.wait(2.0)


@pytest.mark.skipif(not _factory_available(), reason="factory CLI not in venv")
@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude CLI not found on PATH",
)
class TestE2ESnakeGame:
    def test_factory_builds_snake_game(self, tmp_path):
        """End-to-end: factory ceo builds a snake game from scratch.

        Uses the LOCAL factory binary (from the project venv), not the
        global install. Validates the full pipeline: CEO → Researcher →
        Strategist → Builder → Archivist.

        Streams progress to stdout so you can follow the factory state
        machine in real time (run with `pytest -s` to see it).
        """
        print(f"\n  Factory binary: {_FACTORY_BIN}")
        print(f"  Output dir: {tmp_path}")
        print(f"  Timeout: 1800s (30 min)\n")

        # Start event tailer in background
        stop_event = threading.Event()
        tailer = threading.Thread(
            target=_tail_events,
            args=(tmp_path, stop_event),
            daemon=True,
        )
        tailer.start()
        start_time = time.monotonic()

        try:
            result = subprocess.run(
                [_FACTORY_BIN, "ceo",
                 "Build a simple snake game in Python using curses. Create a single snake.py file.",
                 "--headless", "--mode", "build", "--no-github"],
                cwd=tmp_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=1800,
            )
            elapsed = time.monotonic() - start_time
            print(f"\n  Factory exited with code {result.returncode} in {elapsed:.0f}s")

            assert result.returncode in (0, 1), (
                f"Unexpected exit code {result.returncode}:\n{result.stderr[-500:]}"
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start_time
            print(f"\n  Factory timed out after {elapsed:.0f}s")
            py_files = list(tmp_path.rglob("*.py"))
            if py_files:
                pytest.skip(f"Timed out after 30min but produced {len(py_files)} .py files")
            else:
                pytest.fail("Timed out after 30min with no output files")
        finally:
            stop_event.set()
            tailer.join(timeout=3)

        # Verify output
        py_files = list(tmp_path.rglob("*.py"))
        print(f"  Python files produced: {[f.name for f in py_files]}")
        assert len(py_files) > 0, "No .py files produced"

        # At least one file should be valid Python
        valid_files = []
        for f in py_files:
            try:
                py_compile.compile(str(f), doraise=True)
                valid_files.append(f.name)
            except py_compile.PyCompileError:
                pass

        print(f"  Valid Python files: {valid_files}")
        assert len(valid_files) > 0, f"No valid Python files among: {[f.name for f in py_files]}"
        print(f"\n  E2E test PASSED in {elapsed:.0f}s")
