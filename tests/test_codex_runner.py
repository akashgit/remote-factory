"""Tests for factory/runners/codex.py — CodexRunner implementation."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.runners import CodexRunner, get_runner, is_codex_dry_run
from factory.runners.codex import CodexAuthError, _check_auth


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
        stdout, code = await runner.headless(
            prompt="You are a test agent.",
            task="Say hello",
            cwd=tmp_path,
            role="researcher",
        )

        assert code == 0
        assert "[DRY-RUN]" in stdout
        assert "researcher" in stdout

    def test_interactive_run_dry_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("FACTORY_CODEX_DRY_RUN", "1")

        runner = CodexRunner()
        code = runner.interactive_run(
            prompt="Test prompt",
            task="Test task",
            cwd=tmp_path,
            role="ceo",
        )

        assert code == 0
        captured = capsys.readouterr()
        assert "[DRY-RUN]" in captured.out


class TestCodexAuth:
    def test_auth_fails_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        with pytest.raises(CodexAuthError, match="CODEX_API_KEY"):
            _check_auth()

        codex_module._auth_checked = False

    def test_auth_passes_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        _check_auth()
        assert codex_module._auth_checked is True

        codex_module._auth_checked = False

    async def test_headless_fails_without_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        runner = CodexRunner()
        with pytest.raises(CodexAuthError):
            await runner.headless(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                role="researcher",
            )

        codex_module._auth_checked = False


class TestCodexHeadless:
    async def test_builds_correct_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        runner = CodexRunner()

        with patch(
            "factory.runners.codex.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b"output", b"")

            with patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                stdout, code = await runner.headless(
                    prompt="You are a test agent.",
                    task="Say hello",
                    cwd=tmp_path,
                    timeout=60.0,
                    model="gpt-5.4",
                )

                assert code == 0
                assert stdout == "output"

                call_args = mock_exec.call_args[0]
                assert call_args[0] == "codex"
                assert call_args[1] == "exec"
                assert "--sandbox" in call_args
                assert "workspace-write" in call_args
                assert "--ask-for-approval" in call_args
                assert "never" in call_args
                assert "--model" in call_args
                assert "gpt-5.4" in call_args

        codex_module._auth_checked = False

    async def test_combines_prompt_and_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        runner = CodexRunner()

        with patch(
            "factory.runners.codex.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b"ok", b"")

            with patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                await runner.headless(
                    prompt="You are the CEO.",
                    task="Run the experiment",
                    cwd=tmp_path,
                )

                call_args = mock_exec.call_args[0]
                # Third arg (index 2) is the combined prompt+task
                full_prompt = call_args[2]
                assert "You are the CEO." in full_prompt
                assert "Run the experiment" in full_prompt
                assert "## Current Task" in full_prompt

        codex_module._auth_checked = False

    async def test_no_sandbox_flags_when_permissions_not_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        runner = CodexRunner()

        with patch(
            "factory.runners.codex.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b"ok", b"")

            with patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                await runner.headless(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    dangerously_skip_permissions=False,
                )

                call_args = mock_exec.call_args[0]
                assert "--sandbox" not in call_args
                assert "--ask-for-approval" not in call_args

        codex_module._auth_checked = False

    async def test_no_model_flag_when_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        runner = CodexRunner()

        with patch(
            "factory.runners.codex.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b"ok", b"")

            with patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                await runner.headless(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    model=None,
                )

                call_args = mock_exec.call_args[0]
                assert "--model" not in call_args

        codex_module._auth_checked = False

    async def test_handles_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio as aio

        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        with patch("factory.runners.codex.asyncio.wait_for", side_effect=aio.TimeoutError):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.kill = AsyncMock()
                mock_proc.wait = AsyncMock()
                mock_exec.return_value = mock_proc

                runner = CodexRunner()
                stdout, code = await runner.headless(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    role="researcher",
                    timeout=0.1,
                )

        assert code == 1
        assert "timed out" in stdout.lower()
        codex_module._auth_checked = False

    async def test_handles_missing_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError,
        ):
            runner = CodexRunner()
            stdout, code = await runner.headless(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )

        assert code == 1
        assert "not found" in stdout.lower()
        codex_module._auth_checked = False

    async def test_strips_virtual_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        runner = CodexRunner()

        with patch(
            "factory.runners.codex.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b"ok", b"")

            with patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                await runner.headless(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                )

                call_kwargs = mock_exec.call_args.kwargs
                assert "VIRTUAL_ENV" not in call_kwargs["env"]

        codex_module._auth_checked = False


class TestCodexStreaming:
    async def test_uses_streaming_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        runner = CodexRunner()

        with patch("factory.runners.codex.should_stream", return_value=True):
            with patch(
                "factory.runners.codex.stream_subprocess", new_callable=AsyncMock
            ) as mock_stream:
                mock_stream.return_value = (b"output\n", b"")

                with patch(
                    "asyncio.create_subprocess_exec", new_callable=AsyncMock
                ) as mock_exec:
                    mock_proc = AsyncMock()
                    mock_proc.returncode = 0
                    mock_exec.return_value = mock_proc

                    await runner.headless(
                        prompt="Test",
                        task="Test",
                        cwd=tmp_path,
                        role="builder",
                    )

                    mock_stream.assert_called_once()
                    call_kwargs = mock_stream.call_args.kwargs
                    assert call_kwargs["stream"] is True
                    assert call_kwargs["prefix"] == "[codex:builder]"

        codex_module._auth_checked = False


class TestCodexInteractive:
    def test_interactive_run_builds_correct_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        runner = CodexRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            code = runner.interactive_run(
                prompt="You are the CEO.",
                task="Start session",
                cwd=tmp_path,
                model="gpt-5.4",
                dangerously_skip_permissions=True,
            )

            assert code == 0
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "codex"
            assert "--sandbox" in cmd
            assert "workspace-write" in cmd
            assert "--model" in cmd
            assert "gpt-5.4" in cmd

        codex_module._auth_checked = False

    def test_interactive_run_no_sandbox_without_skip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        import factory.runners.codex as codex_module
        codex_module._auth_checked = False

        runner = CodexRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            runner.interactive_run(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                dangerously_skip_permissions=False,
            )

            cmd = mock_run.call_args[0][0]
            assert "--sandbox" not in cmd

        codex_module._auth_checked = False
