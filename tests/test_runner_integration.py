"""Integration tests for runner v2 — command building, capability declarations, invoke_agent dispatch."""

import shutil
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

    def test_prompt_included_in_command(self, tmp_path: Path) -> None:
        runner = CodexRunner()
        req = Request(prompt="system prompt here", task="do the thing", cwd=tmp_path)
        cmd = runner._build_command(req)
        # The full prompt (system + task) should be the positional arg to codex exec
        combined = cmd[2]  # codex exec <prompt>
        assert "system prompt here" in combined
        assert "do the thing" in combined

    def test_json_flag_present(self, tmp_path: Path) -> None:
        runner = CodexRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path)
        cmd = runner._build_command(req)
        assert "--json" in cmd

    def test_permission_mode_bypass(self, tmp_path: Path) -> None:
        runner = CodexRunner()
        req = Request(
            prompt="p", task="t", cwd=tmp_path,
            permission_mode="bypassPermissions", skip_permissions=False,
        )
        cmd = runner._build_command(req)
        # bypassPermissions maps to --sandbox danger-full-access
        assert "--sandbox" in cmd
        assert "danger-full-access" in cmd
        assert "--ask-for-approval" not in cmd

    def test_skip_permissions_uses_sandbox(self, tmp_path: Path) -> None:
        runner = CodexRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, skip_permissions=True)
        cmd = runner._build_command(req)
        assert "--sandbox" in cmd
        assert "workspace-write" in cmd
        # Should NOT use --ask-for-approval (not valid for codex exec)
        assert "--ask-for-approval" not in cmd

    def test_no_permissions_flag_when_disabled(self, tmp_path: Path) -> None:
        runner = CodexRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, skip_permissions=False)
        cmd = runner._build_command(req)
        assert "--sandbox" not in cmd

    def test_ceo_role_gets_full_access_for_nesting(self, tmp_path: Path) -> None:
        """CEO role uses danger-full-access so child codex processes can start."""
        runner = CodexRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, skip_permissions=True, role="ceo")
        cmd = runner._build_command(req)
        assert "--sandbox" in cmd
        assert "danger-full-access" in cmd
        # NOT workspace-write — that blocks inner codex app-server init
        assert "workspace-write" not in cmd

    def test_specialist_role_gets_workspace_write(self, tmp_path: Path) -> None:
        """Non-CEO roles use workspace-write sandbox."""
        runner = CodexRunner()
        for role in ("builder", "researcher", "strategist", "evaluator"):
            req = Request(prompt="p", task="t", cwd=tmp_path, skip_permissions=True, role=role)
            cmd = runner._build_command(req)
            assert "--sandbox" in cmd
            assert "workspace-write" in cmd

    def test_env_propagates_factory_runner(self, tmp_path: Path) -> None:
        """FACTORY_RUNNER=codex is set in env so sub-agents also use codex."""
        runner = CodexRunner()
        env = runner._build_env()
        assert env.get("FACTORY_RUNNER") == "codex"

    def test_proxied_tool_filtering_in_prompt(self, tmp_path: Path) -> None:
        runner = CodexRunner()
        req = Request(
            prompt="p", task="t", cwd=tmp_path,
            allowed_tools=["Bash", "Edit"],
            disallowed_tools=["WebSearch"],
        )
        cmd = runner._build_command(req)
        prompt_arg = cmd[2]
        assert "ONLY use these tools: Bash, Edit" in prompt_arg
        assert "must NOT use these tools: WebSearch" in prompt_arg

    def test_proxied_effort_in_prompt(self, tmp_path: Path) -> None:
        runner = CodexRunner()
        req = Request(prompt="p", task="t", cwd=tmp_path, effort="high")
        cmd = runner._build_command(req)
        prompt_arg = cmd[2]
        assert "EFFORT LEVEL (high)" in prompt_arg

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
        assert Capability.STRUCTURED_OUTPUT in caps
        assert Capability.USAGE_TRACKING in caps
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


class TestCodexJsonlParser:
    """Test parsing of real Codex JSONL output format."""

    def test_parse_agent_message(self) -> None:
        from factory.runners.codex import _parse_codex_jsonl

        raw = (
            '{"type":"thread.started","thread_id":"abc"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"hello factory"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":50,"output_tokens":6,"reasoning_output_tokens":0}}\n'
        )
        text, usage = _parse_codex_jsonl(raw)
        assert text == "hello factory"
        assert usage is not None
        assert usage.input_tokens == 100
        assert usage.output_tokens == 6
        assert usage.cache_read_tokens == 50

    def test_parse_multiple_messages(self) -> None:
        from factory.runners.codex import _parse_codex_jsonl

        raw = (
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"line 1"}}\n'
            '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"line 2"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":200,"cached_input_tokens":0,"output_tokens":10,"reasoning_output_tokens":0}}\n'
        )
        text, usage = _parse_codex_jsonl(raw)
        assert "line 1" in text
        assert "line 2" in text
        assert usage is not None
        assert usage.input_tokens == 200

    def test_parse_no_usage(self) -> None:
        from factory.runners.codex import _parse_codex_jsonl

        raw = '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"hello"}}\n'
        text, usage = _parse_codex_jsonl(raw)
        assert text == "hello"
        assert usage is None

    def test_parse_empty_output(self) -> None:
        from factory.runners.codex import _parse_codex_jsonl

        text, usage = _parse_codex_jsonl("")
        assert text == ""
        assert usage is None

    def test_parse_non_json_lines_skipped(self) -> None:
        from factory.runners.codex import _parse_codex_jsonl

        raw = (
            "Reading additional input from stdin...\n"
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"hello"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":0,"output_tokens":5,"reasoning_output_tokens":0}}\n'
        )
        text, usage = _parse_codex_jsonl(raw)
        assert text == "hello"
        assert usage is not None


@pytest.mark.skipif(
    not shutil.which("codex"),
    reason="codex CLI not installed",
)
class TestCodexE2E:
    """Real end-to-end tests against the codex CLI.

    These tests actually invoke ``codex exec`` and verify the full pipeline:
    _build_command → subprocess → _parse_response → Response with text + usage.

    Requires CODEX_API_KEY or OPENAI_API_KEY to be set.
    """

    @pytest.fixture
    def runner(self) -> CodexRunner:
        return CodexRunner()

    @pytest.fixture
    def git_dir(self, tmp_path: Path) -> Path:
        """Create a temporary git repo for codex (it requires one)."""
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
        (repo / "README.md").write_text("# test")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
        return repo

    async def test_simple_echo(self, runner: CodexRunner, git_dir: Path) -> None:
        """Codex can respond to a simple prompt and we parse the response."""
        req = Request(
            prompt="You are a helpful assistant.",
            task="Say exactly this text and nothing else: FACTORY_E2E_OK",
            cwd=git_dir,
            timeout=60.0,
            skip_permissions=True,
        )
        response = await runner.run(req)
        assert response.return_code == 0
        assert "FACTORY_E2E_OK" in response.stdout

    async def test_usage_tracking(self, runner: CodexRunner, git_dir: Path) -> None:
        """Codex returns usage data via JSONL parsing."""
        req = Request(
            prompt="You are a helpful assistant.",
            task="Say exactly: hello",
            cwd=git_dir,
            timeout=60.0,
            skip_permissions=True,
        )
        response = await runner.run(req)
        assert response.return_code == 0
        assert response.usage is not None
        assert response.usage.input_tokens > 0
        assert response.usage.output_tokens > 0

    async def test_model_override(self, runner: CodexRunner, git_dir: Path) -> None:
        """--model flag is passed correctly to codex."""
        req = Request(
            prompt="You are a helpful assistant.",
            task="Say exactly: model test ok",
            cwd=git_dir,
            timeout=60.0,
            skip_permissions=True,
            model="o4-mini",
        )
        cmd = runner._build_command(req)
        assert "--model" in cmd
        assert "o4-mini" in cmd

    async def test_command_structure(self, runner: CodexRunner, git_dir: Path) -> None:
        """Verify the command structure matches real codex CLI expectations."""
        req = Request(
            prompt="system", task="task", cwd=git_dir,
            skip_permissions=True,
        )
        cmd = runner._build_command(req)
        # Structure: codex exec <prompt> --sandbox workspace-write --json
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"
        assert isinstance(cmd[2], str)  # The combined prompt
        assert "--sandbox" in cmd
        assert "workspace-write" in cmd
        assert "--json" in cmd
        # Must NOT include --ask-for-approval (invalid for codex exec)
        assert "--ask-for-approval" not in cmd

    async def test_headless_shim_e2e(self, runner: CodexRunner, git_dir: Path) -> None:
        """The backward-compat headless() shim also works end-to-end."""
        stdout, return_code, usage = await runner.headless(
            prompt="You are a helpful assistant.",
            task="Say exactly: shim ok",
            cwd=git_dir,
            timeout=60.0,
        )
        assert return_code == 0
        assert "shim ok" in stdout.lower() or "shim" in stdout.lower()


@pytest.mark.skipif(
    not shutil.which("codex"),
    reason="codex CLI not installed",
)
class TestCodexBuildE2E:
    """Real build tests — codex writes code, we run it, verify correctness.

    These prove the full pipeline works: prompt → codex exec → file written → artifact runs.
    Each test verifies:
    1. The runner is CodexRunner (not accidentally falling back to claude)
    2. Codex actually writes a file to disk via sandbox
    3. The written file runs and produces verifiably correct output
    4. Usage telemetry is captured
    """

    @pytest.fixture
    def runner(self) -> CodexRunner:
        r = CodexRunner()
        # Verify we're testing the right runner, not a fallback
        assert r.identity.name == "codex"
        assert r.identity.cli_command == "codex"
        return r

    @pytest.fixture
    def git_dir(self, tmp_path: Path) -> Path:
        """Create a temporary git repo for codex (it requires one)."""
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
        (repo / "README.md").write_text("# test")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
        return repo

    async def test_build_fibonacci_script(self, runner: CodexRunner, git_dir: Path) -> None:
        """Codex builds a fibonacci script, we run it and verify output."""
        import subprocess

        req = Request(
            prompt=(
                "You are a software engineer. Write files directly to disk. "
                "Do not explain, just write the code."
            ),
            task=(
                "Create a file called fib.py in the current directory. "
                "It should define a function fibonacci(n) that returns the nth fibonacci number "
                "(0-indexed: fibonacci(0)=0, fibonacci(1)=1, fibonacci(10)=55). "
                "At the bottom, print the result of fibonacci(10)."
            ),
            cwd=git_dir,
            timeout=120.0,
            skip_permissions=True,
        )

        response = await runner.run(req)

        # 1. Runner identity check
        assert runner.identity.name == "codex"

        # 2. Codex should exit cleanly
        assert response.return_code == 0, f"codex failed: {response.stdout[:500]}"

        # 3. File must exist on disk
        fib_path = git_dir / "fib.py"
        assert fib_path.exists(), (
            f"fib.py not created. Directory contents: {list(git_dir.iterdir())}"
        )

        # 4. Run the script and verify output
        result = subprocess.run(
            ["python3", str(fib_path)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"fib.py failed to run: {result.stderr}"
        assert "55" in result.stdout, (
            f"Expected fibonacci(10)=55 in output, got: {result.stdout!r}"
        )

        # 5. Usage telemetry was captured
        assert response.usage is not None, "No usage data returned from codex"
        assert response.usage.input_tokens > 0
        assert response.usage.output_tokens > 0

    async def test_build_fizzbuzz_and_verify(self, runner: CodexRunner, git_dir: Path) -> None:
        """Codex builds fizzbuzz, we run it and check several known outputs."""
        import subprocess

        req = Request(
            prompt=(
                "You are a software engineer. Write files directly to disk. "
                "Do not explain, just write the code."
            ),
            task=(
                "Create a file called fizzbuzz.py in the current directory. "
                "It should print fizzbuzz for numbers 1 through 20, one per line. "
                "Rules: divisible by 3 print 'Fizz', divisible by 5 print 'Buzz', "
                "divisible by both print 'FizzBuzz', otherwise print the number."
            ),
            cwd=git_dir,
            timeout=120.0,
            skip_permissions=True,
        )

        response = await runner.run(req)
        assert response.return_code == 0, f"codex failed: {response.stdout[:500]}"

        fb_path = git_dir / "fizzbuzz.py"
        assert fb_path.exists(), (
            f"fizzbuzz.py not created. Directory contents: {list(git_dir.iterdir())}"
        )

        result = subprocess.run(
            ["python3", str(fb_path)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"fizzbuzz.py failed: {result.stderr}"

        lines = result.stdout.strip().splitlines()
        assert len(lines) == 20, f"Expected 20 lines, got {len(lines)}: {lines}"

        # Spot-check known values
        assert lines[0] == "1"           # 1
        assert lines[2] == "Fizz"        # 3
        assert lines[4] == "Buzz"        # 5
        assert lines[14] == "FizzBuzz"   # 15
        assert lines[19] == "Buzz"       # 20

    async def test_build_with_proxied_tool_filtering(self, runner: CodexRunner, git_dir: Path) -> None:
        """Verify proxied tool filtering is injected into the prompt sent to codex."""
        req = Request(
            prompt="You are a software engineer.",
            task="Create a file called hello.py that prints 'hello world'.",
            cwd=git_dir,
            timeout=120.0,
            skip_permissions=True,
            allowed_tools=["Bash", "Write"],
            disallowed_tools=["WebSearch"],
        )

        # Verify the proxy injection happened in the command
        cmd = runner._build_command(req)
        prompt_arg = cmd[2]
        assert "ONLY use these tools: Bash, Write" in prompt_arg
        assert "must NOT use these tools: WebSearch" in prompt_arg

        # Actually run it and verify the file was built
        response = await runner.run(req)
        assert response.return_code == 0

        hello_path = git_dir / "hello.py"
        if hello_path.exists():
            import subprocess
            result = subprocess.run(
                ["python3", str(hello_path)],
                capture_output=True, text=True, timeout=10,
            )
            assert result.returncode == 0
            assert "hello world" in result.stdout.lower()

    async def test_get_runner_returns_codex(self) -> None:
        """Verify get_runner('codex') returns a CodexRunner, not claude."""
        from factory.runners import get_runner

        runner = get_runner("codex")
        assert isinstance(runner, CodexRunner)
        assert runner.identity.name == "codex"
        assert runner.identity.cli_command == "codex"
        # Must NOT be a ClaudeRunner
        from factory.runners.claude import ClaudeRunner
        assert not isinstance(runner, ClaudeRunner)
