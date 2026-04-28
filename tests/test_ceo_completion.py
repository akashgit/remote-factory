"""Tests for factory/ceo_completion.py — CEO completion guard."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


class TestCycleState:
    """Tests for cycle state persistence (read, write, delete, staleness)."""

    def test_write_and_read_cycle_state(self, tmp_path: Path) -> None:
        """Cycle state can be written and read back."""
        from factory.ceo_completion import (
            create_cycle_state,
            read_cycle_state,
            write_cycle_state,
        )

        state = create_cycle_state("build", "Build a CLI tool")
        write_cycle_state(tmp_path, state)

        loaded = read_cycle_state(tmp_path)
        assert loaded is not None
        assert loaded.cycle_id == state.cycle_id
        assert loaded.mode == "build"
        assert loaded.initial_prompt == "Build a CLI tool"
        assert loaded.respawns == 0

    def test_read_cycle_state_nonexistent(self, tmp_path: Path) -> None:
        """read_cycle_state returns None if cycle.json doesn't exist."""
        from factory.ceo_completion import read_cycle_state

        assert read_cycle_state(tmp_path) is None

    def test_delete_cycle_state(self, tmp_path: Path) -> None:
        """delete_cycle_state removes the file and returns True."""
        from factory.ceo_completion import (
            create_cycle_state,
            delete_cycle_state,
            read_cycle_state,
            write_cycle_state,
        )

        state = create_cycle_state("improve")
        write_cycle_state(tmp_path, state)
        assert read_cycle_state(tmp_path) is not None

        deleted = delete_cycle_state(tmp_path)
        assert deleted is True
        assert read_cycle_state(tmp_path) is None

    def test_delete_cycle_state_nonexistent(self, tmp_path: Path) -> None:
        """delete_cycle_state returns False if file doesn't exist."""
        from factory.ceo_completion import delete_cycle_state

        assert delete_cycle_state(tmp_path) is False

    def test_stale_cycle_state_ignored(self, tmp_path: Path) -> None:
        """Cycle state older than 24 hours is treated as stale and ignored."""
        from factory.ceo_completion import (
            CYCLE_STALENESS_HOURS,
            read_cycle_state,
            _cycle_state_path,
        )

        # Write a cycle state with old timestamp
        state_path = _cycle_state_path(tmp_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        old_time = datetime.now(timezone.utc) - timedelta(hours=CYCLE_STALENESS_HOURS + 1)
        state_data = {
            "cycle_id": "old123",
            "started_at": old_time.isoformat(),
            "mode": "build",
            "initial_prompt": "",
            "respawns": 5,
        }
        state_path.write_text(json.dumps(state_data))

        # Should return None due to staleness
        loaded = read_cycle_state(tmp_path)
        assert loaded is None

    def test_malformed_cycle_state_ignored(self, tmp_path: Path) -> None:
        """Malformed cycle.json returns None instead of crashing."""
        from factory.ceo_completion import read_cycle_state, _cycle_state_path

        state_path = _cycle_state_path(tmp_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("NOT VALID JSON {{{")

        assert read_cycle_state(tmp_path) is None

    def test_cycle_state_truncates_long_prompt(self, tmp_path: Path) -> None:
        """Initial prompt is truncated to avoid bloat."""
        from factory.ceo_completion import create_cycle_state, write_cycle_state, read_cycle_state

        long_prompt = "x" * 5000
        state = create_cycle_state("build", long_prompt)
        write_cycle_state(tmp_path, state)

        loaded = read_cycle_state(tmp_path)
        assert loaded is not None
        assert len(loaded.initial_prompt) <= 1000


class TestBudgetAllowsRespawn:
    """Tests for _budget_allows_respawn()."""

    def test_returns_true_under_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.ceo_completion import _budget_allows_respawn

        monkeypatch.setenv("FACTORY_BOB_MAX_INVOCATIONS_PER_DAY", "10")
        (tmp_path / ".factory").mkdir()

        assert _budget_allows_respawn("bob", tmp_path) is True

    def test_returns_false_over_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.ceo_completion import _budget_allows_respawn
        from factory.runners.usage import log_usage

        monkeypatch.setenv("FACTORY_BOB_MAX_INVOCATIONS_PER_DAY", "1")
        (tmp_path / ".factory").mkdir()
        log_usage(tmp_path, "a", tmp_path, 1.0, 0, dry_run=False)

        assert _budget_allows_respawn("bob", tmp_path) is False

    def test_claude_always_allowed(self, tmp_path: Path) -> None:
        from factory.ceo_completion import _budget_allows_respawn

        assert _budget_allows_respawn("claude", tmp_path) is True
        assert _budget_allows_respawn(None, tmp_path) is True


class TestDetectIncomplete:
    """Tests for _detect_incomplete()."""

    def test_build_incomplete_no_eval_profile(self, tmp_path: Path) -> None:
        """Build mode without strategy needs eval profile."""
        from factory.ceo_completion import _detect_incomplete

        (tmp_path / ".factory").mkdir()

        gap = _detect_incomplete(tmp_path, "build")
        assert gap is not None
        assert gap.mode == "build"
        assert gap.next_item == "discovery"
        assert "no eval profile" in gap.reason

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

    def test_discover_continuation(self) -> None:
        """Discover mode continuation tells CEO to resume discovery."""
        from factory.ceo_completion import _build_continuation_task, IncompleteGap

        gap = IncompleteGap(
            mode="discover",
            planned=1,
            completed=0,
            next_item="eval_profile",
            reason="discover.incomplete",
        )

        task = _build_continuation_task(gap)
        assert "Resume Discovery" in task or "discover" in task.lower()

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

    def test_continuation_includes_mode_directive(self) -> None:
        """Continuation task includes explicit mode directive to prevent flip."""
        from factory.ceo_completion import _build_continuation_task, IncompleteGap, create_cycle_state

        gap = IncompleteGap(
            mode="build",
            planned=6,
            completed=3,
            next_item="Phase4",
            reason="build.incomplete",
        )
        cycle_state = create_cycle_state("build", "Build a CLI")

        task = _build_continuation_task(gap, cycle_state)
        assert "## CRITICAL: Mode Override" in task
        assert "CONTINUATION" in task
        assert "BUILD" in task
        assert "Do NOT re-detect mode" in task
        assert cycle_state.cycle_id in task


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

    async def test_creates_cycle_state_on_fresh_cycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fresh cycle creates cycle.json with the correct mode."""
        from factory.ceo_completion import run_ceo_with_completion_guard, read_cycle_state

        # Setup complete (so no respawns)
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n")
        exp_dir = tmp_path / ".factory" / "experiments" / "001"
        exp_dir.mkdir(parents=True)
        (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')

        mock_invoke = AsyncMock(return_value=("Done", 0))

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            # Invoke in build mode
            await run_ceo_with_completion_guard(
                tmp_path,
                "Build task",
                mode="build",
                runner_name="claude",
            )

        # Cycle state should be deleted after completion
        assert read_cycle_state(tmp_path) is None

    async def test_deletes_cycle_state_on_completion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cycle state is deleted when cycle completes successfully."""
        from factory.ceo_completion import (
            run_ceo_with_completion_guard,
            read_cycle_state,
            _cycle_state_path,
        )

        # Setup complete
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n")
        exp_dir = tmp_path / ".factory" / "experiments" / "001"
        exp_dir.mkdir(parents=True)
        (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')

        async def mock_invoke(role, task, path, **kwargs):
            # Verify cycle state exists during invocation
            assert read_cycle_state(path) is not None
            return "Done", 0

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            await run_ceo_with_completion_guard(
                tmp_path,
                "Improve task",
                mode="improve",
                runner_name="claude",
            )

        # After completion, cycle state should be gone
        assert not _cycle_state_path(tmp_path).exists()

    async def test_mode_preserved_across_respawns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mode from initial cycle is preserved across all respawns."""
        from factory.ceo_completion import run_ceo_with_completion_guard, read_cycle_state

        # Setup: 2 hypotheses
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n\n#### H2: B\n")
        (tmp_path / ".factory" / "experiments").mkdir(parents=True)

        call_count = 0
        observed_modes = []

        async def mock_invoke(role, task, path, **kwargs):
            nonlocal call_count
            call_count += 1

            # Record mode from cycle state
            state = read_cycle_state(path)
            if state:
                observed_modes.append(state.mode)

            # First call: create 1 verdict
            if call_count == 1:
                exp_dir = path / ".factory" / "experiments" / "001"
                exp_dir.mkdir(parents=True, exist_ok=True)
                (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')
                return "First run", 0

            # Second call: create 2nd verdict (complete)
            exp_dir = path / ".factory" / "experiments" / "002"
            exp_dir.mkdir(parents=True, exist_ok=True)
            (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')
            return "Second run", 0

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            await run_ceo_with_completion_guard(
                tmp_path,
                "Build task",
                mode="build",  # Start in build mode
                runner_name="claude",
            )

        assert call_count == 2
        # Both invocations should see the same mode
        assert all(m == "build" for m in observed_modes)

    async def test_respawn_increments_counter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each respawn increments the respawn counter in cycle state."""
        from factory.ceo_completion import run_ceo_with_completion_guard, read_cycle_state

        # Setup: 3 hypotheses
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n\n#### H2: B\n\n#### H3: C\n")
        (tmp_path / ".factory" / "experiments").mkdir(parents=True)

        call_count = 0
        observed_respawns = []

        async def mock_invoke(role, task, path, **kwargs):
            nonlocal call_count
            call_count += 1

            state = read_cycle_state(path)
            if state:
                observed_respawns.append(state.respawns)

            # Each call creates one verdict
            exp_dir = path / ".factory" / "experiments" / f"00{call_count}"
            exp_dir.mkdir(parents=True, exist_ok=True)
            (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')
            return f"Run {call_count}", 0

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            await run_ceo_with_completion_guard(
                tmp_path,
                "Improve task",
                mode="improve",
                runner_name="claude",
            )

        assert call_count == 3
        # Respawn counter: 0, 1, 2
        assert observed_respawns == [0, 1, 2]

    async def test_respawn_event_includes_cycle_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Respawn events include the cycle_id for correlation."""
        from factory.ceo_completion import run_ceo_with_completion_guard

        # Setup: 2 hypotheses
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("#### H1: A\n\n#### H2: B\n")
        (tmp_path / ".factory" / "experiments").mkdir(parents=True)

        call_count = 0

        async def mock_invoke(role, task, path, **kwargs):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                exp_dir = path / ".factory" / "experiments" / "001"
                exp_dir.mkdir(parents=True, exist_ok=True)
                (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')
                return "First", 0

            exp_dir = path / ".factory" / "experiments" / "002"
            exp_dir.mkdir(parents=True, exist_ok=True)
            (exp_dir / "verdict.json").write_text('{"verdict": "keep"}')
            return "Second", 0

        with patch("factory.agents.runner.invoke_agent", mock_invoke):
            await run_ceo_with_completion_guard(
                tmp_path,
                "Task",
                mode="improve",
                runner_name="claude",
            )

        # Check respawn event has cycle_id
        events_file = tmp_path / ".factory" / "events.jsonl"
        events = [json.loads(line) for line in events_file.read_text().splitlines()]
        respawn_events = [e for e in events if e["type"] == "ceo.respawn"]
        assert len(respawn_events) == 1
        assert "cycle_id" in respawn_events[0]["data"]
        assert "mode" in respawn_events[0]["data"]
        assert respawn_events[0]["data"]["mode"] == "improve"


class TestAutoDetectModeWithCycle:
    """Tests for _auto_detect_mode respecting in-flight cycles."""

    def test_returns_cycle_mode_when_inflight(self, tmp_path: Path) -> None:
        """_auto_detect_mode returns cycle mode when cycle.json exists."""
        from factory.cli import _auto_detect_mode
        from factory.ceo_completion import create_cycle_state, write_cycle_state

        # Create a git repo so state detection doesn't return NO_REPO
        (tmp_path / ".git").mkdir()

        # Write in-flight cycle state for build mode
        state = create_cycle_state("build", "Initial task")
        write_cycle_state(tmp_path, state)

        # Even though project has no factory, should return build (from cycle)
        mode = _auto_detect_mode(tmp_path, has_prompt=False)
        assert mode == "build"

    def test_ignores_cycle_when_force_fresh(self, tmp_path: Path) -> None:
        """_auto_detect_mode ignores cycle.json when force_fresh=True."""
        from factory.cli import _auto_detect_mode
        from factory.ceo_completion import create_cycle_state, write_cycle_state

        # Create a git repo
        (tmp_path / ".git").mkdir()

        # Write in-flight cycle state for build mode
        state = create_cycle_state("build", "Initial task")
        write_cycle_state(tmp_path, state)

        # With force_fresh, should detect from state (no_factory → discover)
        mode = _auto_detect_mode(tmp_path, has_prompt=False, force_fresh=True)
        assert mode == "discover"

    def test_detects_normally_when_no_cycle(self, tmp_path: Path) -> None:
        """_auto_detect_mode detects from project state when no cycle.json."""
        from factory.cli import _auto_detect_mode

        # Create a git repo
        (tmp_path / ".git").mkdir()

        # No cycle state exists
        mode = _auto_detect_mode(tmp_path, has_prompt=False)
        assert mode == "discover"  # no_factory state

    def test_detects_normally_when_cycle_stale(self, tmp_path: Path) -> None:
        """_auto_detect_mode ignores stale cycle.json."""
        from factory.cli import _auto_detect_mode
        from factory.ceo_completion import CYCLE_STALENESS_HOURS, _cycle_state_path

        # Create a git repo
        (tmp_path / ".git").mkdir()

        # Write stale cycle state
        state_path = _cycle_state_path(tmp_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        old_time = datetime.now(timezone.utc) - timedelta(hours=CYCLE_STALENESS_HOURS + 1)
        state_data = {
            "cycle_id": "old123",
            "started_at": old_time.isoformat(),
            "mode": "build",
            "initial_prompt": "",
            "respawns": 0,
        }
        state_path.write_text(json.dumps(state_data))

        # Should ignore stale cycle and detect from state
        mode = _auto_detect_mode(tmp_path, has_prompt=False)
        assert mode == "discover"  # no_factory state
