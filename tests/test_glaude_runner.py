"""Tests for factory/runners/glaude.py — GlaudeRunner implementation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.models import AgentRunRequest, AgentRunResult
from factory.runners import GlaudeRunner, get_runner, is_glaude_dry_run


# ---------------------------------------------------------------------------
# Runner selection
# ---------------------------------------------------------------------------


class TestGetRunnerGlaude:
    def test_explicit_glaude(self) -> None:
        runner = get_runner("glaude")
        assert runner.name == "glaude"

    def test_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "glaude")
        runner = get_runner()
        assert runner.name == "glaude"

    def test_explicit_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "glaude")
        runner = get_runner("claude")
        assert runner.name == "claude"

    def test_returns_glaude_runner_type(self) -> None:
        runner = get_runner("glaude")
        assert isinstance(runner, GlaudeRunner)


# ---------------------------------------------------------------------------
# Dry-run flag
# ---------------------------------------------------------------------------


class TestGlaudeDryRun:
    def test_dry_run_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_GLAUDE_DRY_RUN", "1")
        assert is_glaude_dry_run() is True

    def test_dry_run_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FACTORY_GLAUDE_DRY_RUN", raising=False)
        assert is_glaude_dry_run() is False

    def test_dry_run_true_word(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_GLAUDE_DRY_RUN", "true")
        assert is_glaude_dry_run() is True

    def test_dry_run_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_GLAUDE_DRY_RUN", "yes")
        assert is_glaude_dry_run() is True

    async def test_headless_dry_run_returns_stub(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FACTORY_GLAUDE_DRY_RUN", "1")

        runner = GlaudeRunner()
        result = await runner.headless(
            AgentRunRequest(
                prompt="You are a test agent.",
                task="Say hello",
                cwd=tmp_path,
                role="researcher",
            )
        )

        assert result.return_code == 0
        assert "[DRY-RUN]" in result.stdout
        assert "researcher" in result.stdout
        assert result.usage is None

    def test_interactive_run_dry_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("FACTORY_GLAUDE_DRY_RUN", "1")

        runner = GlaudeRunner()
        code = runner.interactive_run(
            AgentRunRequest(
                prompt="Test prompt",
                task="Test task",
                cwd=tmp_path,
                role="ceo",
            )
        )

        assert code == 0
        captured = capsys.readouterr()
        assert "[DRY-RUN]" in captured.out


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestGlaudeMetadata:
    def test_name(self) -> None:
        meta = GlaudeRunner.metadata()
        assert meta.name == "glaude"

    def test_display_name(self) -> None:
        meta = GlaudeRunner.metadata()
        assert meta.display_name == "Glaude (GLM-5.2)"

    def test_binary(self) -> None:
        meta = GlaudeRunner.metadata()
        assert meta.binary == "glaude"

    def test_no_required_env_vars(self) -> None:
        meta = GlaudeRunner.metadata()
        assert meta.required_env_vars == []

    def test_supports_usage_telemetry(self) -> None:
        meta = GlaudeRunner.metadata()
        assert meta.supports_usage_telemetry is True

    def test_supports_session_name(self) -> None:
        meta = GlaudeRunner.metadata()
        assert meta.supports_session_name is True

    def test_supports_background(self) -> None:
        meta = GlaudeRunner.metadata()
        assert meta.supports_background is True

    def test_supports_model_override(self) -> None:
        meta = GlaudeRunner.metadata()
        assert meta.supports_model_override is True

    def test_auth_always_passes_without_env_vars(self) -> None:
        meta = GlaudeRunner.metadata()
        assert meta.check_auth() is True


# ---------------------------------------------------------------------------
# build_command
# ---------------------------------------------------------------------------


class TestGlaudeBuildCommand:
    def test_uses_glaude_binary(self, tmp_path: Path) -> None:
        runner = GlaudeRunner()
        cmd, env, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="You are the CEO.",
                task="Run experiment",
                cwd=tmp_path,
                role="ceo",
            )
        )
        assert cmd[0] == "glaude"
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_includes_core_flags(self, tmp_path: Path) -> None:
        runner = GlaudeRunner()
        cmd, env, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="You are the CEO.",
                task="Run experiment",
                cwd=tmp_path,
                role="ceo",
            )
        )
        assert "--append-system-prompt-file" in cmd
        assert "-p" in cmd
        assert "Run experiment" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--verbose" in cmd
        assert "--disallowedTools" in cmd
        assert "Agent" in cmd
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_model_override(self, tmp_path: Path) -> None:
        runner = GlaudeRunner()
        cmd, env, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                model="glm-5.2-fp8",
            )
        )
        assert "--model" in cmd
        assert "glm-5.2-fp8" in cmd
        assert env.get("FACTORY_MODEL") == "glm-5.2-fp8"
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_no_model_flag_when_none(self, tmp_path: Path) -> None:
        runner = GlaudeRunner()
        cmd, env, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                model=None,
            )
        )
        assert "--model" not in cmd
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_skip_permissions_flag(self, tmp_path: Path) -> None:
        runner = GlaudeRunner()
        cmd, _, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                skip_permissions=True,
            )
        )
        assert "--dangerously-skip-permissions" in cmd
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_no_skip_permissions_by_default(self, tmp_path: Path) -> None:
        runner = GlaudeRunner()
        cmd, _, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                skip_permissions=False,
            )
        )
        assert "--dangerously-skip-permissions" not in cmd
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_session_name(self, tmp_path: Path) -> None:
        runner = GlaudeRunner()
        cmd, _, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                session_name="my-session",
            )
        )
        assert "--name" in cmd
        assert "my-session" in cmd
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_virtual_env_stripped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        runner = GlaudeRunner()
        _, env, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )
        )
        assert "VIRTUAL_ENV" not in env
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_creates_prompt_temp_file(self, tmp_path: Path) -> None:
        runner = GlaudeRunner()
        _, _, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="My prompt content",
                task="Test",
                cwd=tmp_path,
            )
        )
        assert len(temp_files) == 1
        assert temp_files[0].exists()
        assert "My prompt content" in temp_files[0].read_text()
        for f in temp_files:
            f.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# headless
# ---------------------------------------------------------------------------


class TestGlaudeHeadless:
    async def test_builds_correct_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_GLAUDE_DRY_RUN", raising=False)

        runner = GlaudeRunner()

        with patch(
            "factory.runners.glaude.run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = AgentRunResult(stdout="output", return_code=0)

            result = await runner.headless(
                AgentRunRequest(
                    prompt="You are a test agent.",
                    task="Say hello",
                    cwd=tmp_path,
                    timeout=60.0,
                    model="glm-5.2-fp8",
                )
            )

            assert result.return_code == 0
            assert result.stdout == "output"

            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert cmd[0] == "glaude"
            assert "--append-system-prompt-file" in cmd
            assert "-p" in cmd
            assert "--output-format" in cmd
            assert "stream-json" in cmd
            assert "--model" in cmd
            assert "glm-5.2-fp8" in cmd

    async def test_parses_json_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_GLAUDE_DRY_RUN", raising=False)

        runner = GlaudeRunner()
        json_output = (
            '{"type":"assistant","message":"hi"}\n'
            '{"result":"Final answer","usage":{"input_tokens":100,"output_tokens":50},'
            '"total_cost_usd":0.01,"duration_ms":1234,"num_turns":3,"model":"glm-5.2"}\n'
        )

        with patch(
            "factory.runners.glaude.run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = AgentRunResult(stdout=json_output, return_code=0)

            result = await runner.headless(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                )
            )

            assert result.stdout == "Final answer"
            assert result.usage is not None
            assert result.usage.input_tokens == 100
            assert result.usage.output_tokens == 50
            assert result.usage.total_cost_usd == 0.01

    async def test_runner_name_is_glaude(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_GLAUDE_DRY_RUN", raising=False)

        runner = GlaudeRunner()

        with patch(
            "factory.runners.glaude.run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = AgentRunResult(stdout="ok", return_code=0)

            await runner.headless(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    role="builder",
                )
            )

            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["runner_name"] == "glaude"
            assert call_kwargs["role"] == "builder"

    async def test_no_model_flag_when_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_GLAUDE_DRY_RUN", raising=False)

        runner = GlaudeRunner()

        with patch(
            "factory.runners.glaude.run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = AgentRunResult(stdout="ok", return_code=0)

            await runner.headless(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    model=None,
                )
            )

            cmd = mock_run.call_args[0][0]
            assert "--model" not in cmd

    async def test_env_strips_virtual_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.delenv("FACTORY_GLAUDE_DRY_RUN", raising=False)

        runner = GlaudeRunner()

        with patch(
            "factory.runners.glaude.run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = AgentRunResult(stdout="ok", return_code=0)

            await runner.headless(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                )
            )

            call_kwargs = mock_run.call_args.kwargs
            assert "VIRTUAL_ENV" not in call_kwargs["env"]


# ---------------------------------------------------------------------------
# build_interactive_command
# ---------------------------------------------------------------------------


class TestGlaudeBuildInteractiveCommand:
    def test_uses_glaude_binary(self, tmp_path: Path) -> None:
        runner = GlaudeRunner()
        cmd, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="You are the CEO.",
                task="Start session",
                cwd=tmp_path,
            )
        )
        assert cmd[0] == "glaude"
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_writes_claude_md(self, tmp_path: Path) -> None:
        runner = GlaudeRunner()
        _, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="My CEO prompt",
                task="Start session",
                cwd=tmp_path,
            )
        )
        claude_md = tmp_path / ".claude" / "CLAUDE.md"
        assert claude_md.exists()
        assert "My CEO prompt" in claude_md.read_text()
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_writes_settings_local_json(self, tmp_path: Path) -> None:
        import json
        runner = GlaudeRunner()
        _, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )
        )
        settings_path = tmp_path / ".claude" / "settings.local.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert settings["disallowedTools"] == ["Agent"]
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_skip_permissions_flag(self, tmp_path: Path) -> None:
        runner = GlaudeRunner()
        cmd, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                skip_permissions=True,
            )
        )
        assert "--dangerously-skip-permissions" in cmd
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_model_override(self, tmp_path: Path) -> None:
        runner = GlaudeRunner()
        cmd, env, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                model="glm-5.2-fp8",
            )
        )
        assert "--model" in cmd
        assert "glm-5.2-fp8" in cmd
        assert env.get("FACTORY_MODEL") == "glm-5.2-fp8"
        for f in temp_files:
            f.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# interactive_run
# ---------------------------------------------------------------------------


class TestGlaudeInteractive:
    def test_interactive_run_builds_correct_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_GLAUDE_DRY_RUN", raising=False)

        runner = GlaudeRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            code = runner.interactive_run(
                AgentRunRequest(
                    prompt="You are the CEO.",
                    task="Start session",
                    cwd=tmp_path,
                    model="glm-5.2-fp8",
                    skip_permissions=True,
                )
            )

            assert code == 0
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "glaude"
            assert "--dangerously-skip-permissions" in cmd
            assert "--model" in cmd
            assert "glm-5.2-fp8" in cmd

    def test_interactive_run_passes_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.delenv("FACTORY_GLAUDE_DRY_RUN", raising=False)

        runner = GlaudeRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            runner.interactive_run(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                )
            )

            call_kwargs = mock_run.call_args.kwargs
            assert "VIRTUAL_ENV" not in call_kwargs["env"]

    def test_interactive_cleans_temp_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_GLAUDE_DRY_RUN", raising=False)

        runner = GlaudeRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            runner.interactive_run(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                )
            )

        claude_md = tmp_path / ".claude" / "CLAUDE.md"
        settings = tmp_path / ".claude" / "settings.local.json"
        assert not claude_md.exists()
        assert not settings.exists()
