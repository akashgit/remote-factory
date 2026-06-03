"""Tests for factory/runners/abstraction.py — AgentRunner base class and core types."""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.runners.abstraction import (
    AgentRunner,
    Capability,
    Request,
    Response,
    RunnerIdentity,
)


# ── Minimal concrete subclass for testing ──────────────────────


class StubRunner(AgentRunner):
    """Minimal concrete runner for testing the ABC."""

    @property
    def identity(self) -> RunnerIdentity:
        return RunnerIdentity(name="stub", display_name="Stub Runner")

    def _build_command(
        self, request: Request, *, prompt_file: str | None = None
    ) -> list[str]:
        return ["echo", "hello"]

    def _parse_response(self, stdout: str, stderr: str, exit_code: int) -> Response:
        return Response(output=stdout.strip(), exit_code=exit_code)


# ── Request tests ──────────────────────────────────────────────


class TestRequest:
    def test_prompt_combines_system_and_task(self) -> None:
        req = Request(system_prompt="You are a reviewer.", task="Review PR #42", cwd="/tmp")
        assert "You are a reviewer." in req.prompt
        assert "Review PR #42" in req.prompt
        assert "---" in req.prompt
        assert "## Current Task" in req.prompt

    def test_default_values(self) -> None:
        req = Request(system_prompt="sp", task="t", cwd="/tmp")
        assert req.timeout == 600.0
        assert req.model is None
        assert req.skip_permissions is True
        assert req.session_name is None
        assert req.env is None
        assert req.tmux_persist is False
        assert req.role == "unknown"

    def test_custom_values(self) -> None:
        req = Request(
            system_prompt="sp", task="t", cwd="/tmp",
            timeout=120.0, model="claude-sonnet-4-6", skip_permissions=False,
            session_name="sess1", env={"FOO": "bar"}, tmux_persist=True, role="builder",
        )
        assert req.timeout == 120.0
        assert req.model == "claude-sonnet-4-6"
        assert req.skip_permissions is False
        assert req.session_name == "sess1"
        assert req.env == {"FOO": "bar"}
        assert req.tmux_persist is True
        assert req.role == "builder"


# ── Response tests ─────────────────────────────────────────────


class TestResponse:
    def test_construction(self) -> None:
        resp = Response(output="hello", exit_code=0)
        assert resp.output == "hello"
        assert resp.exit_code == 0
        assert resp.usage is None
        assert resp.error is None

    def test_with_error(self) -> None:
        resp = Response(output="", exit_code=1, error="timeout")
        assert resp.exit_code == 1
        assert resp.error == "timeout"


# ── Capability tests ───────────────────────────────────────────


class TestCapability:
    def test_enum_values(self) -> None:
        assert Capability.MODEL_OVERRIDE.value == "model_override"
        assert Capability.SESSION_RESUME.value == "session_resume"
        assert Capability.SYSTEM_PROMPT_FILE.value == "system_prompt_file"
        assert Capability.STREAMING.value == "streaming"
        assert Capability.INTERACTIVE.value == "interactive"
        assert Capability.SANDBOXING.value == "sandboxing"
        assert Capability.STRUCTURED_OUTPUT.value == "structured_output"

    def test_all_capabilities_count(self) -> None:
        assert len(Capability) == 7


# ── RunnerIdentity tests ──────────────────────────────────────


class TestRunnerIdentity:
    def test_binary_defaults_to_name(self) -> None:
        ident = RunnerIdentity(name="claude", display_name="Claude Code")
        assert ident.binary == "claude"

    def test_explicit_binary(self) -> None:
        ident = RunnerIdentity(name="claude", display_name="Claude Code", binary="claude-cli")
        assert ident.binary == "claude-cli"

    def test_capabilities_default_empty(self) -> None:
        ident = RunnerIdentity(name="test", display_name="Test")
        assert ident.capabilities == set()


# ── AgentRunner tests ──────────────────────────────────────────


class TestAgentRunnerCheckHealth:
    async def test_health_found(self) -> None:
        runner = StubRunner()
        with patch("shutil.which", return_value="/usr/bin/stub"):
            ok, msg = await runner.check_health()
        assert ok is True
        assert "/usr/bin/stub" in msg

    async def test_health_not_found(self) -> None:
        runner = StubRunner()
        with patch("shutil.which", return_value=None):
            ok, msg = await runner.check_health()
        assert ok is False
        assert "not found" in msg


class TestAgentRunnerBuildEnv:
    def test_strips_virtual_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.setenv("HOME", "/home/test")
        runner = StubRunner()
        req = Request(system_prompt="sp", task="t", cwd="/tmp")
        env = runner._build_env(req)
        assert "VIRTUAL_ENV" not in env
        assert env["HOME"] == "/home/test"

    def test_merges_request_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_CUSTOM_VAR", raising=False)
        runner = StubRunner()
        req = Request(system_prompt="sp", task="t", cwd="/tmp", env={"MY_CUSTOM_VAR": "val"})
        env = runner._build_env(req)
        assert env["MY_CUSTOM_VAR"] == "val"

    def test_request_env_overrides_os_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXISTING", "old")
        runner = StubRunner()
        req = Request(system_prompt="sp", task="t", cwd="/tmp", env={"EXISTING": "new"})
        env = runner._build_env(req)
        assert env["EXISTING"] == "new"


class TestAgentRunnerRun:
    async def test_run_success(self, tmp_path: Path) -> None:
        runner = StubRunner()
        req = Request(system_prompt="sys prompt", task="do stuff", cwd=str(tmp_path))

        mock_proc = AsyncMock()
        mock_proc.returncode = 0

        with patch("factory.runners.abstraction.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec, \
             patch("factory.runners.abstraction.stream_subprocess", return_value=(b"output text", b"")), \
             patch("factory.runners.abstraction.should_stream", return_value=False):
            resp = await runner.run(req)

        assert resp.output == "output text"
        assert resp.exit_code == 0
        # Verify _build_command was called (via create_subprocess_exec receiving the command)
        call_args = mock_exec.call_args
        cmd = call_args[0]
        assert cmd == ("echo", "hello")

    async def test_run_cleans_up_temp_file(self, tmp_path: Path) -> None:
        runner = StubRunner()
        req = Request(system_prompt="sys prompt", task="do stuff", cwd=str(tmp_path))

        mock_proc = AsyncMock()
        mock_proc.returncode = 0

        temp_files_created: list[str] = []
        original_ntf = __import__("tempfile").NamedTemporaryFile

        def track_tempfile(**kwargs):
            f = original_ntf(**kwargs)
            temp_files_created.append(f.name)
            return f

        with patch("factory.runners.abstraction.tempfile.NamedTemporaryFile", side_effect=track_tempfile), \
             patch("factory.runners.abstraction.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("factory.runners.abstraction.stream_subprocess", return_value=(b"ok", b"")), \
             patch("factory.runners.abstraction.should_stream", return_value=False):
            await runner.run(req)

        # Temp file should have been cleaned up
        assert len(temp_files_created) == 1
        assert not Path(temp_files_created[0]).exists()

    async def test_run_timeout(self, tmp_path: Path) -> None:
        runner = StubRunner()
        req = Request(system_prompt="sp", task="t", cwd=str(tmp_path), timeout=1.0)

        mock_proc = AsyncMock()
        mock_proc.returncode = -9

        with patch("factory.runners.abstraction.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("factory.runners.abstraction.stream_subprocess", side_effect=asyncio.TimeoutError), \
             patch("factory.runners.abstraction.should_stream", return_value=False):
            resp = await runner.run(req)

        assert resp.exit_code == 1
        assert "timed out" in resp.output.lower()
        assert resp.error is not None

    async def test_run_file_not_found(self, tmp_path: Path) -> None:
        runner = StubRunner()
        req = Request(system_prompt="sp", task="t", cwd=str(tmp_path))

        with patch("factory.runners.abstraction.asyncio.create_subprocess_exec", side_effect=FileNotFoundError), \
             patch("factory.runners.abstraction.should_stream", return_value=False):
            resp = await runner.run(req)

        assert resp.exit_code == 1
        assert "not found" in resp.output.lower()


class TestAgentRunnerInteractive:
    def test_run_interactive(self, tmp_path: Path) -> None:
        runner = StubRunner()
        req = Request(system_prompt="sp", task="t", cwd=str(tmp_path))

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0

        with patch("factory.runners.abstraction.subprocess.run", return_value=mock_result) as mock_run:
            code = runner.run_interactive(req)

        assert code == 0
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["echo", "hello"]


# ── Concrete runner instantiation tests ────────────────────────


class TestConcreteRunners:
    def test_claude_runner(self) -> None:
        from factory.runners.claude import ClaudeRunner
        r = ClaudeRunner()
        assert r.identity.name == "claude"
        assert r.identity.binary == "claude"
        assert r.identity.display_name

    def test_bob_runner(self) -> None:
        from factory.runners.bob import BobRunner
        r = BobRunner()
        assert r.identity.name == "bob"
        assert r.identity.display_name

    def test_codex_runner(self) -> None:
        from factory.runners.codex import CodexRunner
        r = CodexRunner()
        assert r.identity.name == "codex"
        assert r.identity.display_name

    def test_opencode_runner(self) -> None:
        from factory.runners.opencode import OpenCodeRunner
        r = OpenCodeRunner()
        assert r.identity.name == "opencode"
        assert r.identity.display_name

    def test_aider_runner(self) -> None:
        from factory.runners.aider import AiderRunner
        r = AiderRunner()
        assert r.identity.name == "aider"
        assert r.identity.display_name

    def test_all_runners_are_agent_runners(self) -> None:
        from factory.runners import _RUNNERS
        for name, cls in _RUNNERS.items():
            assert issubclass(cls, AgentRunner), f"{name} does not extend AgentRunner"
