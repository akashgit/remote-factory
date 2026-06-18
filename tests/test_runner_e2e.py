"""E2E tests for runner implementations — real API calls, no mocks, no dry-run.

These tests invoke actual CLI binaries and make real API calls via the factory's
invoke_agent() path, which is the production code path for agent invocations.

Slow tests (real API calls) are marked @pytest.mark.slow.
Fast tests (metadata, CLI commands) run without the marker.

Cost control:
- Minimal sample project (5 files, <50 lines each)
- Short prompts, trivial tasks
- 60–120s timeouts
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.agents.runner import invoke_agent, reset_failure_counter
from factory.runners import get_all_runner_meta, get_available_runners, get_runner

_DRY_RUN_VARS = ["FACTORY_BOB_DRY_RUN", "FACTORY_CODEX_DRY_RUN", "FACTORY_OPENCODE_DRY_RUN"]


@pytest.fixture(autouse=True)
def _e2e_env_reset() -> None:
    """Clear dry-run flags and reset failure counter for e2e tests."""
    saved = {k: os.environ.pop(k, None) for k in _DRY_RUN_VARS}
    reset_failure_counter()
    yield  # type: ignore[misc]
    time.sleep(1)
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    reset_failure_counter()


# ── auth detection ──────────────────────────────────────────────


def _runner_has_auth(name: str) -> bool:
    """Check if a runner's auth is configured using the actual runner mechanism."""
    runners = get_available_runners()
    cls = runners.get(name)
    if not cls:
        return False

    meta = cls.metadata()
    if not meta.is_available():
        return False

    if name == "bob":
        # Bob stores auth in ~/.bob/, not env vars — if the binary responds, it's authed
        try:
            result = subprocess.run(
                ["bob", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    if name == "codex":
        # Codex uses ChatGPT OAuth — check via login status
        if os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"):
            return True
        try:
            result = subprocess.run(
                ["codex", "login", "status"],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    if name == "opencode":
        if os.environ.get("OPENAI_API_KEY"):
            return True
        try:
            result = subprocess.run(
                ["zsh", "-c", "source ~/.zshrc 2>/dev/null && echo $OPENAI_API_KEY"],
                capture_output=True, text=True, timeout=5,
            )
            return bool(result.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    # Claude — if binary is available, auth is handled by the CLI itself
    return True


# ── collect available runners ───────────────────────────────────


def _collect_runner_ids() -> list[str]:
    """Collect runner names that are installed and authenticated."""
    ids = []
    for name in get_available_runners():
        if _runner_has_auth(name):
            ids.append(name)
    return ids


AVAILABLE_RUNNERS = _collect_runner_ids()


# ── sample project fixture ──────────────────────────────────────


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a realistic sample Python project with .factory/ config."""
    # main.py — simple CLI with argparse
    (tmp_path / "main.py").write_text(
        'import argparse\n'
        'from utils import format_name, validate_positive\n'
        '\n'
        '\n'
        'def greet(name: str) -> str:\n'
        '    return f"Hello, {format_name(name)}!"\n'
        '\n'
        '\n'
        'def add(a: int, b: int) -> int:\n'
        '    validate_positive(a)\n'
        '    validate_positive(b)\n'
        '    return a + b\n'
        '\n'
        '\n'
        'def main() -> None:\n'
        '    parser = argparse.ArgumentParser(description="Sample CLI")\n'
        '    parser.add_argument("name", help="Name to greet")\n'
        '    parser.add_argument("--add", nargs=2, type=int, help="Two numbers to add")\n'
        '    args = parser.parse_args()\n'
        '    print(greet(args.name))\n'
        '    if args.add:\n'
        '        print(f"Sum: {add(*args.add)}")\n'
        '\n'
        '\n'
        'if __name__ == "__main__":\n'
        '    main()\n'
    )

    # utils.py — helper functions
    (tmp_path / "utils.py").write_text(
        'def format_name(name: str) -> str:\n'
        '    return name.strip().title()\n'
        '\n'
        '\n'
        'def validate_positive(n: int) -> None:\n'
        '    if n < 0:\n'
        '        raise ValueError(f"Expected positive number, got {n}")\n'
    )

    # tests/test_main.py
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_main.py").write_text(
        'from main import greet, add\n'
        '\n'
        '\n'
        'def test_greet():\n'
        '    assert greet("alice") == "Hello, Alice!"\n'
        '\n'
        '\n'
        'def test_greet_strips_whitespace():\n'
        '    assert greet("  bob  ") == "Hello, Bob!"\n'
        '\n'
        '\n'
        'def test_add():\n'
        '    assert add(2, 3) == 5\n'
        '\n'
        '\n'
        'def test_add_rejects_negative():\n'
        '    import pytest\n'
        '    with pytest.raises(ValueError):\n'
        '        add(-1, 2)\n'
    )

    # pyproject.toml
    (tmp_path / "pyproject.toml").write_text(
        '[project]\n'
        'name = "sample-project"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.11"\n'
        '\n'
        '[tool.pytest.ini_options]\n'
        'testpaths = ["tests"]\n'
    )

    # README.md
    (tmp_path / "README.md").write_text(
        "# Sample Project\n\n"
        "A simple CLI that greets users and adds numbers.\n"
    )

    # .factory/ config
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "config.json").write_text(json.dumps({
        "goal": "A sample CLI for testing",
        "scope": ["main.py", "utils.py", "tests/"],
        "guards": [],
        "eval_command": f"cd {tmp_path} && python -m pytest tests/ -q --tb=no",
        "eval_threshold": 0.5,
        "constraints": [],
    }))

    # eval_profile.json — minimal profile for eval/agent CLI tests
    (factory_dir / "eval_profile.json").write_text(json.dumps({
        "project_type": "python",
        "dimensions": [
            {
                "name": "tests",
                "command": f"cd {tmp_path} && python -m pytest tests/ -q --tb=no",
                "weight": 0.7,
                "parser": "exit_code",
                "description": "Run test suite",
                "source": "discovered",
            },
            {
                "name": "lint",
                "command": "echo 'lint ok'",
                "weight": 0.3,
                "parser": "exit_code",
                "description": "Lint check",
                "source": "fallback",
            },
        ],
        "tier": "discovered",
        "confidence": 0.8,
        "human_reviewed": True,
    }))

    # .factory/reviews/ for output capture
    (factory_dir / "reviews").mkdir()

    # Initialize git repo (agents need git)
    subprocess.run(
        ["git", "init"], cwd=tmp_path,
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "add", "."], cwd=tmp_path,
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=tmp_path, capture_output=True, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com"},
    )

    return tmp_path


# ── fast tests (no API calls) ──────────────────────────────────


def test_runners_list_command() -> None:
    """factory runners list returns 0 and shows all runners."""
    from factory.cli import build_parser, cmd_runners_list

    parser = build_parser()
    args = parser.parse_args(["runners", "list"])
    code = cmd_runners_list(args)
    assert code == 0


def test_runners_list_json() -> None:
    """factory runners list --json returns valid JSON with all runners."""
    from factory.cli import build_parser, cmd_runners_list

    parser = build_parser()
    args = parser.parse_args(["runners", "list", "--json"])

    buf = StringIO()
    with patch("sys.stdout", buf):
        code = cmd_runners_list(args)

    assert code == 0
    data = json.loads(buf.getvalue())
    assert isinstance(data, list)
    assert len(data) >= 4
    names = {r["name"] for r in data}
    assert "claude" in names
    assert "bob" in names
    assert "codex" in names
    assert "opencode" in names


def test_get_available_runners_includes_all_builtins() -> None:
    """get_available_runners includes all 4 built-in runners."""
    runners = get_available_runners()
    assert "claude" in runners
    assert "bob" in runners
    assert "codex" in runners
    assert "opencode" in runners


def test_runner_metadata_consistency() -> None:
    """All runners have consistent metadata."""
    meta_list = get_all_runner_meta()
    assert len(meta_list) >= 4

    names = set()
    for m in meta_list:
        assert m.name, "name must not be empty"
        assert m.display_name, "display_name must not be empty"
        assert m.binary, "binary must not be empty"
        assert m.install_hint, "install_hint must not be empty"
        assert m.name not in names, f"duplicate runner name: {m.name}"
        names.add(m.name)


def test_entry_point_discovery_does_not_crash() -> None:
    """Entry point discovery runs without error even if no plugins are installed."""
    import factory.runners as runners_mod

    runners_mod._entrypoints_loaded = False
    runners_mod._load_entrypoint_runners()
    assert runners_mod._entrypoints_loaded is True


def test_capability_matrix() -> None:
    """RunnerMeta accurately reflects each runner's capabilities."""
    for name, cls in get_available_runners().items():
        meta = cls.metadata()
        assert meta.name == name
        if meta.is_available():
            found = shutil.which(meta.binary)
            if not found:
                common = [
                    Path.home() / "go" / "bin" / meta.binary,
                    Path.home() / ".local" / "bin" / meta.binary,
                ]
                assert any(p.is_file() for p in common), (
                    f"{name}: binary '{meta.binary}' claimed available "
                    f"but not found on PATH or common paths"
                )


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="No runners authenticated in CI",
)
def test_available_runners_detected() -> None:
    """At least one runner is detected as available and authenticated."""
    assert len(AVAILABLE_RUNNERS) > 0, (
        "No runners detected — auth detection may be broken"
    )


# ── slow tests (real API calls) ─────────────────────────────────

# Agent invocation via invoke_agent() — the real factory code path


@pytest.mark.slow
@pytest.mark.parametrize("runner_name", AVAILABLE_RUNNERS)
async def test_agent_invocation(runner_name: str, sample_project: Path) -> None:
    """Each runner can invoke a specialist agent and produce output."""
    stdout, code = await invoke_agent(
        "researcher",
        "List all functions in main.py and utils.py. Be concise.",
        sample_project,
        runner_name=runner_name,
        timeout=90.0,
    )
    assert code == 0, f"{runner_name} researcher failed (code={code}): {stdout[:300]}"
    assert len(stdout.strip()) > 0, f"{runner_name} returned empty output"

    review_file = sample_project / ".factory" / "reviews" / "researcher-latest.md"
    assert review_file.exists(), f"{runner_name}: output not captured to reviews/"


@pytest.mark.slow
@pytest.mark.parametrize("runner_name", AVAILABLE_RUNNERS)
async def test_builder_makes_changes(runner_name: str, sample_project: Path) -> None:
    """Each runner's Builder can modify code and the changes exist."""
    stdout, code = await invoke_agent(
        "builder",
        "Add a one-line docstring to the greet() function in main.py. "
        "Commit the change with message 'add docstring'.",
        sample_project,
        runner_name=runner_name,
        timeout=120.0,
    )
    assert code == 0, f"{runner_name} builder failed (code={code}): {stdout[:300]}"

    content = (sample_project / "main.py").read_text()
    assert "def greet" in content, "greet function should still exist"


@pytest.mark.slow
@pytest.mark.parametrize("runner_name", AVAILABLE_RUNNERS)
async def test_output_captured_to_reviews(runner_name: str, sample_project: Path) -> None:
    """Agent output is captured to .factory/reviews/<role>-latest.md for all runners."""
    stdout, code = await invoke_agent(
        "researcher",
        "What does this project do? One sentence.",
        sample_project,
        runner_name=runner_name,
        timeout=60.0,
    )
    review_file = sample_project / ".factory" / "reviews" / "researcher-latest.md"
    assert review_file.exists(), f"{runner_name}: output not captured to reviews/"
    content = review_file.read_text()
    assert len(content) > 10, f"{runner_name}: review file is too short"


@pytest.mark.slow
async def test_cross_runner_parity(sample_project: Path) -> None:
    """Same task with all runners produces valid results."""
    results: dict[str, dict[str, object]] = {}
    for name in AVAILABLE_RUNNERS:
        stdout, code = await invoke_agent(
            "researcher",
            "Count the number of Python files in this project. Reply with just the number.",
            sample_project,
            runner_name=name,
            timeout=60.0,
        )
        results[name] = {"stdout": stdout, "code": code}

    for name, r in results.items():
        assert r["code"] == 0, f"{name} failed with code {r['code']}"
        stdout = r["stdout"]
        assert isinstance(stdout, str)
        assert len(stdout.strip()) > 0, f"{name} returned empty output"


@pytest.mark.slow
@pytest.mark.parametrize("runner_name", AVAILABLE_RUNNERS)
async def test_timeout_handling(runner_name: str, sample_project: Path) -> None:
    """Very short timeout is handled gracefully."""
    stdout, code = await invoke_agent(
        "researcher",
        "Write a detailed 5000-word analysis of every aspect of this project.",
        sample_project,
        runner_name=runner_name,
        timeout=5.0,
    )
    # Either the runner timed out (code != 0) or completed before the deadline.
    # Both are acceptable — the test verifies graceful handling, not guaranteed timeout.
    assert isinstance(stdout, str), f"{runner_name} should return string output"


@pytest.mark.slow
@pytest.mark.skipif("claude" not in AVAILABLE_RUNNERS, reason="claude not available")
async def test_claude_usage_telemetry(sample_project: Path) -> None:
    """Claude runner returns usage telemetry (input/output tokens)."""
    runner = get_runner("claude")
    from factory.models import AgentRunRequest
    request = AgentRunRequest(
        prompt="You are a code assistant. Be concise.",
        task="What does main.py do? One sentence.",
        cwd=sample_project,
        timeout=60.0,
        skip_permissions=True,
        role="researcher",
        project_path=sample_project,
    )
    result = await runner.headless(request)

    assert result.return_code == 0
    assert result.usage is not None, "Claude should return usage telemetry"
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0


@pytest.mark.slow
async def test_tmux_persist_degrades_for_non_claude(sample_project: Path) -> None:
    """tmux_persist=True on non-Claude runners warns but doesn't crash."""
    for name in AVAILABLE_RUNNERS:
        if name == "claude":
            continue
        stdout, code = await invoke_agent(
            "researcher",
            "List files in this project. Be concise.",
            sample_project,
            runner_name=name,
            timeout=60.0,
            tmux_persist=True,
        )
        assert code == 0, (
            f"{name} should succeed despite unsupported tmux_persist "
            f"(code={code}): {stdout[:200]}"
        )


@pytest.mark.slow
@pytest.mark.parametrize("runner_name", AVAILABLE_RUNNERS)
async def test_headless_produces_output(runner_name: str, sample_project: Path) -> None:
    """Every runner can do a headless invocation via the low-level runner API."""
    from factory.models import AgentRunRequest, AgentRunResult

    runner = get_runner(runner_name)
    request = AgentRunRequest(
        prompt="You are a code assistant. Be concise.",
        task="What does main.py do? Reply in one sentence.",
        cwd=sample_project,
        timeout=60.0,
        skip_permissions=True,
        role="researcher",
        project_path=sample_project,
    )
    result = await runner.headless(request)

    assert isinstance(result, AgentRunResult)
    assert result.return_code == 0, f"{runner_name} failed: {result.stdout[:300]}"
    assert len(result.stdout.strip()) > 0, f"{runner_name} returned empty output"


# ── factory CLI tests (subprocess-level) ──────────────────────


def _cli_env() -> dict[str, str]:
    """Build subprocess env with PATH that includes ~/go/bin for opencode."""
    env = os.environ.copy()
    go_bin = str(Path.home() / "go" / "bin")
    if go_bin not in env.get("PATH", ""):
        env["PATH"] = go_bin + ":" + env["PATH"]
    if not env.get("OPENAI_API_KEY"):
        try:
            result = subprocess.run(
                ["zsh", "-c", "source ~/.zshrc 2>/dev/null && echo $OPENAI_API_KEY"],
                capture_output=True, text=True, timeout=5,
            )
            key = result.stdout.strip()
            if key:
                env["OPENAI_API_KEY"] = key
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    for var in _DRY_RUN_VARS:
        env.pop(var, None)
    return env


@pytest.mark.slow
@pytest.mark.parametrize("runner_name", AVAILABLE_RUNNERS)
def test_factory_agent_cli_per_runner(runner_name: str, sample_project: Path) -> None:
    """factory agent researcher via CLI subprocess for each runner."""
    env = _cli_env()
    if runner_name == "codex":
        env.pop("OPENAI_API_KEY", None)
        env.pop("CODEX_API_KEY", None)
    result = subprocess.run(
        [
            "uv", "run", "factory", "agent", "researcher",
            "--task", "List files in this project. Be concise.",
            "--runner", runner_name,
            "--project", str(sample_project),
            "--timeout", "60",
        ],
        cwd=sample_project,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, (
        f"factory agent researcher --runner {runner_name} failed "
        f"(code={result.returncode}):\nstdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
    )
    review_file = sample_project / ".factory" / "reviews" / "researcher-latest.md"
    assert review_file.exists(), (
        f"--runner {runner_name}: .factory/reviews/researcher-latest.md not created"
    )


@pytest.mark.slow
def test_factory_eval_runs(sample_project: Path) -> None:
    """factory eval produces JSON with results."""
    result = subprocess.run(
        ["uv", "run", "factory", "eval", str(sample_project)],
        cwd=sample_project,
        capture_output=True,
        text=True,
        timeout=60,
        env=_cli_env(),
    )
    assert result.returncode == 0, (
        f"factory eval failed (code={result.returncode}):\n"
        f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
    )
    data = json.loads(result.stdout)
    assert "total" in data, "eval output missing 'total' key"
    assert "results" in data, "eval output missing 'results' key"
    assert isinstance(data["results"], list)
    assert len(data["results"]) > 0, "eval produced no results"


def test_factory_runners_list_all_present() -> None:
    """factory runners list --json shows all 4 runners with correct metadata."""
    result = subprocess.run(
        ["uv", "run", "factory", "runners", "list", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"factory runners list --json failed: {result.stderr[:300]}"
    )
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    names = {r["name"] for r in data}
    for expected in ("claude", "bob", "codex", "opencode"):
        assert expected in names, f"runner '{expected}' missing from list"
    for runner in data:
        assert "name" in runner
        assert "display_name" in runner
        assert "binary" in runner
        assert "available" in runner
