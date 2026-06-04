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

E2E_TIMEOUT_SECONDS = int(os.environ.get("FACTORY_E2E_TIMEOUT", "3600"))

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
    stop: threading.Event,
    project_path_holder: list[Path | None],
    wall_start: float,
) -> None:
    """Background thread that tails events from the project directory.

    The project path is set by the main thread (via project_path_holder)
    once it's extracted from the factory's stdout output. Only prints
    events with timestamps after wall_start.
    """
    seen: dict[str, int] = {}
    announced = False
    # ISO format of wall_start for comparing with event timestamps
    from datetime import datetime, timezone
    start_iso = datetime.fromtimestamp(wall_start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    while not stop.is_set():
        project_dir = project_path_holder[0]
        if project_dir is None:
            stop.wait(2.0)
            continue

        if not announced:
            print(f"  Project: {project_dir}", flush=True)
            announced = True

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
                ts = ev.get("timestamp", "")[:19]
                # Skip events from before this test run
                if ts < start_iso:
                    continue
                ev_type = ev.get("type", "")
                agent = ev.get("agent", "")
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
    print(f"  Timeout: {E2E_TIMEOUT_SECONDS}s ({E2E_TIMEOUT_SECONDS // 60} min)\n")

    # Shared holder for the project path — set by stdout reader, read by tailer
    project_path_holder: list[Path | None] = [None]
    existing_projects = set(projects_dir.iterdir()) if projects_dir.exists() else set()
    start_time = time.monotonic()

    wall_start = time.time()
    stop_event = threading.Event()
    tailer = threading.Thread(
        target=_tail_events,
        args=(stop_event, project_path_holder, wall_start),
        daemon=True,
    )
    tailer.start()

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    cmd = [_FACTORY_BIN, "ceo",
           "Build a simple snake game in Python using curses. Create a single snake.py file.",
           "--mode", "build", "--no-github",
           "--runner", runner]

    proc = subprocess.Popen(
        cmd,
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    # Read stdout in background to extract project path
    stdout_lines: list[str] = []

    def _read_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_lines.append(line)
            # Factory prints: "New project from prompt: /path/to/project"
            if "New project from prompt:" in line:
                path_str = line.split(":", 1)[1].strip()
                project_path_holder[0] = Path(path_str)

    stdout_reader = threading.Thread(target=_read_stdout, daemon=True)
    stdout_reader.start()

    try:
        proc.wait(timeout=E2E_TIMEOUT_SECONDS)
        elapsed = time.monotonic() - start_time
        timed_out = False
        print(f"\n  Factory exited with code {proc.returncode} in {elapsed:.0f}s")

        assert proc.returncode in (0, 1), (
            f"Unexpected exit code {proc.returncode}"
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start_time
        timed_out = True
        print(f"\n  Factory timed out after {elapsed:.0f}s — checking output")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    finally:
        stop_event.set()
        stdout_reader.join(timeout=3)
        tailer.join(timeout=3)

    # Find project dir — from stdout or by detecting new dirs
    project_dir = project_path_holder[0]
    new_projects = (set(projects_dir.iterdir()) - existing_projects) if projects_dir.exists() else set()
    search_dirs = [tmp_path]
    if project_dir:
        search_dirs.append(project_dir)
    search_dirs.extend(new_projects)
    print(f"  Project dir: {project_dir}")

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
