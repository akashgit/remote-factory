"""Tests for factory/runners/codex.py — CodexRunner implementation."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import factory.runners.codex as codex_module
from factory.runners import CodexRunner, get_runner, is_codex_dry_run
from factory.runners.abstraction import Request
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
        stdout, code, usage = await runner.headless(
            prompt="You are a test agent.",
            task="Say hello",
            cwd=tmp_path,
            role="researcher",
        )

        assert code == 0
        assert "[DRY-RUN]" in stdout
        assert "researcher" in stdout
        assert usage is None

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
    def test_auth_fails_without_key(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        # Mock Path.home() to avoid finding real ~/.codex/auth.json
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

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

        _check_auth()
        assert codex_module._auth_checked is True

    async def test_headless_fails_without_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        # Mock Path.home() to avoid finding real ~/.codex/auth.json
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        runner = CodexRunner()
        with pytest.raises(CodexAuthError):
            await runner.headless(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                role="researcher",
            )


class TestCodexEnvMapping:
    def test_codex_key_mapped_to_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "my-codex-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        from factory.runners.codex import _make_codex_env

        env = _make_codex_env()
        assert env["OPENAI_API_KEY"] == "my-codex-key"
        assert "VIRTUAL_ENV" not in env

    def test_openai_key_not_overridden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "codex-key")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

        from factory.runners.codex import _make_codex_env

        env = _make_codex_env()
        assert env["OPENAI_API_KEY"] == "openai-key"

    def test_virtual_env_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")

        from factory.runners.codex import _make_codex_env

        env = _make_codex_env()
        assert "VIRTUAL_ENV" not in env


class TestCodexHeadless:
    """Tests for CodexRunner.headless() shim — now delegates to run() → super().run()."""

    async def test_builds_correct_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify _build_command produces correct flags (unit test, no subprocess)."""
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()
        req = Request(
            prompt="You are a test agent.",
            task="Say hello",
            cwd=tmp_path,
            timeout=60.0,
            model="gpt-5.4",
            skip_permissions=True,
        )
        cmd = runner._build_command(req)
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"
        assert "--sandbox" in cmd
        assert "workspace-write" in cmd
        # codex exec does NOT support --ask-for-approval
        assert "--ask-for-approval" not in cmd
        assert "--model" in cmd
        assert "gpt-5.4" in cmd
        assert "--json" in cmd

    async def test_combines_prompt_and_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()
        req = Request(prompt="You are the CEO.", task="Run the experiment", cwd=tmp_path)
        cmd = runner._build_command(req)
        full_prompt = cmd[2]
        assert "You are the CEO." in full_prompt
        assert "Run the experiment" in full_prompt
        assert "## Current Task" in full_prompt

    async def test_no_sandbox_flags_when_permissions_not_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()
        req = Request(prompt="Test", task="Test", cwd=tmp_path, skip_permissions=False)
        cmd = runner._build_command(req)
        assert "--sandbox" not in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd

    async def test_no_model_flag_when_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()
        req = Request(prompt="Test", task="Test", cwd=tmp_path, model=None)
        cmd = runner._build_command(req)
        assert "--model" not in cmd

    async def test_handles_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio as aio

        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        with patch("asyncio.wait_for", side_effect=aio.TimeoutError):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.kill = AsyncMock()
                mock_proc.wait = AsyncMock()
                mock_exec.return_value = mock_proc

                runner = CodexRunner()
                stdout, code, usage = await runner.headless(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    role="researcher",
                    timeout=0.1,
                )

        assert code == 1
        assert "timed out" in stdout.lower()

    async def test_handles_missing_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError,
        ):
            runner = CodexRunner()
            stdout, code, usage = await runner.headless(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )

        assert code == 1
        assert "not found" in stdout.lower()

    async def test_passes_env_with_openai_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()
        env = runner._build_env()
        assert "VIRTUAL_ENV" not in env
        assert env["OPENAI_API_KEY"] == "test-key"


class TestCodexStreaming:
    """Test streaming config via _build_command and run() lifecycle."""

    async def test_uses_streaming_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)

        runner = CodexRunner()

        with patch("factory.runners._stream.should_stream", return_value=True):
            with patch(
                "factory.runners._stream.stream_subprocess", new_callable=AsyncMock
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
                prompt="You are the CEO.",
                task="Start session",
                cwd=tmp_path,
                model="gpt-5.4",
                dangerously_skip_permissions=True,
            )

            assert code == 0
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "codex"
            # Interactive uses --dangerously-bypass-approvals-and-sandbox
            assert "--dangerously-bypass-approvals-and-sandbox" in cmd
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
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                dangerously_skip_permissions=False,
            )

            cmd = mock_run.call_args[0][0]
            assert "--dangerously-bypass-approvals-and-sandbox" not in cmd

    def test_interactive_run_passes_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)

        runner = CodexRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            runner.interactive_run(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )

            call_kwargs = mock_run.call_args.kwargs
            assert "VIRTUAL_ENV" not in call_kwargs["env"]
            assert call_kwargs["env"]["OPENAI_API_KEY"] == "test-key"
