"""Tests for CEO session resume via Claude --resume/--session-id."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.models import AgentRunRequest


class TestAgentRunRequestSessionFields:
    """Tests for session_id and resume_session_id fields on AgentRunRequest."""

    def test_default_none(self) -> None:
        req = AgentRunRequest(prompt="p", task="t", cwd=Path("/tmp"))
        assert req.session_id is None
        assert req.resume_session_id is None

    def test_session_id_set(self) -> None:
        req = AgentRunRequest(
            prompt="p",
            task="t",
            cwd=Path("/tmp"),
            session_id="abc-123",
        )
        assert req.session_id == "abc-123"
        assert req.resume_session_id is None

    def test_resume_session_id_set(self) -> None:
        req = AgentRunRequest(
            prompt="p",
            task="t",
            cwd=Path("/tmp"),
            resume_session_id="xyz-789",
        )
        assert req.session_id is None
        assert req.resume_session_id == "xyz-789"


class TestCycleStateClaudeSessionId:
    """Tests for claude_session_id field on CycleState."""

    def test_default_none(self) -> None:
        from factory.ceo_completion import create_cycle_state

        state = create_cycle_state("improve")
        assert state.claude_session_id is None

    def test_round_trip(self, tmp_path: Path) -> None:
        from factory.ceo_completion import (
            create_cycle_state,
            read_cycle_state,
            write_cycle_state,
        )

        state = create_cycle_state("build")
        state.claude_session_id = "session-abc-123"
        write_cycle_state(tmp_path, state)

        loaded = read_cycle_state(tmp_path)
        assert loaded is not None
        assert loaded.claude_session_id == "session-abc-123"

    def test_round_trip_none(self, tmp_path: Path) -> None:
        from factory.ceo_completion import (
            create_cycle_state,
            read_cycle_state,
            write_cycle_state,
        )

        state = create_cycle_state("improve")
        write_cycle_state(tmp_path, state)

        loaded = read_cycle_state(tmp_path)
        assert loaded is not None
        assert loaded.claude_session_id is None


class TestRunnerMetaSessionResume:
    """Tests for supports_session_resume on RunnerMeta."""

    def test_default_false(self) -> None:
        from factory.runners.protocol import RunnerMeta

        meta = RunnerMeta(
            name="test",
            display_name="Test",
            binary="test",
            install_hint="test",
        )
        assert meta.supports_session_resume is False

    def test_claude_supports_session_resume(self) -> None:
        from factory.runners.claude import ClaudeRunner

        meta = ClaudeRunner.metadata()
        assert meta.supports_session_resume is True

class TestClaudeBuildCommandSessionFlags:
    """Tests for --session-id and --resume flags in build_command."""

    def test_session_id_flag(self, tmp_path: Path) -> None:
        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                session_id="sid-001",
            )
        )

        assert "--session-id" in cmd
        idx = cmd.index("--session-id")
        assert cmd[idx + 1] == "sid-001"
        assert "--resume" not in cmd

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_resume_flag(self, tmp_path: Path) -> None:
        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                resume_session_id="rsid-002",
            )
        )

        assert "--resume" in cmd
        idx = cmd.index("--resume")
        assert cmd[idx + 1] == "rsid-002"
        assert "--session-id" not in cmd

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_resume_takes_precedence(self, tmp_path: Path) -> None:
        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                session_id="sid-001",
                resume_session_id="rsid-002",
            )
        )

        assert "--resume" in cmd
        assert "--session-id" not in cmd

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_no_flags_when_none(self, tmp_path: Path) -> None:
        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )
        )

        assert "--session-id" not in cmd
        assert "--resume" not in cmd

        for f in temp_files:
            f.unlink(missing_ok=True)


class TestClaudeBuildInteractiveCommandSessionFlags:
    """Tests for --session-id and --resume flags in build_interactive_command."""

    def test_session_id_flag(self, tmp_path: Path) -> None:
        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                session_id="sid-i-001",
            )
        )

        assert "--session-id" in cmd
        idx = cmd.index("--session-id")
        assert cmd[idx + 1] == "sid-i-001"
        assert "--resume" not in cmd

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_resume_flag(self, tmp_path: Path) -> None:
        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                resume_session_id="rsid-i-002",
            )
        )

        assert "--resume" in cmd
        idx = cmd.index("--resume")
        assert cmd[idx + 1] == "rsid-i-002"
        assert "--session-id" not in cmd

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_no_flags_when_none(self, tmp_path: Path) -> None:
        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )
        )

        assert "--session-id" not in cmd
        assert "--resume" not in cmd

        for f in temp_files:
            f.unlink(missing_ok=True)


class TestSessionPersistence:
    """Tests for read_ceo_session_id, read_ceo_session, and write_ceo_session_id."""

    def test_write_and_read(self, tmp_path: Path) -> None:
        from factory.ceo_completion import read_ceo_session_id, write_ceo_session_id

        write_ceo_session_id(tmp_path, "test-session-123")
        result = read_ceo_session_id(tmp_path)
        assert result == "test-session-123"

    def test_read_nonexistent(self, tmp_path: Path) -> None:
        from factory.ceo_completion import read_ceo_session_id

        assert read_ceo_session_id(tmp_path) is None

    def test_read_malformed(self, tmp_path: Path) -> None:
        from factory.ceo_completion import _session_state_path, read_ceo_session_id

        path = _session_state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json{{{")

        assert read_ceo_session_id(tmp_path) is None

    def test_write_creates_directory(self, tmp_path: Path) -> None:
        from factory.ceo_completion import _session_state_path, write_ceo_session_id

        write_ceo_session_id(tmp_path, "sid-abc")
        path = _session_state_path(tmp_path)
        assert path.exists()

        data = json.loads(path.read_text())
        assert data["session_id"] == "sid-abc"
        assert "created" in data

    def test_write_stores_metadata(self, tmp_path: Path) -> None:
        from factory.ceo_completion import _session_state_path, write_ceo_session_id

        write_ceo_session_id(tmp_path, "sid-meta", interactive=True, mode="design")
        path = _session_state_path(tmp_path)
        data = json.loads(path.read_text())
        assert data["session_id"] == "sid-meta"
        assert data["interactive"] is True
        assert data["mode"] == "design"

    def test_write_defaults_metadata(self, tmp_path: Path) -> None:
        from factory.ceo_completion import _session_state_path, write_ceo_session_id

        write_ceo_session_id(tmp_path, "sid-defaults")
        path = _session_state_path(tmp_path)
        data = json.loads(path.read_text())
        assert data["interactive"] is False
        assert data["mode"] == ""

    def test_read_ceo_session_full(self, tmp_path: Path) -> None:
        from factory.ceo_completion import read_ceo_session, write_ceo_session_id

        write_ceo_session_id(tmp_path, "sid-full", interactive=False, mode="design")
        result = read_ceo_session(tmp_path)
        assert result is not None
        assert result["session_id"] == "sid-full"
        assert result["interactive"] is False
        assert result["mode"] == "design"
        assert "created" in result

    def test_read_ceo_session_nonexistent(self, tmp_path: Path) -> None:
        from factory.ceo_completion import read_ceo_session

        assert read_ceo_session(tmp_path) is None

    def test_read_ceo_session_malformed(self, tmp_path: Path) -> None:
        from factory.ceo_completion import _session_state_path, read_ceo_session

        path = _session_state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json")
        assert read_ceo_session(tmp_path) is None

    def test_delete_cycle_state_also_deletes_session(self, tmp_path: Path) -> None:
        from factory.ceo_completion import (
            create_cycle_state,
            delete_cycle_state,
            read_ceo_session_id,
            write_ceo_session_id,
            write_cycle_state,
        )

        state = create_cycle_state("improve")
        write_cycle_state(tmp_path, state)
        write_ceo_session_id(tmp_path, "session-to-delete")

        assert read_ceo_session_id(tmp_path) == "session-to-delete"

        deleted = delete_cycle_state(tmp_path)
        assert deleted is True
        assert read_ceo_session_id(tmp_path) is None


class TestCompletionGuardSessionThreading:
    """Tests for session_id threading across respawns in the completion guard."""

    @pytest.fixture(autouse=True)
    def enable_respawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FACTORY_CEO_RESPAWN_DISABLED", raising=False)

    async def test_first_spawn_uses_session_id(self, tmp_path: Path) -> None:
        """First spawn passes session_id, not resume_session_id."""
        from factory.ceo_completion import run_ceo_with_completion_guard
        from factory.events import emit_event

        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n")
        exp_dir = tmp_path / ".factory" / "experiments" / "001"
        exp_dir.mkdir(parents=True)
        (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')

        captured_kwargs: list[dict] = []

        async def mock_invoke(role, task, path, **kwargs):
            captured_kwargs.append(kwargs)
            emit_event(path, "agent.completed", agent="ceo", data={"session_id": "returned-sid"})
            return "done", 0

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            await run_ceo_with_completion_guard(
                tmp_path,
                "Initial task",
                mode="design",
                runner_name="claude",
                session_id="my-session-id",
            )

        assert len(captured_kwargs) == 1
        assert captured_kwargs[0]["session_id"] == "my-session-id"
        assert captured_kwargs[0].get("resume_session_id") is None

    async def test_respawn_uses_resume_session_id(self, tmp_path: Path) -> None:
        """Respawns pass resume_session_id captured from first spawn's events."""
        from factory.ceo_completion import run_ceo_with_completion_guard
        from factory.events import emit_event

        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n\n#### H2: B\n")
        (tmp_path / ".factory" / "experiments").mkdir(parents=True)

        call_count = 0
        captured_kwargs: list[dict] = []

        async def mock_invoke(role, task, path, **kwargs):
            nonlocal call_count
            call_count += 1
            captured_kwargs.append(kwargs)

            emit_event(path, "agent.completed", agent="ceo", data={"session_id": "captured-sid"})

            exp_dir = path / ".factory" / "experiments" / f"00{call_count}"
            exp_dir.mkdir(parents=True, exist_ok=True)
            (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')
            return f"run {call_count}", 0

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            await run_ceo_with_completion_guard(
                tmp_path,
                "Initial task",
                mode="design",
                runner_name="claude",
                session_id="initial-sid",
            )

        assert call_count == 2
        assert captured_kwargs[0]["session_id"] == "initial-sid"
        assert captured_kwargs[0].get("resume_session_id") is None
        assert captured_kwargs[1].get("session_id") is None
        assert captured_kwargs[1]["resume_session_id"] == "captured-sid"

    async def test_session_id_persisted_to_cycle_state(self, tmp_path: Path) -> None:
        """Session ID from events is persisted to CycleState.claude_session_id."""
        from factory.ceo_completion import read_cycle_state, run_ceo_with_completion_guard
        from factory.events import emit_event

        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n\n#### H2: B\n")
        (tmp_path / ".factory" / "experiments").mkdir(parents=True)

        call_count = 0

        async def mock_invoke(role, task, path, **kwargs):
            nonlocal call_count
            call_count += 1

            emit_event(path, "agent.completed", agent="ceo", data={"session_id": "persisted-sid"})

            exp_dir = path / ".factory" / "experiments" / f"00{call_count}"
            exp_dir.mkdir(parents=True, exist_ok=True)
            (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')

            if call_count == 2:
                state = read_cycle_state(path)
                assert state is not None
                assert state.claude_session_id == "persisted-sid"

            return f"run {call_count}", 0

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            await run_ceo_with_completion_guard(
                tmp_path,
                "Task",
                mode="design",
                runner_name="claude",
                session_id="initial",
            )

        assert call_count == 2


class TestCmdResume:
    """Tests for the factory resume command."""

    def test_resume_from_cycle_state_is_headless(self, tmp_path: Path) -> None:
        """CycleState presence means headless — should include -p and continuation prompt."""
        from factory.ceo_completion import create_cycle_state, write_cycle_state

        state = create_cycle_state("improve")
        state.claude_session_id = "cycle-session-id"
        write_cycle_state(tmp_path, state)

        import argparse

        args = argparse.Namespace(path=str(tmp_path), model=None)

        with (
            patch("os.execvp") as mock_exec,
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("factory.agents.runner.resolve_prompt", return_value="# CEO prompt"),
        ):
            from factory.cli.infra import cmd_resume

            cmd_resume(args)

            mock_exec.assert_called_once()
            call_args = mock_exec.call_args[0]
            assert call_args[0] == "claude"
            cmd_list = call_args[1]
            assert "--resume" in cmd_list
            assert "cycle-session-id" in cmd_list
            assert "-p" in cmd_list
            assert "--disallowedTools" in cmd_list

    def test_resume_interactive_session_no_continuation(self, tmp_path: Path) -> None:
        """Interactive sessions get a bare resume — no -p flag."""
        from factory.ceo_completion import write_ceo_session_id

        write_ceo_session_id(tmp_path, "interactive-sid", interactive=True, mode="design")

        import argparse

        args = argparse.Namespace(path=str(tmp_path), model=None)

        with (
            patch("os.execvp") as mock_exec,
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            from factory.cli.infra import cmd_resume

            cmd_resume(args)

            mock_exec.assert_called_once()
            call_args = mock_exec.call_args[0]
            cmd_list = call_args[1]
            assert "--resume" in cmd_list
            assert "interactive-sid" in cmd_list
            assert "-p" not in cmd_list
            assert "--disallowedTools" not in cmd_list

    def test_resume_headless_session_has_continuation(self, tmp_path: Path) -> None:
        """Headless sessions from session.json get a continuation prompt."""
        from factory.ceo_completion import write_ceo_session_id

        write_ceo_session_id(tmp_path, "headless-sid", interactive=False, mode="design")

        import argparse

        args = argparse.Namespace(path=str(tmp_path), model=None)

        with (
            patch("os.execvp") as mock_exec,
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("factory.agents.runner.resolve_prompt", return_value="# CEO prompt"),
        ):
            from factory.cli.infra import cmd_resume

            cmd_resume(args)

            mock_exec.assert_called_once()
            call_args = mock_exec.call_args[0]
            cmd_list = call_args[1]
            assert "-p" in cmd_list
            p_idx = cmd_list.index("-p")
            assert "Resume from where you left off" in cmd_list[p_idx + 1]
            assert "--append-system-prompt-file" in cmd_list
            assert "--disallowedTools" in cmd_list

    def test_resume_prefers_cycle_state(self, tmp_path: Path) -> None:
        """CycleState.claude_session_id takes precedence over session.json."""
        from factory.ceo_completion import (
            create_cycle_state,
            write_ceo_session_id,
            write_cycle_state,
        )

        state = create_cycle_state("improve")
        state.claude_session_id = "cycle-sid"
        write_cycle_state(tmp_path, state)
        write_ceo_session_id(tmp_path, "file-sid", interactive=True, mode="design")

        import argparse

        args = argparse.Namespace(path=str(tmp_path), model=None)

        with (
            patch("os.execvp") as mock_exec,
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("factory.agents.runner.resolve_prompt", return_value="# CEO prompt"),
        ):
            from factory.cli.infra import cmd_resume

            cmd_resume(args)

            call_args = mock_exec.call_args[0]
            cmd_list = call_args[1]
            assert "cycle-sid" in cmd_list
            assert "-p" in cmd_list

    def test_resume_no_session_found(self, tmp_path: Path) -> None:
        import argparse

        args = argparse.Namespace(path=str(tmp_path), model=None)

        from factory.cli.infra import cmd_resume

        code = cmd_resume(args)
        assert code == 1

    def test_resume_with_model(self, tmp_path: Path) -> None:
        from factory.ceo_completion import write_ceo_session_id

        write_ceo_session_id(tmp_path, "model-test-sid", interactive=True, mode="design")

        import argparse

        args = argparse.Namespace(path=str(tmp_path), model="claude-opus-4-7")

        with (
            patch("os.execvp") as mock_exec,
            patch("shutil.which", return_value="/usr/bin/claude"),
        ):
            from factory.cli.infra import cmd_resume

            cmd_resume(args)

            call_args = mock_exec.call_args[0]
            cmd_list = call_args[1]
            assert "--model" in cmd_list
            model_idx = cmd_list.index("--model")
            assert cmd_list[model_idx + 1] == "claude-opus-4-7"

    def test_resume_no_claude_binary(self, tmp_path: Path) -> None:
        from factory.ceo_completion import write_ceo_session_id

        write_ceo_session_id(tmp_path, "some-sid")

        import argparse

        args = argparse.Namespace(path=str(tmp_path), model=None)

        with patch("shutil.which", return_value=None):
            from factory.cli.infra import cmd_resume

            code = cmd_resume(args)
            assert code == 1

    def test_resume_resolve_prompt_called_with_mode(self, tmp_path: Path) -> None:
        """Headless resume passes the correct workflow_mode to resolve_prompt."""
        from factory.ceo_completion import write_ceo_session_id

        write_ceo_session_id(tmp_path, "mode-sid", interactive=False, mode="research")

        import argparse

        args = argparse.Namespace(path=str(tmp_path), model=None)

        with (
            patch("os.execvp"),
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("factory.agents.runner.resolve_prompt", return_value="# prompt") as mock_resolve,
        ):
            from factory.cli.infra import cmd_resume

            cmd_resume(args)

            mock_resolve.assert_called_once_with("ceo", tmp_path, workflow_mode="research")
