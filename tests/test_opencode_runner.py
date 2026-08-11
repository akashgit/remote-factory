"""Tests for factory/runners/opencode.py — OpenCode v1.x runner."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import factory.runners.opencode as oc_module
from factory.models import AgentRunRequest, AgentRunResult
from factory.runners.opencode import (
    OpenCodeAuthError,
    OpenCodeRunner,
    _check_auth,
    _check_binary_compat,
    _has_opencode_auth,
    is_opencode_dry_run,
)


@pytest.fixture(autouse=True)
def _reset_opencode_globals() -> None:
    """Reset module-level auth/compat guards before each test."""
    oc_module._auth_checked = False
    oc_module._compat_checked = False


# ---------------------------------------------------------------------------
# OpenCodeAuthError
# ---------------------------------------------------------------------------


class TestOpenCodeAuthError:
    def test_error_message(self) -> None:
        err = OpenCodeAuthError()
        assert "opencode auth login" in str(err)
        assert "ANTHROPIC_API_KEY" in str(err)
        assert "config.toml" in str(err)


# ---------------------------------------------------------------------------
# _has_opencode_auth
# ---------------------------------------------------------------------------


class TestHasOpenCodeAuth:
    def test_true_with_opencode_dir(self, tmp_path: Path) -> None:
        with patch("factory.runners.opencode.Path.home", return_value=tmp_path):
            (tmp_path / ".opencode").mkdir()
            assert _has_opencode_auth() is True

    def test_true_with_anthropic_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        with patch("factory.runners.opencode.Path.home", return_value=Path("/nonexistent")):
            assert _has_opencode_auth() is True

    def test_true_with_openai_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("factory.runners.opencode.Path.home", return_value=Path("/nonexistent")):
            assert _has_opencode_auth() is True

    def test_false_without_anything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in oc_module._PROVIDER_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        with patch("factory.runners.opencode.Path.home", return_value=Path("/nonexistent")):
            assert _has_opencode_auth() is False


# ---------------------------------------------------------------------------
# _check_auth
# ---------------------------------------------------------------------------


class TestCheckAuth:
    def test_skips_when_already_checked(self) -> None:
        oc_module._auth_checked = True
        _check_auth()

    def test_passes_with_opencode_dir(self, tmp_path: Path) -> None:
        with patch("factory.runners.opencode._check_binary_compat"):
            with patch("factory.runners.opencode.Path.home", return_value=tmp_path):
                (tmp_path / ".opencode").mkdir()
                _check_auth()
        assert oc_module._auth_checked is True

    def test_passes_with_env_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        with patch("factory.runners.opencode._check_binary_compat"):
            _check_auth()
        assert oc_module._auth_checked is True

    def test_raises_without_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in oc_module._PROVIDER_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        with patch("factory.runners.opencode._check_binary_compat"):
            with patch("factory.runners.opencode.Path.home", return_value=Path("/nonexistent")):
                with pytest.raises(OpenCodeAuthError, match="opencode auth login"):
                    _check_auth()


# ---------------------------------------------------------------------------
# _check_binary_compat
# ---------------------------------------------------------------------------


class TestCheckBinaryCompat:
    def test_skips_when_already_checked(self) -> None:
        oc_module._compat_checked = True
        _check_binary_compat()

    def test_returns_early_when_no_binary(self) -> None:
        with patch("shutil.which", return_value=None):
            _check_binary_compat()
        assert oc_module._compat_checked is True

    def test_v1x_detected_ok(self) -> None:
        mock_result = MagicMock(stdout="1.18.14", stderr="")
        with patch("shutil.which", return_value="/usr/local/bin/opencode"):
            with patch("factory.runners.opencode.subprocess.run", return_value=mock_result):
                _check_binary_compat()
        assert oc_module._compat_checked is True

    def test_v0x_warns(self) -> None:
        mock_result = MagicMock(stdout="opencode version v0.0.55", stderr="")
        with patch("shutil.which", return_value="/usr/local/bin/opencode"):
            with patch("factory.runners.opencode.subprocess.run", return_value=mock_result):
                _check_binary_compat()
        assert oc_module._compat_checked is True

    def test_file_not_found_handled(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/opencode"):
            with patch(
                "factory.runners.opencode.subprocess.run",
                side_effect=FileNotFoundError,
            ):
                _check_binary_compat()
        assert oc_module._compat_checked is True

    def test_timeout_handled(self) -> None:
        import subprocess

        with patch("shutil.which", return_value="/usr/local/bin/opencode"):
            with patch(
                "factory.runners.opencode.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="opencode", timeout=10),
            ):
                _check_binary_compat()
        assert oc_module._compat_checked is True


# ---------------------------------------------------------------------------
# OpenCodeRunner.metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_metadata_v1x(self) -> None:
        meta = OpenCodeRunner.metadata()
        assert meta.name == "opencode"
        assert meta.supports_model_override is True
        assert meta.supports_session_name is True
        assert meta.supports_session_resume is True
        assert meta.supports_background is False
        assert meta.required_env_vars == []
        assert "opencode.ai/install" in meta.install_hint
        assert meta.custom_auth_check is not None


# ---------------------------------------------------------------------------
# OpenCodeRunner.build_command
# ---------------------------------------------------------------------------


class TestBuildCommand:
    def test_basic_command_structure(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        cmd, env, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="You are the CEO.",
                task="Run experiment",
                cwd=tmp_path,
                role="ceo",
            )
        )

        assert cmd[0] == "opencode"
        assert cmd[1] == "run"
        assert cmd[2] == "Run experiment"
        assert "--format" in cmd
        assert "json" in cmd
        assert "--dir" in cmd
        assert str(tmp_path) in cmd
        assert "--auto" in cmd
        assert "-p" not in cmd
        assert "-c" not in cmd
        assert "-q" not in cmd
        assert "VIRTUAL_ENV" not in env

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md in temp_files
        assert agents_md.exists()
        assert agents_md.read_text() == "You are the CEO."

    def test_model_override(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        cmd, _, _ = runner.build_command(
            AgentRunRequest(
                prompt="test",
                task="test",
                cwd=tmp_path,
                role="ceo",
                model="anthropic/claude-sonnet-4-20250514",
            )
        )
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "anthropic/claude-sonnet-4-20250514"

    def test_session_name(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        cmd, _, _ = runner.build_command(
            AgentRunRequest(
                prompt="test",
                task="test",
                cwd=tmp_path,
                role="ceo",
                session_name="my-session",
            )
        )
        assert "--title" in cmd
        idx = cmd.index("--title")
        assert cmd[idx + 1] == "my-session"

    def test_session_resume(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        cmd, _, _ = runner.build_command(
            AgentRunRequest(
                prompt="test",
                task="test",
                cwd=tmp_path,
                role="ceo",
                resume_session_id="sess-abc",
            )
        )
        assert "--session" in cmd
        idx = cmd.index("--session")
        assert cmd[idx + 1] == "sess-abc"

    def test_session_continue(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        cmd, _, _ = runner.build_command(
            AgentRunRequest(
                prompt="test",
                task="test",
                cwd=tmp_path,
                role="ceo",
                session_id="any",
            )
        )
        assert "--continue" in cmd

    def test_no_auto_without_skip_permissions(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        cmd, _, _ = runner.build_command(
            AgentRunRequest(
                prompt="test",
                task="test",
                cwd=tmp_path,
                role="ceo",
                skip_permissions=False,
            )
        )
        assert "--auto" not in cmd


# ---------------------------------------------------------------------------
# OpenCodeRunner.build_interactive_command
# ---------------------------------------------------------------------------


class TestBuildInteractiveCommand:
    def test_interactive_no_run_subcommand(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        cmd, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="You are a test agent.",
                task="Start session",
                cwd=tmp_path,
                role="ceo",
                skip_permissions=False,
            )
        )
        assert cmd[0] == "opencode"
        assert "run" not in cmd
        assert "--format" not in cmd
        assert "--auto" not in cmd
        assert "--prompt" in cmd
        prompt_idx = cmd.index("--prompt")
        assert cmd[prompt_idx + 1] == "Start session"
        assert "--dir" not in cmd
        assert cmd[-1] == str(tmp_path)

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md in temp_files
        assert agents_md.exists()
        assert agents_md.read_text() == "You are a test agent."

    def test_interactive_no_title_flag(self, tmp_path: Path) -> None:
        """--title is only valid for 'opencode run', not the base TUI command."""
        runner = OpenCodeRunner()
        cmd, _, _ = runner.build_interactive_command(
            AgentRunRequest(
                prompt="test",
                task="test",
                cwd=tmp_path,
                role="ceo",
                session_name="factory: discover run-123",
            )
        )
        assert "--title" not in cmd

    def test_interactive_auto_with_skip_permissions(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        cmd, _, _ = runner.build_interactive_command(
            AgentRunRequest(
                prompt="test",
                task="test",
                cwd=tmp_path,
                role="ceo",
                skip_permissions=True,
            )
        )
        assert "--auto" in cmd

    def test_interactive_no_auto_without_skip_permissions(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        cmd, _, _ = runner.build_interactive_command(
            AgentRunRequest(
                prompt="test",
                task="test",
                cwd=tmp_path,
                role="ceo",
                skip_permissions=False,
            )
        )
        assert "--auto" not in cmd

    def test_interactive_model_override(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        cmd, _, _ = runner.build_interactive_command(
            AgentRunRequest(
                prompt="test",
                task="test",
                cwd=tmp_path,
                role="ceo",
                model="openai/gpt-4o",
            )
        )
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "openai/gpt-4o"


# ---------------------------------------------------------------------------
# OpenCodeRunner.headless
# ---------------------------------------------------------------------------


class TestOpenCodeHeadless:
    async def test_dry_run_returns_stub(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "1")
        (tmp_path / ".factory").mkdir()
        runner = OpenCodeRunner(project_path=tmp_path)
        result = await runner.headless(
            AgentRunRequest(
                prompt="Test prompt",
                task="Test task",
                cwd=tmp_path,
                role="researcher",
                project_path=tmp_path,
            )
        )
        assert result.return_code == 0
        assert "[DRY-RUN]" in result.stdout
        assert "researcher" in result.stdout

    async def test_background_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "1")
        runner = OpenCodeRunner()
        result = await runner.headless(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                role="builder",
                extras={"background": True},
            )
        )
        assert result.return_code == 1
        assert "--bg is not supported" in result.stdout

    async def test_tmux_persist_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = OpenCodeRunner()
        result = await runner.headless(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                role="ceo",
                extras={"tmux_persist": True},
            )
        )
        assert result.return_code == 1
        assert "--tmux-persist is not supported" in result.stdout

    async def test_headless_calls_run_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)
        (tmp_path / ".factory").mkdir()

        runner = OpenCodeRunner(project_path=tmp_path)
        with patch("factory.runners.opencode._check_auth"):
            with patch(
                "factory.runners.opencode.run_subprocess",
                new_callable=AsyncMock,
            ) as mock_run:
                mock_run.return_value = AgentRunResult(stdout="output", return_code=0)
                result = await runner.headless(
                    AgentRunRequest(
                        prompt="You are a test agent.",
                        task="Say hello",
                        cwd=tmp_path,
                        role="researcher",
                        timeout=60.0,
                        project_path=tmp_path,
                    )
                )

                assert result.return_code == 0
                assert result.stdout == "output"

                call_kwargs = mock_run.call_args.kwargs
                assert call_kwargs["runner_name"] == "opencode"
                assert call_kwargs["role"] == "researcher"
                assert call_kwargs["timeout"] == 60.0
                assert call_kwargs["sanitize"] is True
                cmd = mock_run.call_args[0][0]
                assert cmd[0] == "opencode"
                assert cmd[1] == "run"
                assert "--format" in cmd
                assert "-q" not in cmd

    async def test_headless_raises_without_auth(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in oc_module._PROVIDER_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()
        with patch("factory.runners.opencode._check_binary_compat"):
            with patch("factory.runners.opencode.Path.home", return_value=Path("/nonexistent")):
                with pytest.raises(OpenCodeAuthError):
                    await runner.headless(
                        AgentRunRequest(
                            prompt="Test",
                            task="Test",
                            cwd=tmp_path,
                            role="researcher",
                        )
                    )

    async def test_ceiling_exceeded_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)
        (tmp_path / ".factory").mkdir()

        from factory.runners.usage import CeilingExceededError

        runner = OpenCodeRunner(project_path=tmp_path)
        with patch("factory.runners.opencode._check_auth"):
            with patch(
                "factory.runners.opencode.check_ceilings",
                side_effect=CeilingExceededError(
                    "per-cycle",
                    8,
                    8,
                    "FACTORY_OPENCODE_MAX_INVOCATIONS_PER_CYCLE",
                    "opencode",
                ),
            ):
                with patch.object(runner, "_emit_ceiling_event"):
                    result = await runner.headless(
                        AgentRunRequest(
                            prompt="Test",
                            task="Test",
                            cwd=tmp_path,
                            role="researcher",
                            project_path=tmp_path,
                        )
                    )
                    assert result.return_code == 1
                    assert "ceiling exceeded" in result.stdout


# ---------------------------------------------------------------------------
# OpenCodeRunner.interactive_run
# ---------------------------------------------------------------------------


class TestOpenCodeInteractive:
    def test_dry_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "1")
        runner = OpenCodeRunner()
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

    def test_interactive_run_calls_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
        (tmp_path / ".factory").mkdir()

        runner = OpenCodeRunner(project_path=tmp_path)
        with patch("factory.runners.opencode._check_auth"):
            with patch("factory.runners.opencode.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                code = runner.interactive_run(
                    AgentRunRequest(
                        prompt="You are the CEO.",
                        task="Start session",
                        cwd=tmp_path,
                        role="ceo",
                        project_path=tmp_path,
                    )
                )
                assert code == 0
                cmd = mock_run.call_args[0][0]
                assert cmd[0] == "opencode"
                assert "run" not in cmd
                assert "--dir" not in cmd
                assert cmd[-1] == str(tmp_path)
                assert "-p" not in cmd
                assert "-c" not in cmd


# ---------------------------------------------------------------------------
# Token guardrails (usage integration)
# ---------------------------------------------------------------------------


class TestTokenGuardrails:
    async def test_usage_logging_on_headless(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "1")
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        runner = OpenCodeRunner(project_path=tmp_path)

        await runner.headless(
            AgentRunRequest(
                prompt="test",
                task="test",
                cwd=tmp_path,
                role="researcher",
                project_path=tmp_path,
            )
        )

        usage_log = factory_dir / "opencode_usage.jsonl"
        assert usage_log.exists()
        entry = json.loads(usage_log.read_text().strip())
        assert entry["role"] == "researcher"
        assert entry["dry_run"] is True


# ---------------------------------------------------------------------------
# is_opencode_dry_run
# ---------------------------------------------------------------------------


class TestIsOpencodeDryRun:
    def test_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "1")
        assert is_opencode_dry_run() is True

    def test_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)
        assert is_opencode_dry_run() is False

    def test_true_word(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "true")
        assert is_opencode_dry_run() is True

    def test_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "yes")
        assert is_opencode_dry_run() is True


# ---------------------------------------------------------------------------
# OpenCodeRunner.__init__ (cycle_start resolution)
# ---------------------------------------------------------------------------


class TestOpenCodeRunnerInit:
    def test_init_with_explicit_cycle_start(self) -> None:
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        runner = OpenCodeRunner(cycle_start=ts)
        assert runner.cycle_start == ts

    def test_init_with_project_path(self, tmp_path: Path) -> None:
        with patch("factory.runners.opencode.OpenCodeRunner.__init__.__wrapped__", create=True):
            runner = OpenCodeRunner(project_path=tmp_path)
            assert runner.cycle_start is not None

    def test_init_default(self) -> None:
        runner = OpenCodeRunner()
        assert runner.cycle_start is not None
