"""Tests for factory/runners/qwen.py — QwenRunner implementation."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import factory.runners.qwen as qwen_module
from factory.runners import get_runner
from factory.runners.qwen import QwenRunner, _warn_auth, is_qwen_dry_run


@pytest.fixture(autouse=True)
def _reset_qwen_auth() -> None:
    qwen_module._auth_warned = False


class TestGetRunnerQwen:
    def test_explicit_qwen(self) -> None:
        runner = get_runner("qwen")
        assert runner.name == "qwen"

    def test_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "qwen")
        runner = get_runner()
        assert runner.name == "qwen"

    def test_explicit_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "qwen")
        runner = get_runner("claude")
        assert runner.name == "claude"


class TestQwenDryRun:
    def test_dry_run_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_QWEN_DRY_RUN", "1")
        assert is_qwen_dry_run() is True

    def test_dry_run_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FACTORY_QWEN_DRY_RUN", raising=False)
        assert is_qwen_dry_run() is False

    def test_dry_run_true_word(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_QWEN_DRY_RUN", "true")
        assert is_qwen_dry_run() is True

    async def test_headless_dry_run_returns_stub(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FACTORY_QWEN_DRY_RUN", "1")

        runner = QwenRunner()
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
        monkeypatch.setenv("FACTORY_QWEN_DRY_RUN", "1")

        runner = QwenRunner()
        code = runner.interactive_run(
            prompt="Test prompt",
            task="Test task",
            cwd=tmp_path,
            role="ceo",
        )

        assert code == 0
        captured = capsys.readouterr()
        assert "[DRY-RUN]" in captured.out


class TestQwenAuth:
    def test_warns_without_key(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        monkeypatch.delenv("QWEN_API_KEY", raising=False)

        with caplog.at_level("WARNING"):
            _warn_auth()

        assert "DASHSCOPE_API_KEY" in caplog.text

    def test_no_warning_with_dashscope_key(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.delenv("QWEN_API_KEY", raising=False)

        with caplog.at_level("WARNING"):
            _warn_auth()

        assert "DASHSCOPE_API_KEY" not in caplog.text
        assert qwen_module._auth_warned is True

    def test_no_warning_with_qwen_key(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        monkeypatch.setenv("QWEN_API_KEY", "test-key")

        with caplog.at_level("WARNING"):
            _warn_auth()

        assert "DASHSCOPE_API_KEY" not in caplog.text
        assert qwen_module._auth_warned is True

    def test_warns_only_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        monkeypatch.delenv("QWEN_API_KEY", raising=False)

        with caplog.at_level("WARNING"):
            _warn_auth()
            caplog.clear()
            _warn_auth()

        assert "DASHSCOPE_API_KEY" not in caplog.text


class TestQwenEnv:
    def test_virtual_env_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")

        from factory.runners.qwen import _make_qwen_env

        env = _make_qwen_env()
        assert "VIRTUAL_ENV" not in env

    def test_preserves_other_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

        from factory.runners.qwen import _make_qwen_env

        env = _make_qwen_env()
        assert env["DASHSCOPE_API_KEY"] == "test-key"


class TestQwenHeadless:
    async def test_builds_correct_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_QWEN_DRY_RUN", raising=False)

        runner = QwenRunner()

        with patch(
            "factory.runners.qwen.stream_subprocess", new_callable=AsyncMock
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
                    model="qwen3-coder",
                )

                assert code == 0
                assert stdout == "output"

                call_args = mock_exec.call_args[0]
                assert call_args[0] == "qwen"
                assert "--append-system-prompt" in call_args
                assert "-p" in call_args
                assert "--yolo" in call_args
                assert "--output-format" in call_args
                assert "text" in call_args
                assert "--model" in call_args
                assert "qwen3-coder" in call_args

    async def test_no_model_flag_when_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_QWEN_DRY_RUN", raising=False)

        runner = QwenRunner()

        with patch(
            "factory.runners.qwen.stream_subprocess", new_callable=AsyncMock
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

    async def test_prompt_and_task_are_separate_args(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_QWEN_DRY_RUN", raising=False)

        runner = QwenRunner()

        with patch(
            "factory.runners.qwen.stream_subprocess", new_callable=AsyncMock
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
                assert "You are the CEO." in call_args
                assert "Run the experiment" in call_args

    async def test_handles_missing_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_QWEN_DRY_RUN", raising=False)

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError,
        ):
            runner = QwenRunner()
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
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.delenv("FACTORY_QWEN_DRY_RUN", raising=False)

        runner = QwenRunner()

        with patch(
            "factory.runners.qwen.stream_subprocess", new_callable=AsyncMock
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


class TestQwenInteractive:
    def test_interactive_run_builds_correct_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_QWEN_DRY_RUN", raising=False)

        runner = QwenRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            code = runner.interactive_run(
                prompt="You are the CEO.",
                task="Start session",
                cwd=tmp_path,
                model="qwen3-coder",
                dangerously_skip_permissions=True,
            )

            assert code == 0
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "qwen"
            assert "--append-system-prompt" in cmd
            assert "--yolo" in cmd
            assert "--model" in cmd
            assert "qwen3-coder" in cmd

    def test_interactive_run_no_yolo_without_skip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_QWEN_DRY_RUN", raising=False)

        runner = QwenRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            runner.interactive_run(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                dangerously_skip_permissions=False,
            )

            cmd = mock_run.call_args[0][0]
            assert "--yolo" not in cmd

    def test_interactive_run_passes_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.delenv("FACTORY_QWEN_DRY_RUN", raising=False)

        runner = QwenRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            runner.interactive_run(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )

            call_kwargs = mock_run.call_args.kwargs
            assert "VIRTUAL_ENV" not in call_kwargs["env"]
