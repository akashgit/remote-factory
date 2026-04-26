"""Tests for factory/ceo_completion.py — CEO completion guard."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


class TestDetectIncomplete:
    """Tests for _detect_incomplete()."""

    def test_improve_complete_when_all_verdicts(self, tmp_path: Path) -> None:
        """Improve mode is complete when verdict count >= hypothesis count."""
        from factory.ceo_completion import _detect_incomplete

        # Setup: 2 hypotheses in strategy
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text(
            "### Hypotheses\n\n#### H1: First\n\n#### H2: Second\n"
        )

        # Setup: 2 verdicts
        for i in (1, 2):
            exp_dir = tmp_path / ".factory" / "experiments" / f"00{i}"
            exp_dir.mkdir(parents=True)
            (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')

        gap = _detect_incomplete(tmp_path, "improve")
        assert gap is None

    def test_improve_incomplete_when_missing_verdicts(self, tmp_path: Path) -> None:
        """Improve mode is incomplete when verdict count < hypothesis count."""
        from factory.ceo_completion import _detect_incomplete

        # Setup: 3 hypotheses
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text(
            "### Hypotheses\n\n#### H1: First\n\n#### H2: Second\n\n#### H3: Third\n"
        )

        # Setup: only 1 verdict
        exp_dir = tmp_path / ".factory" / "experiments" / "001"
        exp_dir.mkdir(parents=True)
        (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')

        gap = _detect_incomplete(tmp_path, "improve")
        assert gap is not None
        assert gap.planned == 3
        assert gap.completed == 1
        assert gap.next_item == "H2"
        assert "improve.incomplete" in gap.reason

    def test_improve_no_strategy_returns_none(self, tmp_path: Path) -> None:
        """No strategy file means nothing planned — not incomplete."""
        from factory.ceo_completion import _detect_incomplete

        (tmp_path / ".factory").mkdir()

        gap = _detect_incomplete(tmp_path, "improve")
        assert gap is None

    def test_discover_complete_when_profile_exists(self, tmp_path: Path) -> None:
        """Discover mode is complete when eval_profile.json exists."""
        from factory.ceo_completion import _detect_incomplete

        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "eval_profile.json").write_text('{"dimensions": []}')

        gap = _detect_incomplete(tmp_path, "discover")
        assert gap is None

    def test_discover_incomplete_when_no_profile(self, tmp_path: Path) -> None:
        """Discover mode is incomplete without eval_profile.json."""
        from factory.ceo_completion import _detect_incomplete

        (tmp_path / ".factory").mkdir()

        gap = _detect_incomplete(tmp_path, "discover")
        assert gap is not None
        assert gap.mode == "discover"
        assert "no eval_profile.json" in gap.reason


class TestBuildContinuationTask:
    """Tests for _build_continuation_task()."""

    def test_improve_continuation(self) -> None:
        """Improve mode continuation tells CEO to spawn Builder for next H."""
        from factory.ceo_completion import _build_continuation_task, IncompleteGap

        gap = IncompleteGap(
            mode="improve",
            planned=5,
            completed=2,
            next_item="H3",
            reason="improve.incomplete",
        )

        task = _build_continuation_task(gap)
        assert "Resume execution from hypothesis H3" in task
        assert "do not re-plan" in task
        assert "Spawn Builder for H3" in task
        assert "2/5" in task

    def test_build_continuation(self) -> None:
        """Build mode continuation tells CEO to resume from next phase."""
        from factory.ceo_completion import _build_continuation_task, IncompleteGap

        gap = IncompleteGap(
            mode="build",
            planned=6,
            completed=3,
            next_item="Phase4",
            reason="build.incomplete",
        )

        task = _build_continuation_task(gap)
        assert "Resume Build pipeline" in task
        assert "Phase4" in task


class TestRunCeoWithCompletionGuard:
    """Tests for run_ceo_with_completion_guard()."""

    @pytest.fixture(autouse=True)
    def enable_respawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Enable respawn for all tests in this class (disabled globally in conftest)."""
        monkeypatch.delenv("FACTORY_CEO_RESPAWN_DISABLED", raising=False)

    async def test_complete_on_first_try_no_respawn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If CEO completes all work in one spawn, no respawn occurs."""
        from factory.ceo_completion import run_ceo_with_completion_guard

        # Setup: 2 hypotheses, 2 verdicts (complete)
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n\n#### H2: B\n")

        for i in (1, 2):
            exp_dir = tmp_path / ".factory" / "experiments" / f"00{i}"
            exp_dir.mkdir(parents=True)
            (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')

        mock_invoke = AsyncMock(return_value=("CEO output", 0))

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            result, code = await run_ceo_with_completion_guard(
                tmp_path,
                "Initial task",
                mode="improve",
                runner_name="claude",
            )

        assert code == 0
        assert mock_invoke.call_count == 1

    async def test_respawns_when_incomplete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If CEO exits with work undone, it respawns with continuation task."""
        from factory.ceo_completion import run_ceo_with_completion_guard

        # Setup: 3 hypotheses
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n\n#### H2: B\n\n#### H3: C\n")
        (tmp_path / ".factory" / "experiments").mkdir(parents=True)

        call_count = 0

        async def mock_invoke(role, task, path, **kwargs):
            nonlocal call_count
            call_count += 1

            # First call: create 1 verdict
            if call_count == 1:
                exp_dir = path / ".factory" / "experiments" / "001"
                exp_dir.mkdir(parents=True, exist_ok=True)
                (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')
                return "First run", 0

            # Second call: create 2nd verdict
            if call_count == 2:
                exp_dir = path / ".factory" / "experiments" / "002"
                exp_dir.mkdir(parents=True, exist_ok=True)
                (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')
                return "Second run", 0

            # Third call: create 3rd verdict (complete)
            exp_dir = path / ".factory" / "experiments" / "003"
            exp_dir.mkdir(parents=True, exist_ok=True)
            (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')
            return "Third run", 0

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            result, code = await run_ceo_with_completion_guard(
                tmp_path,
                "Initial task",
                mode="improve",
                runner_name="claude",
            )

        assert code == 0
        assert call_count == 3

        # Check respawn events were emitted
        events_file = tmp_path / ".factory" / "events.jsonl"
        assert events_file.exists()
        events = [json.loads(line) for line in events_file.read_text().splitlines()]
        respawn_events = [e for e in events if e["type"] == "ceo.respawn"]
        assert len(respawn_events) == 2

    async def test_respects_user_interrupt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit code 130 (SIGINT) stops respawning."""
        from factory.ceo_completion import run_ceo_with_completion_guard

        # Setup incomplete
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n")
        (tmp_path / ".factory" / "experiments").mkdir()

        mock_invoke = AsyncMock(return_value=("Interrupted", 130))

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            result, code = await run_ceo_with_completion_guard(
                tmp_path,
                "Initial task",
                mode="improve",
                runner_name="claude",
            )

        assert code == 130
        assert mock_invoke.call_count == 1

    async def test_respects_abort_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cycle.aborted event stops respawning."""
        from factory.ceo_completion import run_ceo_with_completion_guard
        from factory.events import emit_event

        # Setup incomplete
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n")
        (tmp_path / ".factory" / "experiments").mkdir()

        async def mock_invoke(role, task, path, **kwargs):
            # CEO emits abort event
            emit_event(path, "cycle.aborted", data={"reason": "unrecoverable"})
            return "Aborted", 1

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            result, code = await run_ceo_with_completion_guard(
                tmp_path,
                "Initial task",
                mode="improve",
                runner_name="claude",
            )

        assert code == 1
        # Only one call because abort was respected
        events_file = tmp_path / ".factory" / "events.jsonl"
        events = [json.loads(line) for line in events_file.read_text().splitlines()]
        respawn_events = [e for e in events if e["type"] == "ceo.respawn"]
        assert len(respawn_events) == 0

    async def test_cap_hit_writes_incomplete_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After max respawns, writes cycle-incomplete.md and exits non-zero."""
        from factory.ceo_completion import run_ceo_with_completion_guard

        # Setup incomplete - will never complete
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n")
        (tmp_path / ".factory" / "experiments").mkdir()

        mock_invoke = AsyncMock(return_value=("Incomplete", 0))

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            result, code = await run_ceo_with_completion_guard(
                tmp_path,
                "Initial task",
                mode="improve",
                runner_name="claude",
                max_respawns=2,  # Low cap for test
            )

        assert code == 1
        # 1 initial + 2 respawns = 3 calls
        assert mock_invoke.call_count == 3

        # Check incomplete file was written
        incomplete_file = strategy_dir / "cycle-incomplete.md"
        assert incomplete_file.exists()
        content = incomplete_file.read_text()
        assert "respawn_cap_hit" in content

    async def test_disabled_via_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FACTORY_CEO_RESPAWN_DISABLED=1 makes the guard a no-op."""
        from factory.ceo_completion import run_ceo_with_completion_guard

        monkeypatch.setenv("FACTORY_CEO_RESPAWN_DISABLED", "1")

        # Setup incomplete
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n")
        (tmp_path / ".factory" / "experiments").mkdir()

        mock_invoke = AsyncMock(return_value=("Output", 0))

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            result, code = await run_ceo_with_completion_guard(
                tmp_path,
                "Initial task",
                mode="improve",
                runner_name="claude",
            )

        # Only one call — no respawn even though incomplete
        assert mock_invoke.call_count == 1
