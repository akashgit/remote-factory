"""Tests for factory/runners/abstraction.py — Request, Response, Capability, AgentRunner."""

from pathlib import Path

from factory.runners.abstraction import (
    AgentRunner,
    Capability,
    Request,
    Response,
    RunnerIdentity,
)


class TestRequest:
    def test_defaults(self, tmp_path: Path) -> None:
        r = Request(prompt="p", task="t", cwd=tmp_path)
        assert r.timeout == 600.0
        assert r.skip_permissions is True
        assert r.role == "unknown"
        assert r.allowed_tools is None
        assert r.disallowed_tools is None
        assert r.permission_mode is None
        assert r.max_budget_usd is None
        assert r.effort is None
        assert r.output_format is None
        assert r.append_system_prompt is None
        assert r.mcp_config is None

    def test_with_all_v2_fields(self, tmp_path: Path) -> None:
        r = Request(
            prompt="p",
            task="t",
            cwd=tmp_path,
            allowed_tools=["Bash", "Read"],
            disallowed_tools=["WebSearch"],
            permission_mode="auto",
            max_budget_usd=5.0,
            effort="high",
            output_format="json",
            append_system_prompt="Be careful.",
            mcp_config=["server.json"],
        )
        assert r.allowed_tools == ["Bash", "Read"]
        assert r.disallowed_tools == ["WebSearch"]
        assert r.permission_mode == "auto"
        assert r.max_budget_usd == 5.0
        assert r.effort == "high"
        assert r.output_format == "json"
        assert r.append_system_prompt == "Be careful."
        assert r.mcp_config == ["server.json"]


class TestResponse:
    def test_basic(self) -> None:
        r = Response(stdout="ok", return_code=0)
        assert r.stdout == "ok"
        assert r.return_code == 0
        assert r.usage is None


class TestCapability:
    def test_all_values(self) -> None:
        expected = {
            "model_override", "session_resume", "system_prompt_file",
            "streaming", "interactive", "sandboxing", "structured_output",
            "tool_filtering", "permission_modes", "budget_cap",
            "effort_control", "append_system_prompt", "mcp_config",
            "usage_tracking", "nesting",
        }
        actual = {c.value for c in Capability}
        assert actual == expected

    def test_enum_count(self) -> None:
        assert len(Capability) == 15


class TestRunnerIdentity:
    def test_fields(self) -> None:
        identity = RunnerIdentity(
            name="test",
            cli_command="test-cli",
            capabilities=frozenset({Capability.MODEL_OVERRIDE}),
        )
        assert identity.name == "test"
        assert identity.cli_command == "test-cli"
        assert Capability.MODEL_OVERRIDE in identity.capabilities


class TestAgentRunnerHelpers:
    """Test the prompt injection helpers on the base class."""

    class _StubRunner(AgentRunner):
        @property
        def identity(self) -> RunnerIdentity:
            return RunnerIdentity("stub", "stub", frozenset())

        def _build_command(self, request: Request) -> list[str]:
            return ["stub"]

        def _parse_response(self, stdout: str, stderr: str, return_code: int) -> Response:
            return Response(stdout=stdout, return_code=return_code)

    def test_inject_tool_restrictions_allowed(self, tmp_path: Path) -> None:
        runner = self._StubRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, allowed_tools=["Bash", "Read"])
        result = runner._inject_tool_restrictions("base prompt", req)
        assert "You may ONLY use these tools: Bash, Read" in result

    def test_inject_tool_restrictions_disallowed(self, tmp_path: Path) -> None:
        runner = self._StubRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, disallowed_tools=["WebSearch"])
        result = runner._inject_tool_restrictions("base prompt", req)
        assert "You must NOT use these tools: WebSearch" in result

    def test_inject_tool_restrictions_none(self, tmp_path: Path) -> None:
        runner = self._StubRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path)
        result = runner._inject_tool_restrictions("base prompt", req)
        assert result == "base prompt"

    def test_inject_effort_instructions(self) -> None:
        runner = self._StubRunner()
        result = runner._inject_effort_instructions("prompt", "high")
        assert "EFFORT LEVEL (high)" in result
        assert "Think step by step" in result

    def test_inject_effort_none(self) -> None:
        runner = self._StubRunner()
        result = runner._inject_effort_instructions("prompt", None)
        assert result == "prompt"

    def test_inject_effort_xhigh(self) -> None:
        runner = self._StubRunner()
        result = runner._inject_effort_instructions("prompt", "xhigh")
        assert "EFFORT LEVEL (xhigh)" in result

    def test_inject_append_system_prompt(self) -> None:
        runner = self._StubRunner()
        result = runner._inject_append_system_prompt("prompt", "extra text")
        assert result == "prompt\n\nextra text"

    def test_inject_append_system_prompt_none(self) -> None:
        runner = self._StubRunner()
        result = runner._inject_append_system_prompt("prompt", None)
        assert result == "prompt"

    def test_check_health_missing_cli(self) -> None:
        runner = self._StubRunner()
        # "stub" won't be on PATH
        assert runner.check_health() is False
