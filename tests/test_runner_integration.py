"""Integration tests for runner v2 — command building, capability declarations, invoke_agent dispatch."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.runners.abstraction import Capability, Request
from factory.runners.claude import ClaudeRunner
from factory.runners.codex import CodexRunner
from factory.runners.opencode import OpenCodeRunner


class TestClaudeRunnerBuildCommand:
    def test_basic_command(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path)
        cmd = runner._build_command(req)
        assert cmd[0] == "claude"
        assert "--dangerously-skip-permissions" in cmd
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "json"

    def test_allowed_tools(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, allowed_tools=["Bash", "Read"])
        cmd = runner._build_command(req)
        idx = cmd.index("--allowedTools")
        assert cmd[idx + 1] == "Bash"
        assert cmd[idx + 2] == "Read"

    def test_disallowed_tools(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, disallowed_tools=["WebSearch"])
        cmd = runner._build_command(req)
        idx = cmd.index("--disallowedTools")
        assert cmd[idx + 1] == "WebSearch"

    def test_permission_mode(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, permission_mode="plan")
        cmd = runner._build_command(req)
        assert "--permission-mode" in cmd
        assert cmd[cmd.index("--permission-mode") + 1] == "plan"
        assert "--dangerously-skip-permissions" not in cmd

    def test_max_budget_usd(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, max_budget_usd=10.0)
        cmd = runner._build_command(req)
        assert "--max-budget-usd" in cmd
        assert cmd[cmd.index("--max-budget-usd") + 1] == "10.0"

    def test_effort(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, effort="high")
        cmd = runner._build_command(req)
        assert "--effort" in cmd
        assert cmd[cmd.index("--effort") + 1] == "high"

    def test_append_system_prompt(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, append_system_prompt="Be careful")
        cmd = runner._build_command(req)
        assert "--append-system-prompt" in cmd
        assert cmd[cmd.index("--append-system-prompt") + 1] == "Be careful"

    def test_mcp_config(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, mcp_config=["a.json", "b.json"])
        cmd = runner._build_command(req)
        indices = [i for i, x in enumerate(cmd) if x == "--mcp-config"]
        assert len(indices) == 2
        assert cmd[indices[0] + 1] == "a.json"
        assert cmd[indices[1] + 1] == "b.json"

    def test_output_format_override(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, output_format="stream-json")
        cmd = runner._build_command(req)
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"

    def test_model_and_session_name(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, model="opus", session_name="test-sess")
        cmd = runner._build_command(req)
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "opus"
        assert "--name" in cmd
        assert cmd[cmd.index("--name") + 1] == "test-sess"


class TestClaudeRunnerCapabilities:
    def test_claude_has_all_native_capabilities(self) -> None:
        runner = ClaudeRunner()
        caps = runner.identity.capabilities
        assert Capability.TOOL_FILTERING in caps
        assert Capability.PERMISSION_MODES in caps
        assert Capability.BUDGET_CAP in caps
        assert Capability.EFFORT_CONTROL in caps
        assert Capability.APPEND_SYSTEM_PROMPT in caps
        assert Capability.MCP_CONFIG in caps
        assert Capability.USAGE_TRACKING in caps
        assert Capability.STREAMING in caps

    def test_claude_identity_name(self) -> None:
        runner = ClaudeRunner()
        assert runner.identity.name == "claude"
        assert runner.identity.cli_command == "claude"


class TestCodexRunnerBuildCommand:
    def test_basic_command(self, tmp_path: Path) -> None:
        runner = CodexRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path)
        cmd = runner._build_command(req)
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"
        assert "--sandbox" in cmd

    def test_permission_mode_bypass(self, tmp_path: Path) -> None:
        runner = CodexRunner()
        req = Request(
            prompt="p", task="t", cwd=tmp_path,
            permission_mode="bypassPermissions", skip_permissions=False,
        )
        cmd = runner._build_command(req)
        assert "--sandbox" in cmd
        assert "--ask-for-approval" in cmd

    def test_unsupported_fields_warn(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        runner = CodexRunner()
        req = Request(
            prompt="p", task="t", cwd=tmp_path,
            max_budget_usd=5.0,
            mcp_config=["server.json"],
        )
        with caplog.at_level("WARNING"):
            runner._warn_unsupported(req)
        assert "max_budget_usd" in caplog.text
        assert "mcp_config" in caplog.text


class TestCodexRunnerCapabilities:
    def test_codex_capabilities(self) -> None:
        runner = CodexRunner()
        caps = runner.identity.capabilities
        assert Capability.MODEL_OVERRIDE in caps
        assert Capability.SANDBOXING in caps
        assert Capability.TOOL_FILTERING not in caps
        assert Capability.MCP_CONFIG not in caps

    def test_codex_identity(self) -> None:
        runner = CodexRunner()
        assert runner.identity.name == "codex"


class TestOpenCodeRunnerBuildCommand:
    def test_basic_command(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path)
        cmd = runner._build_command(req)
        assert cmd[0] == "opencode"
        assert "--dangerously-skip-permissions" in cmd
        assert "--format" in cmd
        assert cmd[cmd.index("--format") + 1] == "json"

    def test_effort_maps_to_variant(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        for effort, expected_variant in [("low", "minimal"), ("high", "high"), ("max", "max")]:
            req = Request(prompt="p", task="t", cwd=tmp_path, effort=effort)
            cmd = runner._build_command(req)
            assert "--variant" in cmd, f"Missing --variant for effort={effort}"
            assert cmd[cmd.index("--variant") + 1] == expected_variant

    def test_effort_medium_no_variant(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, effort="medium")
        cmd = runner._build_command(req)
        # medium maps to "default" which should not produce a --variant flag
        assert "--variant" not in cmd

    def test_session_name(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, session_name="my-session")
        cmd = runner._build_command(req)
        assert "--session" in cmd
        assert cmd[cmd.index("--session") + 1] == "my-session"

    def test_unsupported_fields_warn(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        runner = OpenCodeRunner()
        req = Request(
            prompt="p", task="t", cwd=tmp_path,
            max_budget_usd=3.0,
            mcp_config=["cfg.json"],
        )
        with caplog.at_level("WARNING"):
            runner._warn_unsupported(req)
        assert "max_budget_usd" in caplog.text
        assert "mcp_config" in caplog.text


class TestOpenCodeRunnerCapabilities:
    def test_opencode_capabilities(self) -> None:
        runner = OpenCodeRunner()
        caps = runner.identity.capabilities
        assert Capability.MODEL_OVERRIDE in caps
        assert Capability.EFFORT_CONTROL in caps
        assert Capability.STRUCTURED_OUTPUT in caps
        assert Capability.SESSION_RESUME in caps
        assert Capability.MCP_CONFIG not in caps

    def test_opencode_identity(self) -> None:
        runner = OpenCodeRunner()
        assert runner.identity.name == "opencode"
        assert runner.identity.cli_command == "opencode"


class TestGetRunnerOpenCode:
    def test_get_opencode_runner(self) -> None:
        from factory.runners import get_runner

        runner = get_runner("opencode")
        assert runner.name == "opencode"

    def test_runner_name_includes_opencode(self) -> None:
        from factory.runners import _RUNNERS

        assert "opencode" in _RUNNERS


class TestInvokeAgentV2Dispatch:
    """Test that invoke_agent passes v2 fields through to AgentRunner.run()."""

    async def test_v2_fields_dispatch_to_run(self, tmp_path: Path) -> None:
        """When v2 fields are provided and runner has run(), it should use Request-based dispatch."""
        from factory.agents.runner import invoke_agent

        mock_response = type("Response", (), {
            "stdout": "ok",
            "return_code": 0,
            "usage": None,
        })()

        with (
            patch("factory.agents.runner.get_runner") as mock_get_runner,
            patch("factory.agents.runner.resolve_prompt", return_value="prompt"),
            patch("factory.agents.runner._emit_safe"),
            patch("factory.agents.runner._save_review"),
        ):
            mock_runner = AsyncMock()
            mock_runner.name = "claude"
            mock_runner.run = AsyncMock(return_value=mock_response)
            mock_runner.headless = AsyncMock(return_value=("ok", 0, None))
            mock_get_runner.return_value = mock_runner

            stdout, return_code = await invoke_agent(
                "builder",
                "build it",
                tmp_path,
                _track_failures=False,
                allowed_tools=["Bash"],
                effort="high",
            )

            assert stdout == "ok"
            assert return_code == 0
            # run() should have been called, not headless()
            mock_runner.run.assert_called_once()
            mock_runner.headless.assert_not_called()

            # Verify the Request passed to run() has the v2 fields
            call_args = mock_runner.run.call_args
            request = call_args[0][0]
            assert request.allowed_tools == ["Bash"]
            assert request.effort == "high"

    async def test_legacy_dispatch_without_v2_fields(self, tmp_path: Path) -> None:
        """Without v2 fields, invoke_agent should fall back to headless()."""
        from factory.agents.runner import invoke_agent

        with (
            patch("factory.agents.runner.get_runner") as mock_get_runner,
            patch("factory.agents.runner.resolve_prompt", return_value="prompt"),
            patch("factory.agents.runner._emit_safe"),
            patch("factory.agents.runner._save_review"),
        ):
            mock_runner = AsyncMock()
            mock_runner.name = "claude"
            mock_runner.run = AsyncMock()
            mock_runner.headless = AsyncMock(return_value=("ok", 0, None))
            mock_get_runner.return_value = mock_runner

            stdout, return_code = await invoke_agent(
                "builder",
                "build it",
                tmp_path,
                _track_failures=False,
            )

            assert stdout == "ok"
            assert return_code == 0
            mock_runner.headless.assert_called_once()
            mock_runner.run.assert_not_called()
