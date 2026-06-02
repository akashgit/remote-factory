"""Tests for Phase H3 — ACPAdapter, CodexRunner v2, OpenCodeRunner."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from factory.runners.acp_adapter import ACPAdapter
from factory.runners.codex import CodexRunner
from factory.runners.opencode import OpenCodeRunner
from factory.runners.types import RunnerCapability, RunnerRequest


# -- ACPAdapter ---------------------------------------------------------------

class TestACPAdapterInit:
    def test_capabilities_include_acp(self) -> None:
        adapter = ACPAdapter(
            command=["my-agent", "--headless"],
            name="myagent",
            display_name="My Agent",
        )
        assert RunnerCapability.ACP in adapter.info.capabilities
        assert RunnerCapability.EXECUTION_TRACE in adapter.info.capabilities

    def test_custom_capabilities_merged(self) -> None:
        adapter = ACPAdapter(
            command=["my-agent"],
            name="myagent",
            display_name="My Agent",
            capabilities={RunnerCapability.MODEL_OVERRIDE},
        )
        caps = adapter.info.capabilities
        assert RunnerCapability.ACP in caps
        assert RunnerCapability.MODEL_OVERRIDE in caps

    def test_info_fields(self) -> None:
        adapter = ACPAdapter(
            command=["agent-bin", "run"],
            name="test",
            display_name="Test Agent",
        )
        assert adapter.info.name == "test"
        assert adapter.info.display_name == "Test Agent"


class TestACPAdapterHealthCheck:
    async def test_health_fails_without_acp_package(self) -> None:
        adapter = ACPAdapter(
            command=["my-agent"],
            name="myagent",
            display_name="My Agent",
        )
        with patch("factory.runners.acp_adapter.ACP_AVAILABLE", False):
            ok, msg = await adapter.check_health()
        assert ok is False
        assert "not installed" in msg

    async def test_health_checks_binary_when_acp_available(self) -> None:
        adapter = ACPAdapter(
            command=["nonexistent-binary-xyz"],
            name="myagent",
            display_name="My Agent",
        )
        with patch("factory.runners.acp_adapter.ACP_AVAILABLE", True):
            ok, msg = await adapter.check_health()
        assert ok is False
        assert "not found" in msg

    async def test_health_passes_when_binary_found(self) -> None:
        adapter = ACPAdapter(
            command=["python"],
            name="myagent",
            display_name="My Agent",
        )
        with patch("factory.runners.acp_adapter.ACP_AVAILABLE", True):
            ok, msg = await adapter.check_health()
        assert ok is True


class TestACPAdapterHeadless:
    async def test_headless_fails_without_acp_package(self) -> None:
        adapter = ACPAdapter(
            command=["my-agent"],
            name="myagent",
            display_name="My Agent",
        )
        request = RunnerRequest(prompt="test", cwd="/tmp")
        with patch("factory.runners.acp_adapter.ACP_AVAILABLE", False):
            resp = await adapter.headless(request)
        assert resp.exit_code == 1
        assert "not installed" in resp.output


class TestACPAdapterBuildCommand:
    def test_build_command_includes_prompt(self) -> None:
        adapter = ACPAdapter(
            command=["my-agent", "--headless"],
            name="myagent",
            display_name="My Agent",
        )
        request = RunnerRequest(prompt="do the thing", cwd="/tmp")
        cmd = adapter._build_command(request)
        assert cmd[0] == "my-agent"
        assert cmd[1] == "--headless"
        assert "do the thing" in cmd

    def test_build_command_with_prompt_file(self) -> None:
        adapter = ACPAdapter(
            command=["my-agent"],
            name="myagent",
            display_name="My Agent",
        )
        request = RunnerRequest(prompt="hello", cwd="/tmp")
        cmd = adapter._build_command(request, prompt_file="/tmp/prompt.md")
        assert "/tmp/prompt.md" in cmd


class TestACPAdapterParseOutput:
    def test_parse_output_passthrough(self) -> None:
        adapter = ACPAdapter(
            command=["my-agent"],
            name="myagent",
            display_name="My Agent",
        )
        resp = adapter._parse_output("hello world", "err", 0)
        assert resp.output == "hello world"
        assert resp.exit_code == 0


# -- CodexRunner v2 -----------------------------------------------------------

class TestCodexRunnerV2:
    def test_extends_cli_adapter(self) -> None:
        runner = CodexRunner()
        assert runner.info.name == "codex"
        assert runner.info.display_name == "OpenAI Codex"
        assert RunnerCapability.MODEL_OVERRIDE in runner.info.capabilities
        assert RunnerCapability.SANDBOXING in runner.info.capabilities

    def test_build_command_basic(self) -> None:
        runner = CodexRunner()
        request = RunnerRequest(prompt="say hello", cwd="/tmp")
        cmd = runner._build_command(request)
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"
        assert "say hello" in cmd
        assert "--sandbox" in cmd
        assert "workspace-write" in cmd
        assert "--ask-for-approval" in cmd
        assert "never" in cmd

    def test_build_command_with_model(self) -> None:
        runner = CodexRunner()
        request = RunnerRequest(prompt="test", cwd="/tmp", model="gpt-5.4")
        cmd = runner._build_command(request)
        assert "--model" in cmd
        assert "gpt-5.4" in cmd

    def test_build_command_no_model(self) -> None:
        runner = CodexRunner()
        request = RunnerRequest(prompt="test", cwd="/tmp")
        cmd = runner._build_command(request)
        assert "--model" not in cmd

    def test_parse_output(self) -> None:
        runner = CodexRunner()
        resp = runner._parse_output("result text", "stderr", 0)
        assert resp.output == "result text"
        assert resp.exit_code == 0

    def test_build_env_maps_codex_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "my-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        runner = CodexRunner()
        request = RunnerRequest(prompt="test", cwd="/tmp")
        env = runner._build_env(request)
        assert env["OPENAI_API_KEY"] == "my-key"
        assert "VIRTUAL_ENV" not in env

    def test_build_env_preserves_openai_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "codex-key")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        runner = CodexRunner()
        request = RunnerRequest(prompt="test", cwd="/tmp")
        env = runner._build_env(request)
        assert env["OPENAI_API_KEY"] == "openai-key"

    async def test_check_health_no_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        runner = CodexRunner()
        with patch("shutil.which", return_value="/usr/bin/codex"):
            ok, msg = await runner.check_health()
        assert ok is False
        assert "not set" in msg

    async def test_check_health_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        runner = CodexRunner()
        with patch("shutil.which", return_value="/usr/bin/codex"):
            ok, msg = await runner.check_health()
        assert ok is True

    async def test_check_health_no_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        runner = CodexRunner()
        with patch("shutil.which", return_value=None):
            ok, msg = await runner.check_health()
        assert ok is False
        assert "not found" in msg


# -- OpenCodeRunner -----------------------------------------------------------

class TestOpenCodeRunner:
    def test_info(self) -> None:
        runner = OpenCodeRunner()
        assert runner.info.name == "opencode"
        assert runner.info.display_name == "OpenCode"
        assert RunnerCapability.MODEL_OVERRIDE in runner.info.capabilities
        assert RunnerCapability.STRUCTURED_OUTPUT in runner.info.capabilities

    def test_build_command(self) -> None:
        runner = OpenCodeRunner()
        request = RunnerRequest(prompt="analyze this code", cwd="/tmp")
        cmd = runner._build_command(request)
        assert cmd == ["opencode", "run", "--format", "json", "analyze this code"]

    def test_parse_output(self) -> None:
        runner = OpenCodeRunner()
        resp = runner._parse_output('{"result": "ok"}', "", 0)
        assert resp.output == '{"result": "ok"}'
        assert resp.exit_code == 0

    def test_parse_output_nonzero(self) -> None:
        runner = OpenCodeRunner()
        resp = runner._parse_output("error", "stderr", 1)
        assert resp.exit_code == 1

    async def test_check_health_no_binary(self) -> None:
        runner = OpenCodeRunner()
        with patch("shutil.which", return_value=None):
            ok, msg = await runner.check_health()
        assert ok is False
        assert "not found" in msg

    async def test_check_health_found(self) -> None:
        runner = OpenCodeRunner()
        with patch("shutil.which", return_value="/usr/bin/opencode"):
            ok, msg = await runner.check_health()
        assert ok is True

    def test_get_runner_opencode(self) -> None:
        from factory.runners import get_runner
        runner = get_runner("opencode")
        assert runner.info.name == "opencode"
