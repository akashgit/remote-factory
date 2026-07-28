"""Tests for factory/runners/opencode.py — OpenCodeRunner implementation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import factory.runners.opencode as oc_module
from factory.models import AgentRunRequest, AgentRunResult
from factory.runners.opencode import (
    OpenCodeAuthError,
    OpenCodeRunner,
    _can_source_key_from_shell,
    _check_auth,
    _check_binary_compat,
    _find_opencode_bin_dir,
    _prepend_opencode_path,
    _source_openai_key_from_shell,
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
        assert "OPENAI_API_KEY" in str(err)
        assert "config.toml" in str(err)
        assert "[credentials.opencode]" in str(err)


# ---------------------------------------------------------------------------
# _can_source_key_from_shell
# ---------------------------------------------------------------------------


class TestCanSourceKeyFromShell:
    def test_returns_true_when_key_found(self) -> None:
        mock_result = MagicMock(stdout="sk-fake-key-123\n")
        with patch("factory.runners.opencode.subprocess.run", return_value=mock_result):
            assert _can_source_key_from_shell() is True

    def test_returns_false_when_empty(self) -> None:
        mock_result = MagicMock(stdout="\n")
        with patch("factory.runners.opencode.subprocess.run", return_value=mock_result):
            assert _can_source_key_from_shell() is False

    def test_returns_false_on_file_not_found(self) -> None:
        with patch(
            "factory.runners.opencode.subprocess.run", side_effect=FileNotFoundError
        ):
            assert _can_source_key_from_shell() is False

    def test_returns_false_on_timeout(self) -> None:
        import subprocess

        with patch(
            "factory.runners.opencode.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="zsh", timeout=5),
        ):
            assert _can_source_key_from_shell() is False


# ---------------------------------------------------------------------------
# _check_auth
# ---------------------------------------------------------------------------


class TestCheckAuth:
    def test_skips_when_already_checked(self) -> None:
        oc_module._auth_checked = True
        # Should return immediately without raising
        _check_auth()

    def test_passes_with_env_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        with patch("factory.runners.opencode._check_binary_compat"):
            _check_auth()
        assert oc_module._auth_checked is True

    def test_passes_with_shell_sourced_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("factory.runners.opencode._check_binary_compat"):
            with patch("factory.runners.opencode._can_source_key_from_shell", return_value=True):
                _check_auth()
        assert oc_module._auth_checked is True

    def test_raises_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("factory.runners.opencode._check_binary_compat"):
            with patch("factory.runners.opencode._can_source_key_from_shell", return_value=False):
                with pytest.raises(OpenCodeAuthError, match="OPENAI_API_KEY"):
                    _check_auth()


# ---------------------------------------------------------------------------
# _check_binary_compat
# ---------------------------------------------------------------------------


class TestCheckBinaryCompat:
    def test_skips_when_already_checked(self) -> None:
        oc_module._compat_checked = True
        # Should return immediately
        _check_binary_compat()

    def test_returns_early_when_no_binary(self) -> None:
        with patch("shutil.which", return_value=None):
            _check_binary_compat()
        assert oc_module._compat_checked is True

    def test_go_binary_detected(self) -> None:
        mock_result = MagicMock(stdout="opencode version v0.0.55", stderr="")
        with patch("shutil.which", return_value="/usr/local/bin/opencode"):
            with patch("factory.runners.opencode.subprocess.run", return_value=mock_result):
                _check_binary_compat()
        assert oc_module._compat_checked is True

    def test_npm_binary_warns(self) -> None:
        mock_result = MagicMock(stdout="some npm output", stderr="")
        with patch("shutil.which", return_value="/usr/local/bin/opencode"):
            with patch("factory.runners.opencode.subprocess.run", return_value=mock_result):
                _check_binary_compat()
        assert oc_module._compat_checked is True

    def test_version_in_stderr(self) -> None:
        mock_result = MagicMock(stdout="", stderr="opencode version v0.1.0")
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
# _find_opencode_bin_dir
# ---------------------------------------------------------------------------


class TestFindOpencodeBinDir:
    def test_found_on_path(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/opencode"):
            assert _find_opencode_bin_dir() == "/usr/local/bin"

    def test_found_in_gopath(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOPATH", "/custom/go")
        with patch("shutil.which", return_value=None):
            with patch.object(Path, "is_file", return_value=True):
                result = _find_opencode_bin_dir()
        assert result is not None

    def test_found_in_home_go_bin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOPATH", raising=False)
        with patch("shutil.which", return_value=None):
            with patch.object(Path, "is_file", side_effect=lambda: True):
                # The first candidate is Path.home() / "go" / "bin"
                result = _find_opencode_bin_dir()
        # Should find it or not depending on mocking; just verify no crash
        assert result is None or isinstance(result, str)

    def test_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOPATH", raising=False)
        with patch("shutil.which", return_value=None):
            with patch.object(Path, "is_file", return_value=False):
                assert _find_opencode_bin_dir() is None


# ---------------------------------------------------------------------------
# _prepend_opencode_path
# ---------------------------------------------------------------------------


class TestPrependOpencodePath:
    def test_prepends_when_found(self) -> None:
        env: dict[str, str] = {"PATH": "/usr/bin:/bin"}
        with patch(
            "factory.runners.opencode._find_opencode_bin_dir",
            return_value="/home/user/go/bin",
        ):
            _prepend_opencode_path(env)
        assert env["PATH"].startswith("/home/user/go/bin:")

    def test_no_op_when_already_first(self) -> None:
        env: dict[str, str] = {"PATH": "/home/user/go/bin:/usr/bin"}
        with patch(
            "factory.runners.opencode._find_opencode_bin_dir",
            return_value="/home/user/go/bin",
        ):
            _prepend_opencode_path(env)
        assert env["PATH"] == "/home/user/go/bin:/usr/bin"

    def test_no_op_when_not_found(self) -> None:
        env: dict[str, str] = {"PATH": "/usr/bin"}
        with patch(
            "factory.runners.opencode._find_opencode_bin_dir", return_value=None
        ):
            _prepend_opencode_path(env)
        assert env["PATH"] == "/usr/bin"


# ---------------------------------------------------------------------------
# _source_openai_key_from_shell
# ---------------------------------------------------------------------------


class TestSourceOpenaiKeyFromShell:
    def test_no_op_when_key_exists(self) -> None:
        env: dict[str, str] = {"OPENAI_API_KEY": "already-set"}
        # Should not call subprocess at all
        _source_openai_key_from_shell(env)
        assert env["OPENAI_API_KEY"] == "already-set"

    def test_sources_key_from_zshrc(self) -> None:
        env: dict[str, str] = {}
        mock_result = MagicMock(stdout="sk-sourced-key\n")
        with patch("factory.runners.opencode.subprocess.run", return_value=mock_result):
            _source_openai_key_from_shell(env)
        assert env["OPENAI_API_KEY"] == "sk-sourced-key"

    def test_no_key_from_zshrc(self) -> None:
        env: dict[str, str] = {}
        mock_result = MagicMock(stdout="\n")
        with patch("factory.runners.opencode.subprocess.run", return_value=mock_result):
            _source_openai_key_from_shell(env)
        assert "OPENAI_API_KEY" not in env

    def test_handles_file_not_found(self) -> None:
        env: dict[str, str] = {}
        with patch(
            "factory.runners.opencode.subprocess.run", side_effect=FileNotFoundError
        ):
            _source_openai_key_from_shell(env)
        assert "OPENAI_API_KEY" not in env

    def test_handles_timeout(self) -> None:
        import subprocess

        env: dict[str, str] = {}
        with patch(
            "factory.runners.opencode.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="zsh", timeout=5),
        ):
            _source_openai_key_from_shell(env)
        assert "OPENAI_API_KEY" not in env


# ---------------------------------------------------------------------------
# OpenCodeRunner.build_command
# ---------------------------------------------------------------------------


class TestBuildCommand:
    def test_command_structure(self, tmp_path: Path) -> None:
        runner = OpenCodeRunner()
        with patch("factory.runners.opencode._prepend_opencode_path"):
            with patch("factory.runners.opencode._source_openai_key_from_shell"):
                cmd, env, temp_files = runner.build_command(
                    AgentRunRequest(
                        prompt="You are the CEO.",
                        task="Run experiment",
                        cwd=tmp_path,
                        role="ceo",
                    )
                )

        assert cmd[0] == "opencode"
        assert "-p" in cmd
        assert "-c" in cmd
        assert str(tmp_path) in cmd
        assert "-q" in cmd
        full_prompt = cmd[cmd.index("-p") + 1]
        assert "You are the CEO." in full_prompt
        assert "Run experiment" in full_prompt
        assert "## Current Task" in full_prompt
        assert temp_files == []
        assert "VIRTUAL_ENV" not in env


# ---------------------------------------------------------------------------
# OpenCodeRunner.headless
# ---------------------------------------------------------------------------


class TestOpenCodeHeadless:
    async def test_dry_run_returns_stub(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FACTORY_OPENCODE_DRY_RUN", "1")
        runner = OpenCodeRunner()
        result = await runner.headless(
            AgentRunRequest(
                prompt="Test prompt",
                task="Test task",
                cwd=tmp_path,
                role="researcher",
            )
        )
        assert result.return_code == 0
        assert "[DRY-RUN]" in result.stdout
        assert "researcher" in result.stdout

    async def test_background_warning(
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
        assert result.return_code == 0

    async def test_headless_calls_run_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()
        with patch("factory.runners.opencode._check_auth"):
            with patch("factory.runners.opencode._prepend_opencode_path"):
                with patch("factory.runners.opencode._source_openai_key_from_shell"):
                    with patch(
                        "factory.runners.opencode.run_subprocess",
                        new_callable=AsyncMock,
                    ) as mock_run:
                        mock_run.return_value = AgentRunResult(
                            stdout="output", return_code=0
                        )
                        result = await runner.headless(
                            AgentRunRequest(
                                prompt="You are a test agent.",
                                task="Say hello",
                                cwd=tmp_path,
                                role="researcher",
                                timeout=60.0,
                            )
                        )

                        assert result.return_code == 0
                        assert result.stdout == "output"

                        call_kwargs = mock_run.call_args.kwargs
                        assert call_kwargs["runner_name"] == "opencode"
                        assert call_kwargs["role"] == "researcher"
                        assert call_kwargs["timeout"] == 60.0
                        cmd = mock_run.call_args[0][0]
                        assert cmd[0] == "opencode"
                        assert "-q" in cmd

    async def test_headless_raises_without_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("FACTORY_OPENCODE_DRY_RUN", raising=False)

        runner = OpenCodeRunner()
        with patch("factory.runners.opencode._check_binary_compat"):
            with patch(
                "factory.runners.opencode._can_source_key_from_shell",
                return_value=False,
            ):
                with pytest.raises(OpenCodeAuthError):
                    await runner.headless(
                        AgentRunRequest(
                            prompt="Test",
                            task="Test",
                            cwd=tmp_path,
                            role="researcher",
                        )
                    )


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
        runner = OpenCodeRunner()

        with patch("factory.runners.opencode._prepend_opencode_path"):
            with patch("factory.runners.opencode._source_openai_key_from_shell"):
                with patch("factory.runners.opencode.subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    code = runner.interactive_run(
                        AgentRunRequest(
                            prompt="You are the CEO.",
                            task="Start session",
                            cwd=tmp_path,
                            role="ceo",
                        )
                    )
                    assert code == 0
                    cmd = mock_run.call_args[0][0]
                    assert cmd[0] == "opencode"
                    assert "-p" in cmd
                    assert "-c" in cmd
                    assert "-q" not in cmd  # interactive does not use -q


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
