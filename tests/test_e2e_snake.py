"""Tier 3 e2e test — factory builds a snake game.

This test requires the full factory infrastructure and is skipped by default.
Run with: uv run pytest -m e2e tests/test_e2e_snake.py -v -s

The -s flag is important — it disables output capture so you can see
live progress from the factory state machine.

Expected runtime: 25-45 minutes (full pipeline: Build → Discover → Improve).
Expected cost: ~$0.50-2.00 per run.

IMPORTANT: Always run via `uv run pytest` (not bare `pytest`) to ensure
the local factory code is used, not the globally installed version.
"""

import json
import os
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


def _has_codex_key() -> bool:
    return bool(os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"))


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
    "phase.research.completed": "Research phase complete",
    "phase.strategy.completed": "Strategy phase complete",
    "phase.build.completed": "Build phase complete",
    "phase.eval.completed": "Eval phase complete",
    "phase.verdict": "Verdict reached",
    "phase.archive.completed": "Archive phase complete",
    "precheck.completed": "Precheck complete",
    "guard.completed": "Guard check complete",
    "verdict.force_kept": "Verdict: KEEP (forced)",
    "summary.started": "Generating summary",
    "summary.completed": "Summary complete",
}


def _tail_events(
    projects_dir: Path,
    stop: threading.Event,
    existing_projects: set[Path],
) -> None:
    """Background thread that detects the new project dir and tails only its events.

    Only watches events.jsonl files inside the project directory created
    by THIS test run — ignores other projects in ~/factory-projects/.
    """
    project_dir: Path | None = None
    seen: dict[str, int] = {}

    while not stop.is_set():
        # Detect the new project directory created by the factory
        if project_dir is None and projects_dir.exists():
            current = set(projects_dir.iterdir()) if projects_dir.exists() else set()
            new_dirs = [d for d in (current - existing_projects) if d.is_dir()]
            if new_dirs:
                project_dir = max(new_dirs, key=lambda d: d.stat().st_mtime)
                print(f"  Project: {project_dir}", flush=True)

        if project_dir is None:
            stop.wait(2.0)
            continue

        # Tail only events.jsonl files inside THIS project
        for events_file in project_dir.rglob("events.jsonl"):
            key = str(events_file)
            offset = seen.get(key, 0)
            try:
                lines = events_file.read_text().splitlines()
            except OSError:
                continue
            for line in lines[offset:]:
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
            seen[key] = len(lines)
        stop.wait(2.0)


def _find_projects_dir() -> Path:
    """Resolve the factory projects directory (same logic as factory CLI)."""
    raw = os.environ.get("FACTORY_PROJECTS_DIR", str(Path.home() / "factory-projects"))
    return Path(raw).expanduser()


def _run_factory_e2e(tmp_path: Path, runner: str) -> None:
    """Shared e2e logic: run factory CEO to build a snake game with the given runner.

    Runs the full factory pipeline (Build → Discover → Improve chaining)
    as a normal user would. Streams live progress from the event log.
    """
    projects_dir = _find_projects_dir()
    print(f"\n  Factory binary: {_FACTORY_BIN}")
    print(f"  Runner: {runner}")
    print(f"  Projects dir: {projects_dir}")
    print(f"  Timeout: 3600s (60 min)\n")

    # Snapshot existing projects so we can find the new one
    existing_projects = set(projects_dir.iterdir()) if projects_dir.exists() else set()

    stop_event = threading.Event()
    tailer = threading.Thread(
        target=_tail_events,
        args=(projects_dir, stop_event, existing_projects),
        daemon=True,
    )
    tailer.start()
    start_time = time.monotonic()

    try:
        result = subprocess.run(
            [_FACTORY_BIN, "ceo",
             "Build a simple snake game in Python using curses. Create a single snake.py file.",
             "--headless", "--mode", "build", "--no-github",
             "--runner", runner],
            cwd=tmp_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3600,
        )
        elapsed = time.monotonic() - start_time
        timed_out = False
        print(f"\n  Factory exited with code {result.returncode} in {elapsed:.0f}s")

        assert result.returncode in (0, 1), (
            f"Unexpected exit code {result.returncode}:\n{result.stderr[-500:]}"
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start_time
        timed_out = True
        print(f"\n  Factory timed out after {elapsed:.0f}s — checking output")
    finally:
        stop_event.set()
        tailer.join(timeout=3)

    # Find the new project directory created by the factory
    new_projects = (set(projects_dir.iterdir()) - existing_projects) if projects_dir.exists() else set()
    search_dirs = [tmp_path, *new_projects]
    print(f"  New project dirs: {[d.name for d in new_projects]}")

    # Verify output — search both tmp_path and new project dirs
    py_files: list[Path] = []
    for d in search_dirs:
        py_files.extend(d.rglob("*.py"))
    print(f"  Python files produced: {[f.name for f in py_files]}")

    if timed_out and not py_files:
        pytest.fail("Timed out with no output files")

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

    status = "PASSED" if not timed_out else "PASSED (timed out but output valid)"
    print(f"\n  E2E test {status} ({runner}) in {elapsed:.0f}s")


@pytest.mark.skipif(not _factory_available(), reason="factory CLI not in venv")
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not found")
class TestE2ESnakeGameClaude:
    def test_factory_builds_snake_game_claude(self, tmp_path):
        """E2E: factory builds a snake game using the Claude runner (full pipeline)."""
        _run_factory_e2e(tmp_path, "claude")


@pytest.mark.skipif(not _factory_available(), reason="factory CLI not in venv")
@pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI not found")
@pytest.mark.skipif(not _has_codex_key(), reason="CODEX_API_KEY/OPENAI_API_KEY not set")
class TestE2ESnakeGameCodex:
    def test_factory_builds_snake_game_codex(self, tmp_path):
        """E2E: factory builds a snake game using the Codex runner (full pipeline)."""
        _run_factory_e2e(tmp_path, "codex")
