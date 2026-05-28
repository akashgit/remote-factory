"""Tests for factory/runners/opencode.py — OpenCodeRunner implementation."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.runners import get_runner
from factory.runners.opencode import OpenCodeRunner, is_opencode_dry_run


class TestGetRunnerOpenCode:
    def test_explicit_opencode(self) -> None:
        runner = get_runner("opencode")
        assert runner.name == "opencode"

    def test_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "opencode")
        runner = get_runner()
        assert runner.name == "opencode"

    def test_explicit_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "opencode")
        runner = get_runner("claude")
        assert runner.name == "claude"


class TestOpenCodeDryRun:
    def test_dry_run_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "1")
        assert is_opencode_dry_run() is True

    def test_dry_run_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)
        assert is_opencode_dry_run() is False

    def test_dry_run_true_word(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "true")
        assert is_opencode_dry_run() is True

    async def test_headless_dry_run_returns_stub(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "1")

        runner = OpenCodeRunner()
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
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "1")

        runner = OpenCodeRunner()
        code = runner.interactive_run(
            prompt="Test prompt",
            task="Test task",
            cwd=tmp_path,
            role="ceo",
        )

        assert code == 0
        captured = capsys.readouterr()
        assert "[DRY-RUN]" in captured.out


class TestOpenCodeEnv:
    def test_virtual_env_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")

        from factory.runners.opencode import _make_opencode_env

        env = _make_opencode_env()
        assert "VIRTUAL_ENV" not in env

    def test_preserves_other_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOME_VAR", "test-value")

        from factory.runners.opencode import _make_opencode_env

        env = _make_opencode_env()
        assert env["SOME_VAR"] == "test-value"


class TestOpenCodeHeadless:
    async def test_builds_correct_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()

        with patch(
            "factory.runners.opencode.stream_subprocess", new_callable=AsyncMock
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
                    model="anthropic/claude-sonnet-4-20250514",
                )

                assert code == 0
                assert stdout == "output"

                call_args = mock_exec.call_args[0]
                assert call_args[0] == "opencode"
                assert call_args[1] == "run"
                assert "--format" in call_args
                assert "default" in call_args
                assert "--dangerously-skip-permissions" in call_args
                assert "--dir" in call_args
                assert "--model" in call_args
                assert "anthropic/claude-sonnet-4-20250514" in call_args

    async def test_no_model_flag_when_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()

        with patch(
            "factory.runners.opencode.stream_subprocess", new_callable=AsyncMock
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

    async def test_prompt_prepended_to_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()

        with patch(
            "factory.runners.opencode.stream_subprocess", new_callable=AsyncMock
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
                combined = call_args[2]
                assert "You are the CEO." in combined
                assert "Run the experiment" in combined
                assert "---" in combined

    async def test_no_skip_permissions_when_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()

        with patch(
            "factory.runners.opencode.stream_subprocess", new_callable=AsyncMock
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
                assert "--dangerously-skip-permissions" not in call_args

    async def test_handles_missing_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError,
        ):
            runner = OpenCodeRunner()
            stdout, code = await runner.headless(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )

        assert code == 1
        assert "not found" in stdout.lower()

    async def test_passes_env_without_virtual_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()

        with patch(
            "factory.runners.opencode.stream_subprocess", new_callable=AsyncMock
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


class TestOpenCodeInteractive:
    def test_interactive_run_builds_correct_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            code = runner.interactive_run(
                prompt="You are the CEO.",
                task="Start session",
                cwd=tmp_path,
                model="anthropic/claude-sonnet-4-20250514",
                dangerously_skip_permissions=True,
            )

            assert code == 0
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "opencode"
            assert cmd[1] == "run"
            assert "--interactive" in cmd
            assert "--dangerously-skip-permissions" in cmd
            assert "--model" in cmd
            assert "anthropic/claude-sonnet-4-20250514" in cmd

    def test_interactive_run_no_skip_permissions_without_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            runner.interactive_run(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                dangerously_skip_permissions=False,
            )

            cmd = mock_run.call_args[0][0]
            assert "--dangerously-skip-permissions" not in cmd

    def test_interactive_run_passes_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            runner.interactive_run(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )

            call_kwargs = mock_run.call_args.kwargs
            assert "VIRTUAL_ENV" not in call_kwargs["env"]

    def test_interactive_prompt_prepended_to_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            runner.interactive_run(
                prompt="You are the CEO.",
                task="Start session",
                cwd=tmp_path,
            )

            cmd = mock_run.call_args[0][0]
            combined_args = [arg for arg in cmd if "You are the CEO." in arg]
            assert len(combined_args) == 1
            assert "Start session" in combined_args[0]
