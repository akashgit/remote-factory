"""Tests for factory/runners/codex.py — CodexRunner implementation."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import factory.runners.codex as codex_module
from factory.models import AgentRunRequest, AgentRunResult
from factory.runners import CodexRunner, get_runner, is_codex_dry_run
from factory.runners.codex import CodexAuthError, _check_auth


@pytest.fixture(autouse=True)
def _reset_codex_auth() -> None:
    codex_module._auth_checked = False


class TestGetRunnerCodex:
    def test_explicit_codex(self) -> None:
        runner = get_runner("codex")
        assert runner.name == "codex"

    def test_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "codex")
        runner = get_runner()
        assert runner.name == "codex"

    def test_explicit_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "codex")
        runner = get_runner("claude")
        assert runner.name == "claude"


class TestCodexDryRun:
    def test_dry_run_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_CODEX_DRY_RUN", "1")
        assert is_codex_dry_run() is True

    def test_dry_run_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        assert is_codex_dry_run() is False

    def test_dry_run_true_word(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_CODEX_DRY_RUN", "true")
        assert is_codex_dry_run() is True

    async def test_headless_dry_run_returns_stub(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FACTORY_CODEX_DRY_RUN", "1")

        runner = CodexRunner()
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
        monkeypatch.setenv("FACTORY_CODEX_DRY_RUN", "1")

        runner = CodexRunner()
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


class TestCodexAuth:
    def test_auth_fails_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with patch("factory.runners.codex._has_codex_oauth", return_value=False):
            with pytest.raises(CodexAuthError, match="CODEX_API_KEY"):
                _check_auth()

    def test_auth_passes_with_codex_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        _check_auth()
        assert codex_module._auth_checked is True

    def test_auth_passes_with_openai_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

        with patch("factory.runners.codex._has_codex_oauth", return_value=False):
            _check_auth()
            assert codex_module._auth_checked is True

    def test_auth_prefers_oauth_over_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        with patch("factory.runners.codex._has_codex_oauth", return_value=True):
            _check_auth()
            assert codex_module._auth_checked is True

    async def test_headless_fails_without_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()
        with patch("factory.runners.codex._has_codex_oauth", return_value=False):
            with pytest.raises(CodexAuthError):
                await runner.headless(
                    AgentRunRequest(
                        prompt="Test",
                        task="Test",
                        cwd=tmp_path,
                        role="researcher",
                    )
                )


class TestCodexEnvMapping:
    def test_codex_key_mapped_to_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "my-codex-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        from factory.runners.codex import _make_codex_env

        with patch("factory.runners.codex._has_codex_oauth", return_value=False):
            env, tmpdir = _make_codex_env()
            tmpdir.cleanup()
            assert env["OPENAI_API_KEY"] == "my-codex-key"
            assert "VIRTUAL_ENV" not in env

    def test_openai_key_not_overridden_without_oauth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "codex-key")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

        from factory.runners.codex import _make_codex_env

        with patch("factory.runners.codex._has_codex_oauth", return_value=False):
            env, tmpdir = _make_codex_env()
            tmpdir.cleanup()
            assert env["OPENAI_API_KEY"] == "openai-key"

    def test_oauth_strips_api_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        monkeypatch.setenv("CODEX_API_KEY", "codex-key")

        from factory.runners.codex import _make_codex_env

        with patch("factory.runners.codex._has_codex_oauth", return_value=True):
            env, tmpdir = _make_codex_env()
            assert tmpdir is None
            assert "OPENAI_API_KEY" not in env
            assert "CODEX_API_KEY" not in env

    def test_virtual_env_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")

        from factory.runners.codex import _make_codex_env

        with patch("factory.runners.codex._has_codex_oauth", return_value=False):
            env, tmpdir = _make_codex_env()
            if tmpdir is not None:
                tmpdir.cleanup()
            assert "VIRTUAL_ENV" not in env


class TestCodexHeadless:
    async def test_builds_correct_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()

        with patch(
            "factory.runners.codex.run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = AgentRunResult(stdout="output", return_code=0)

            result = await runner.headless(
                AgentRunRequest(
                    prompt="You are a test agent.",
                    task="Say hello",
                    cwd=tmp_path,
                    timeout=60.0,
                    model="gpt-5.4",
                )
            )

            assert result.return_code == 0
            assert result.stdout == "output"
            assert result.usage is None

            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert cmd[0] == "codex"
            assert cmd[1] == "exec"
            assert "--ignore-user-config" in cmd
            assert "--sandbox" in cmd
            assert "workspace-write" in cmd
            assert "--ask-for-approval" not in cmd
            assert "--model" in cmd
            assert "gpt-5.4" in cmd
            assert "--skip-git-repo-check" in cmd
            assert "--" in cmd

    async def test_combines_prompt_and_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()

        with patch(
            "factory.runners.codex.run_subprocess", new_callable=AsyncMock
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
            dash_idx = cmd.index("--")
            full_prompt = cmd[dash_idx + 1]
            assert "You are the CEO." in full_prompt
            assert "Run the experiment" in full_prompt
            assert "## Current Task" in full_prompt

    async def test_no_sandbox_flags_when_permissions_not_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()

        with patch(
            "factory.runners.codex.run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = AgentRunResult(stdout="ok", return_code=0)

            await runner.headless(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    skip_permissions=False,
                )
            )

            cmd = mock_run.call_args[0][0]
            assert "--sandbox" not in cmd
            assert "--ask-for-approval" not in cmd

    async def test_no_model_flag_when_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()

        with patch(
            "factory.runners.codex.run_subprocess", new_callable=AsyncMock
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

    async def test_handles_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        with patch(
            "factory.runners.codex.run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = AgentRunResult(
                stdout="Agent timed out after 0.1s", return_code=1
            )

            runner = CodexRunner()
            result = await runner.headless(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    role="researcher",
                    timeout=0.1,
                )
            )

        assert result.return_code == 1
        assert "timed out" in result.stdout.lower()
        assert result.usage is None

    async def test_handles_missing_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        with patch(
            "factory.runners.codex.run_subprocess",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentRunResult(
                stdout="Error: 'codex' CLI not found on PATH", return_code=1
            )

            runner = CodexRunner()
            result = await runner.headless(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                )
            )

        assert result.return_code == 1
        assert "not found" in result.stdout.lower()
        assert result.usage is None

    async def test_passes_env_with_openai_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()

        with patch("factory.runners.codex._has_codex_oauth", return_value=False):
            with patch(
                "factory.runners.codex.run_subprocess", new_callable=AsyncMock
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
                assert call_kwargs["env"]["OPENAI_API_KEY"] == "test-key"


class TestCodexStreaming:
    async def test_uses_streaming_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)

        runner = CodexRunner()

        with patch(
            "factory.runners.codex.run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = AgentRunResult(stdout="output\n", return_code=0)

            await runner.headless(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    role="builder",
                )
            )

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["runner_name"] == "codex"
            assert call_kwargs["role"] == "builder"

    async def test_codex_runner_does_not_sanitize(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CodexRunner.headless() does not sanitize (default False) — issue #379."""
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)

        runner = CodexRunner()

        with patch(
            "factory.runners.codex.run_subprocess", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = AgentRunResult(stdout="output\n", return_code=0)

            await runner.headless(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    role="builder",
                )
            )

            mock_run.assert_called_once()
            # run_subprocess defaults sanitize=False; CodexRunner does not pass it
            assert mock_run.call_args.kwargs.get("sanitize", False) is False


class TestCodexBuildInteractiveCommand:
    """Tests for CodexRunner.build_interactive_command()."""

    def test_base_command_structure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        runner = CodexRunner()

        with patch("factory.runners.codex._has_codex_oauth", return_value=False):
            cmd, env, temp_files = runner.build_interactive_command(AgentRunRequest(
                prompt="You are the CEO.",
                task="Start session",
                cwd=tmp_path,
                model="gpt-5.4",
                skip_permissions=True,
            ))

        assert cmd[0] == "codex"
        full_prompt = cmd[1]
        assert "You are the CEO." in full_prompt
        assert "Start session" in full_prompt
        assert "## Current Task" in full_prompt
        assert "exec" not in cmd
        assert "--" not in cmd
        assert "--skip-git-repo-check" not in cmd
        assert "--ignore-user-config" in cmd
        assert "--full-auto" in cmd
        assert "--model" in cmd
        assert "gpt-5.4" in cmd
        assert temp_files == []
        assert "VIRTUAL_ENV" not in env

        if hasattr(runner, "_tmpdir") and runner._tmpdir is not None:
            runner._tmpdir.cleanup()

    def test_no_permission_flags_without_skip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        runner = CodexRunner()

        with patch("factory.runners.codex._has_codex_oauth", return_value=False):
            cmd, _, _ = runner.build_interactive_command(AgentRunRequest(
                prompt="Test", task="Test", cwd=tmp_path, skip_permissions=False,
            ))

        assert "--full-auto" not in cmd
        assert "--sandbox" not in cmd

        if hasattr(runner, "_tmpdir") and runner._tmpdir is not None:
            runner._tmpdir.cleanup()

    def test_no_model_flag_when_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        runner = CodexRunner()

        with patch("factory.runners.codex._has_codex_oauth", return_value=False):
            cmd, _, _ = runner.build_interactive_command(AgentRunRequest(
                prompt="Test", task="Test", cwd=tmp_path, model=None,
            ))

        assert "--model" not in cmd

        if hasattr(runner, "_tmpdir") and runner._tmpdir is not None:
            runner._tmpdir.cleanup()

    def test_env_from_make_codex_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        runner = CodexRunner()

        with patch("factory.runners.codex._has_codex_oauth", return_value=False):
            _, env, _ = runner.build_interactive_command(AgentRunRequest(
                prompt="Test", task="Test", cwd=tmp_path,
            ))

        assert "VIRTUAL_ENV" not in env
        assert env["OPENAI_API_KEY"] == "test-key"

        if hasattr(runner, "_tmpdir") and runner._tmpdir is not None:
            runner._tmpdir.cleanup()


class TestCodexInteractive:
    def test_interactive_run_builds_correct_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            code = runner.interactive_run(
                AgentRunRequest(
                    prompt="You are the CEO.",
                    task="Start session",
                    cwd=tmp_path,
                    model="gpt-5.4",
                    skip_permissions=True,
                )
            )

            assert code == 0
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "codex"
            assert "--ignore-user-config" in cmd
            assert "--full-auto" in cmd
            assert "--model" in cmd
            assert "gpt-5.4" in cmd

    def test_interactive_run_no_sandbox_without_skip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            runner.interactive_run(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    skip_permissions=False,
                )
            )

            cmd = mock_run.call_args[0][0]
            assert "--full-auto" not in cmd

    def test_interactive_run_passes_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()

        with patch("factory.runners.codex._has_codex_oauth", return_value=False):
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
                assert call_kwargs["env"]["OPENAI_API_KEY"] == "test-key"
