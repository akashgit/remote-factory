"""Real E2E tests for tmux-based runner invocations.

These tests launch actual CLI binaries (Claude, Codex) in tmux sessions
via the factory's run_in_tmux() path and verify output capture and telemetry.

All tests are marked @pytest.mark.slow and auto-skip when the required
runner binary is not available or not authenticated.

Cost control:
- Minimal prompts, trivial tasks
- 60–120s timeouts
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from factory.runners._tmux_persist import run_in_tmux, tmux_available
from factory.runners.codex import _parse_codex_ndjson_usage, _write_agents_md, CodexRunner
from factory.runners.claude import ClaudeRunner
from factory.models import AgentRunRequest


def _load_env_file(path: Path) -> dict[str, str]:
    """Load KEY=VALUE pairs from a .env file."""
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'\"")
    return env


_DOTENV = _load_env_file(Path.home() / "remote-factory" / ".env")

_HAS_TMUX = tmux_available()
_HAS_CLAUDE = shutil.which("claude") is not None
_HAS_CODEX = shutil.which("codex") is not None
_HAS_OPENAI_KEY = bool(os.environ.get("OPENAI_API_KEY") or _DOTENV.get("OPENAI_API_KEY"))


def _codex_authed() -> bool:
    if _HAS_OPENAI_KEY:
        return True
    if not _HAS_CODEX:
        return False
    try:
        r = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


_CODEX_AUTHED = _codex_authed()

skip_no_tmux = pytest.mark.skipif(not _HAS_TMUX, reason="tmux not available")
skip_no_claude = pytest.mark.skipif(not _HAS_CLAUDE, reason="claude CLI not available")
skip_no_codex = pytest.mark.skipif(
    not (_HAS_CODEX and _CODEX_AUTHED),
    reason="codex CLI not available or not authenticated",
)


@pytest.fixture
def e2e_project(tmp_path: Path) -> Path:
    """Minimal project with git init and .factory/ for E2E tests."""
    (tmp_path / "hello.py").write_text('def hello():\n    return "Hello, world!"\n')
    (tmp_path / ".factory").mkdir()
    (tmp_path / ".factory" / "reviews").mkdir()
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path, capture_output=True, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return tmp_path


@pytest.fixture
def codex_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure OPENAI_API_KEY is set for Codex tests, loading from ~/remote-factory/.env."""
    if not os.environ.get("OPENAI_API_KEY"):
        key = _DOTENV.get("OPENAI_API_KEY")
        if key:
            monkeypatch.setenv("OPENAI_API_KEY", key)
    monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)


# ── unit tests for new helpers (fast, no API calls) ──────────────


class TestParseCodexNdjsonUsage:
    def test_parses_usage_event(self) -> None:
        ndjson = (
            '{"type":"message","content":"hi"}\n'
            '{"type":"usage","usage":{"input_tokens":100,"output_tokens":50,"reasoning_tokens":10},"model":"o4-mini"}\n'
        )
        usage = _parse_codex_ndjson_usage(ndjson)
        assert usage is not None
        assert usage.input_tokens == 100
        assert usage.output_tokens == 60
        assert usage.model == "o4-mini"

    def test_aggregates_multiple_usage_events(self) -> None:
        ndjson = (
            '{"type":"usage","usage":{"input_tokens":50,"output_tokens":20,"reasoning_tokens":0},"model":"o4-mini"}\n'
            '{"type":"usage","usage":{"input_tokens":30,"output_tokens":10,"reasoning_tokens":5},"model":"o4-mini"}\n'
        )
        usage = _parse_codex_ndjson_usage(ndjson)
        assert usage is not None
        assert usage.input_tokens == 80
        assert usage.output_tokens == 35

    def test_returns_none_for_no_usage(self) -> None:
        ndjson = '{"type":"message","content":"hello"}\n'
        assert _parse_codex_ndjson_usage(ndjson) is None

    def test_handles_empty_string(self) -> None:
        assert _parse_codex_ndjson_usage("") is None

    def test_handles_invalid_json(self) -> None:
        assert _parse_codex_ndjson_usage("not json\n{bad") is None

    def test_handles_plain_text(self) -> None:
        assert _parse_codex_ndjson_usage("just some output text") is None


class TestWriteAgentsMd:
    def test_writes_file(self, tmp_path: Path) -> None:
        agents_md, backup = _write_agents_md(tmp_path, "You are a researcher.")
        assert agents_md == tmp_path / "AGENTS.md"
        assert agents_md.read_text() == "You are a researcher."
        assert backup is None

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("old content")
        agents_md, backup = _write_agents_md(tmp_path, "new content")
        assert agents_md.read_text() == "new content"
        assert backup is not None


class TestCodexMetaTelemetry:
    def test_supports_usage_telemetry(self) -> None:
        meta = CodexRunner.metadata()
        assert meta.supports_usage_telemetry is True


class TestCodexBuildCommandJson:
    def test_json_flag_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        runner = CodexRunner()
        cmd, _, temp_files = runner.build_command(AgentRunRequest(
            prompt="You are a tester.", task="Say hi", cwd=tmp_path,
        ))
        assert "--json" in cmd
        assert cmd[-1] == "Say hi"
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_agents_md_created(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        runner = CodexRunner()
        _, _, temp_files = runner.build_command(AgentRunRequest(
            prompt="Role prompt here.", task="Task", cwd=tmp_path,
        ))
        assert len(temp_files) == 1
        assert temp_files[0].name == "AGENTS.md"
        assert "Role prompt here." in temp_files[0].read_text()
        for f in temp_files:
            f.unlink(missing_ok=True)


class TestTmuxPersistGenericCommand:
    """Verify run_in_tmux accepts runner_cmd for non-Claude runners."""

    async def test_custom_runner_cmd_in_wrapper(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        project_path = tmp_path / "proj"
        project_path.mkdir()

        captured_wrapper: dict[str, str] = {}
        original_write_text = Path.write_text

        def spy_write_text(self_path: Path, content: str, *args, **kwargs) -> None:
            if self_path.name == "wrapper.sh":
                captured_wrapper["content"] = content
            original_write_text(self_path, content, *args, **kwargs)

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=True),
            patch("factory.runners._tmux_persist._wait_for_window_exit", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._wait_for_exitcode", new_callable=AsyncMock, return_value=0),
            patch("factory.runners._tmux_persist._session_exists", return_value=False),
            patch("factory.runners._tmux_persist._window_exists", return_value=False),
            patch.object(Path, "write_text", spy_write_text),
        ):
            mock_run.side_effect = [MagicMock(returncode=0)]

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("codex output")

                stdout, code, _ = await run_in_tmux(
                    "prompt", "task", project_path, "builder", project_path,
                    runner_cmd=["codex", "echo hello"],
                    runner_env={"OPENAI_API_KEY": "sk-test"},
                )

        assert code == 0
        assert "codex output" in stdout
        content = captured_wrapper["content"]
        assert "codex" in content
        assert "trap cleanup EXIT" in content
        assert "--settings" not in content
        assert "OPENAI_API_KEY" in content

    async def test_no_exit_sent_for_non_claude(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        project_path = tmp_path / "proj"
        project_path.mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=True),
            patch("factory.runners._tmux_persist._wait_for_window_exit", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._wait_for_exitcode", new_callable=AsyncMock, return_value=0),
            patch("factory.runners._tmux_persist._session_exists", return_value=False),
            patch("factory.runners._tmux_persist._window_exists", return_value=False),
        ):
            mock_run.side_effect = [MagicMock(returncode=0)]

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("output")

                await run_in_tmux(
                    "prompt", "task", project_path, "builder", project_path,
                    runner_cmd=["codex", "some task"],
                )

        cmds_called = [call[0][0] for call in mock_run.call_args_list]
        send_keys_cmds = [c for c in cmds_called if "send-keys" in c]
        assert len(send_keys_cmds) == 0, "Should not send /exit for non-Claude runner"


# ── real E2E tests (slow, require actual CLI binaries) ───────────


@pytest.mark.slow
@skip_no_tmux
@skip_no_claude
async def test_claude_tmux_persist_e2e(e2e_project: Path) -> None:
    """Launch Claude in a tmux session via run_in_tmux and verify output capture."""
    stdout, code, usage = await run_in_tmux(
        "You are a concise code assistant.",
        "List files in the current directory. Reply in one sentence.",
        e2e_project,
        "researcher",
        e2e_project,
        timeout=120.0,
        dangerously_skip_permissions=True,
    )

    assert code == 0, f"Claude tmux failed (code={code}): {stdout[:300]}"
    assert len(stdout.strip()) > 0, "Claude tmux produced no output"
    assert usage is None


@pytest.mark.slow
@skip_no_tmux
@skip_no_claude
async def test_claude_headless_tmux_persist_via_runner(e2e_project: Path) -> None:
    """ClaudeRunner.headless() with tmux_persist=True captures output."""
    runner = ClaudeRunner()
    request = AgentRunRequest(
        prompt="You are a concise assistant.",
        task="What Python files are in this directory? One sentence.",
        cwd=e2e_project,
        timeout=120.0,
        skip_permissions=True,
        role="researcher",
        extras={"tmux_persist": True},
    )
    result = await runner.headless(request)

    assert result.return_code == 0, f"Claude tmux headless failed: {result.stdout[:300]}"
    assert len(result.stdout.strip()) > 0


@pytest.mark.slow
@skip_no_tmux
@skip_no_codex
async def test_codex_tmux_persist_e2e(e2e_project: Path, codex_env: None) -> None:
    """Launch Codex in a tmux session via run_in_tmux with runner_cmd."""
    import factory.runners.codex as codex_module
    codex_module._auth_checked = False

    runner = CodexRunner()
    request = AgentRunRequest(
        prompt="You are a concise assistant.",
        task="List files in the current directory. Reply in one sentence.",
        cwd=e2e_project,
        timeout=120.0,
        skip_permissions=True,
        role="researcher",
        project_path=e2e_project,
    )
    int_cmd, int_env, temp_files = runner.build_interactive_command(request)

    try:
        stdout, code, usage = await run_in_tmux(
            request.prompt, request.task, e2e_project, "researcher", e2e_project,
            runner_cmd=int_cmd,
            runner_env=int_env,
            timeout=120.0,
        )

        assert code == 0, f"Codex tmux failed (code={code}): {stdout[:300]}"
        assert len(stdout.strip()) > 0, "Codex tmux produced no output"
    finally:
        for f in temp_files:
            f.unlink(missing_ok=True)


@pytest.mark.slow
@skip_no_tmux
@skip_no_codex
async def test_codex_headless_tmux_persist_via_runner(e2e_project: Path, codex_env: None) -> None:
    """CodexRunner.headless() with tmux_persist=True captures output."""
    import factory.runners.codex as codex_module
    codex_module._auth_checked = False

    runner = CodexRunner()
    request = AgentRunRequest(
        prompt="You are a concise assistant.",
        task="What files are in this directory? One sentence.",
        cwd=e2e_project,
        timeout=120.0,
        skip_permissions=True,
        role="researcher",
        project_path=e2e_project,
        extras={"tmux_persist": True},
    )
    result = await runner.headless(request)

    assert result.return_code == 0, f"Codex tmux headless failed: {result.stdout[:300]}"
    assert len(result.stdout.strip()) > 0


@pytest.mark.slow
@skip_no_codex
async def test_codex_headless_ndjson_usage(e2e_project: Path, codex_env: None) -> None:
    """Codex headless invocation returns usage telemetry from NDJSON output."""
    import factory.runners.codex as codex_module
    codex_module._auth_checked = False

    runner = CodexRunner()
    request = AgentRunRequest(
        prompt="You are a concise code assistant.",
        task="What does hello.py do? One sentence.",
        cwd=e2e_project,
        timeout=90.0,
        skip_permissions=True,
        role="researcher",
        project_path=e2e_project,
    )
    result = await runner.headless(request)

    assert result.return_code == 0, f"Codex headless failed: {result.stdout[:300]}"
    if result.usage is not None:
        assert result.usage.input_tokens > 0 or result.usage.output_tokens > 0
