"""Tests for factory/runners/opencode.py — OpenCodeRunner implementation."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import factory.runners.opencode as opencode_module
from factory.models import AgentRunRequest, AgentRunResult
from factory.runners import OpenCodeRunner, get_runner, is_opencode_dry_run
from factory.runners.opencode import (
    OpenCodeAuthError,
    _check_auth,
)


@pytest.fixture(autouse=True)
def _reset_opencode_auth() -> None:
    opencode_module._auth_checked = False
    opencode_module._compat_checked = False


class TestGetRunnerOpenCode:
    def test_explicit_opencode(self) -> None:
        runner = get_runner("opencode")
        assert runner.name == "opencode"

    def test_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "opencode")
        runner = get_runner()
        assert runner.name == "opencode"


class TestOpenCodeDryRun:
    def test_dry_run_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "1")
        assert is_opencode_dry_run() is True

    def test_dry_run_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)
        assert is_opencode_dry_run() is False

    async def test_headless_dry_run_returns_stub(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "1")

        runner = OpenCodeRunner()
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


class TestOpenCodeAuth:
    def test_auth_fails_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with patch("factory.runners.opencode._can_source_key_from_shell", return_value=False):
            with patch("factory.runners.opencode._check_binary_compat"):
                with pytest.raises(OpenCodeAuthError, match="OPENAI_API_KEY"):
                    _check_auth()

    def test_auth_passes_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        with patch("factory.runners.opencode._check_binary_compat"):
            _check_auth()
            assert opencode_module._auth_checked is True


class TestOpenCodeBuildCommand:
    def test_no_temp_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        runner = OpenCodeRunner()

        cmd, env, temp_files = runner.build_command(AgentRunRequest(
            prompt="You are the CEO.",
            task="Run the experiment",
            cwd=tmp_path,
        ))

        assert cmd[0] == "opencode"
        assert "-p" in cmd
        idx = cmd.index("-p")
        assert cmd[idx + 1] == "Run the experiment"
        assert "-q" in cmd
        assert temp_files == []


class TestOpenCodeBuildInteractiveCommand:
    def test_no_temp_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        runner = OpenCodeRunner()

        cmd, env, temp_files = runner.build_interactive_command(AgentRunRequest(
            prompt="You are the CEO.",
            task="Start session",
            cwd=tmp_path,
        ))

        assert cmd[0] == "opencode"
        assert "-p" not in cmd
        assert temp_files == []


class TestOpenCodeHeadless:
    async def test_passes_task_via_p_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()

        with patch("factory.runners.opencode._check_binary_compat"):
            with patch(
                "factory.runners.opencode.run_subprocess", new_callable=AsyncMock
            ) as mock_run:
                mock_run.return_value = AgentRunResult(stdout="ok", return_code=0)

                await runner.headless(
                    AgentRunRequest(
                        prompt="You are the CEO.",
                        task="Run the experiment",
                        cwd=tmp_path,
                    )
                )

                cmd = mock_run.call_args[0][0]
                assert "-p" in cmd
                idx = cmd.index("-p")
                assert cmd[idx + 1] == "Run the experiment"

        assert not (tmp_path / "OpenCode.md").exists()

    async def test_no_cleanup_needed_on_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()

        with patch("factory.runners.opencode._check_binary_compat"):
            with patch(
                "factory.runners.opencode.run_subprocess", new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ):
                with pytest.raises(RuntimeError, match="boom"):
                    await runner.headless(
                        AgentRunRequest(
                            prompt="Test",
                            task="Test",
                            cwd=tmp_path,
                        )
                    )

        assert not (tmp_path / "OpenCode.md").exists()


class TestOpenCodeInteractive:
    def test_interactive_run_no_opencode_md(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()

        with patch("factory.runners.opencode._check_binary_compat"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = type("Result", (), {"returncode": 0})()
                code = runner.interactive_run(
                    AgentRunRequest(
                        prompt="You are the CEO.",
                        task="Start session",
                        cwd=tmp_path,
                    )
                )

                assert code == 0
                assert not (tmp_path / "OpenCode.md").exists()
