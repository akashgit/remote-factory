"""Tests for the factory visualizer state inference module."""

from factory.visualizer.state import (
    PHASES,
    AgentActivity,
    FactoryLiveState,
    get_phases_for_mode,
    active_agent_count,
    completed_phases,
    format_elapsed,
    infer_mode_from_artifacts,
    infer_state,
    phase_index,
    update_state,
)


def _event(event_type: str, *, agent: str | None = None, data: dict | None = None, ts: str = "2026-05-03T12:00:00Z") -> dict:
    return {"type": event_type, "timestamp": ts, "project": "test-project", "agent": agent, "data": data or {}}


class TestInferStateEmpty:
    def test_empty_events(self):
        state = infer_state([])
        assert state.active_agents == {}
        assert state.current_phase is None
        assert state.current_mode is None
        assert state.current_experiment is None


class TestAgentTracking:
    def test_agent_started_adds_to_active(self):
        events = [_event("agent.started", agent="builder", data={"task": "implement feature"})]
        state = infer_state(events)
        assert "builder" in state.active_agents
        assert state.active_agents["builder"].role == "builder"
        assert state.active_agents["builder"].task == "implement feature"

    def test_agent_completed_removes_from_active(self):
        events = [
            _event("agent.started", agent="builder", data={"task": "implement feature"}),
            _event("agent.completed", agent="builder", data={"return_code": 0}),
        ]
        state = infer_state(events)
        assert "builder" not in state.active_agents

    def test_agent_failed_removes_from_active(self):
        events = [
            _event("agent.started", agent="reviewer", data={"task": "review code"}),
            _event("agent.failed", agent="reviewer"),
        ]
        state = infer_state(events)
        assert "reviewer" not in state.active_agents

    def test_agent_timeout_removes_from_active(self):
        events = [
            _event("agent.started", agent="researcher", data={"task": "analyze"}),
            _event("agent.timeout", agent="researcher"),
        ]
        state = infer_state(events)
        assert "researcher" not in state.active_agents

    def test_multiple_agents_active(self):
        events = [
            _event("agent.started", agent="researcher", data={"task": "research"}),
            _event("agent.started", agent="builder", data={"task": "build"}),
        ]
        state = infer_state(events)
        assert len(state.active_agents) == 2
        assert "researcher" in state.active_agents
        assert "builder" in state.active_agents

    def test_task_truncated_to_100_chars(self):
        long_task = "x" * 200
        events = [_event("agent.started", agent="builder", data={"task": long_task})]
        state = infer_state(events)
        assert len(state.active_agents["builder"].task) == 100


class TestPhaseInference:
    def test_detect_phase(self):
        state = infer_state([_event("detect", data={"state": "new"})])
        assert state.current_phase == "Research"
        assert state.current_mode == "Build"

    def test_discover_phase(self):
        state = infer_state([_event("discover.started")])
        assert state.current_phase == "Discover"

    def test_agent_sets_phase(self):
        cases = [
            ("researcher", "Research"),
            ("strategist", "Strategize"),
            ("builder", "Build"),
            ("reviewer", "Review"),
            ("evaluator", "Eval"),
            ("archivist", "Archive"),
        ]
        for agent, expected_phase in cases:
            events = [_event("agent.started", agent=agent, data={"task": "work"})]
            state = infer_state(events)
            assert state.current_phase == expected_phase, f"Agent {agent} should set phase {expected_phase}"

    def test_eval_events_set_phase(self):
        state = infer_state([_event("eval.started", data={"command": "python eval/score.py"})])
        assert state.current_phase == "Eval"

    def test_archive_completed_sets_phase(self):
        state = infer_state([_event("archive.completed", data={"experiments": 5})])
        assert state.current_phase == "Archive"

    def test_phase_progresses_through_pipeline(self):
        events = [
            _event("detect", data={"state": "running"}),
            _event("discover.started"),
            _event("discover.completed"),
            _event("agent.started", agent="researcher", data={"task": "observe"}),
            _event("agent.completed", agent="researcher"),
            _event("agent.started", agent="strategist", data={"task": "plan"}),
            _event("agent.completed", agent="strategist"),
            _event("agent.started", agent="builder", data={"task": "code"}),
        ]
        state = infer_state(events)
        assert state.current_phase == "Build"


class TestModeInference:
    def test_mode_from_cycle_started(self):
        events = [_event("cycle.started", data={"mode": "Research"})]
        state = infer_state(events)
        assert state.current_mode == "Research"

    def test_mode_from_detect_new(self):
        events = [_event("detect", data={"state": "new"})]
        state = infer_state(events)
        assert state.current_mode == "Build"

    def test_mode_from_detect_running(self):
        events = [_event("detect", data={"state": "running"})]
        state = infer_state(events)
        assert state.current_mode == "Improve"

    def test_cycle_mode_overrides_detect(self):
        events = [
            _event("detect", data={"state": "new"}),
            _event("cycle.started", data={"mode": "Meta"}),
        ]
        state = infer_state(events)
        assert state.current_mode == "Meta"

    def test_detect_does_not_override_existing_mode(self):
        events = [
            _event("cycle.started", data={"mode": "Research"}),
            _event("detect", data={"state": "running"}),
        ]
        state = infer_state(events)
        assert state.current_mode == "Research"


class TestExperimentTracking:
    def test_experiment_begin(self):
        events = [_event("experiment.begin", data={"exp_id": 3, "hypothesis": "add caching"})]
        state = infer_state(events)
        assert state.current_experiment is not None
        assert state.current_experiment["id"] == 3
        assert state.current_experiment["hypothesis"] == "add caching"

    def test_experiment_finalize_clears(self):
        events = [
            _event("experiment.begin", data={"exp_id": 3, "hypothesis": "add caching"}),
            _event("experiment.finalize", data={"exp_id": 3, "verdict": "keep"}),
        ]
        state = infer_state(events)
        assert state.current_experiment is None


class TestUpdateState:
    def test_incremental_update(self):
        state = FactoryLiveState()
        state = update_state(state, _event("detect", data={"state": "new"}))
        assert state.current_phase == "Research"
        assert state.current_mode == "Build"

        state = update_state(state, _event("agent.started", agent="builder", data={"task": "work"}))
        assert state.current_phase == "Build"
        assert "builder" in state.active_agents

        state = update_state(state, _event("agent.completed", agent="builder"))
        assert "builder" not in state.active_agents


class TestToDict:
    def test_serialization(self):
        state = FactoryLiveState()
        state.active_agents["builder"] = AgentActivity(role="builder", task="work", started_at="2026-05-03T12:00:00Z")
        state.current_phase = "Build"
        state.current_mode = "Improve"
        state.current_experiment = {"id": 1, "hypothesis": "test"}

        d = state.to_dict()
        assert d["current_phase"] == "Build"
        assert d["current_mode"] == "Improve"
        assert d["current_experiment"]["id"] == 1
        assert d["active_agents"]["builder"]["role"] == "builder"
        assert d["active_agents"]["builder"]["task"] == "work"

    def test_empty_serialization(self):
        d = FactoryLiveState().to_dict()
        assert d["active_agents"] == {}
        assert d["current_phase"] is None
        assert d["current_mode"] is None
        assert d["current_experiment"] is None


class TestPhaseIndex:
    def test_known_phase(self):
        assert phase_index("Detect") == 0
        assert phase_index("Build") == 4
        assert phase_index("Archive") == 7

    def test_none_phase(self):
        assert phase_index(None) == -1

    def test_unknown_phase(self):
        assert phase_index("Unknown") == -1


class TestCompletedPhases:
    def test_no_phase(self):
        state = FactoryLiveState()
        assert completed_phases(state) == []

    def test_first_phase(self):
        state = FactoryLiveState(current_phase="Detect")
        assert completed_phases(state) == []

    def test_middle_phase(self):
        state = FactoryLiveState(current_phase="Build")
        assert completed_phases(state) == ["Detect", "Discover", "Research", "Strategize"]

    def test_last_phase(self):
        state = FactoryLiveState(current_phase="Archive")
        assert completed_phases(state) == ["Detect", "Discover", "Research", "Strategize", "Build", "Review", "Eval"]


class TestActiveAgentCount:
    def test_empty(self):
        assert active_agent_count(FactoryLiveState()) == 0

    def test_with_agents(self):
        state = FactoryLiveState()
        state.active_agents["builder"] = AgentActivity(role="builder", task="work", started_at="2026-05-03T12:00:00Z")
        state.active_agents["reviewer"] = AgentActivity(role="reviewer", task="review", started_at="2026-05-03T12:00:00Z")
        assert active_agent_count(state) == 2


class TestFormatElapsed:
    def test_empty_string(self):
        assert format_elapsed("") == "0s"

    def test_invalid_timestamp(self):
        assert format_elapsed("not-a-date") == "0s"

    def test_recent_timestamp(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        result = format_elapsed(now)
        assert result.endswith("s")

    def test_old_timestamp(self):
        result = format_elapsed("2020-01-01T00:00:00+00:00")
        assert "m" in result


class TestModeAwarePhaseInference:
    def test_improve_researcher_sets_observe(self):
        events = [
            _event("cycle.started", data={"mode": "improve"}),
            _event("agent.started", agent="researcher", data={"task": "study"}),
        ]
        state = infer_state(events)
        assert state.current_phase == "Observe"

    def test_improve_strategist_sets_hypothesize(self):
        events = [
            _event("cycle.started", data={"mode": "improve"}),
            _event("agent.started", agent="strategist", data={"task": "plan"}),
        ]
        state = infer_state(events)
        assert state.current_phase == "Hypothesize"

    def test_research_failure_analyst_sets_analyze(self):
        events = [
            _event("cycle.started", data={"mode": "research"}),
            _event("agent.started", agent="failure_analyst", data={"task": "analyze"}),
        ]
        state = infer_state(events)
        assert state.current_phase == "Analyze"

    def test_research_evaluator_sets_run(self):
        events = [
            _event("cycle.started", data={"mode": "research"}),
            _event("agent.started", agent="evaluator", data={"task": "eval"}),
        ]
        state = infer_state(events)
        assert state.current_phase == "Run"

    def test_build_strategist_sets_plan(self):
        events = [
            _event("cycle.started", data={"mode": "build"}),
            _event("agent.started", agent="strategist", data={"task": "plan"}),
        ]
        state = infer_state(events)
        assert state.current_phase == "Plan"

    def test_no_mode_uses_generic_mapping(self):
        events = [
            _event("agent.started", agent="researcher", data={"task": "study"}),
        ]
        state = infer_state(events)
        assert state.current_phase == "Research"

    def test_meta_ace_event_sets_ace_phase(self):
        events = [
            _event("cycle.started", data={"mode": "meta"}),
            _event("ace.started"),
        ]
        state = infer_state(events)
        assert state.current_phase == "ACE"

    def test_hypothesis_number_increments(self):
        events = [
            _event("cycle.started", data={"mode": "improve"}),
            _event("experiment.begin", data={"exp_id": 1, "hypothesis": "H1"}),
            _event("experiment.finalize", data={"exp_id": 1, "verdict": "keep"}),
            _event("experiment.begin", data={"exp_id": 2, "hypothesis": "H2"}),
        ]
        state = infer_state(events)
        assert state.hypothesis_number == 2
        assert state.current_experiment["hypothesis_number"] == 2

    def test_hypothesis_number_resets_on_new_cycle(self):
        events = [
            _event("cycle.started", data={"mode": "improve"}),
            _event("experiment.begin", data={"exp_id": 1, "hypothesis": "H1"}),
            _event("cycle.started", data={"mode": "improve"}),
        ]
        state = infer_state(events)
        assert state.hypothesis_number == 0

    def test_to_dict_includes_mode_phases(self):
        events = [_event("cycle.started", data={"mode": "improve"})]
        state = infer_state(events)
        d = state.to_dict()
        assert d["phases"] == ["Observe", "Hypothesize", "Build", "Review", "Eval", "Archive"]
        assert "Build" in d["loop_phases"]
        assert "Observe" not in d["loop_phases"]
        assert d["hypothesis_number"] == 0

    def test_to_dict_research_mode_phases(self):
        events = [_event("cycle.started", data={"mode": "research"})]
        state = infer_state(events)
        d = state.to_dict()
        assert d["phases"][0] == "Baseline"
        assert "Run" in d["loop_phases"]

    def test_to_dict_no_mode_uses_generic(self):
        d = FactoryLiveState().to_dict()
        assert d["phases"] == PHASES
        assert d["loop_phases"] == []


class TestModeAwarePhaseIndex:
    def test_improve_observe_index(self):
        assert phase_index("Observe", mode="improve") == 0

    def test_improve_hypothesize_index(self):
        assert phase_index("Hypothesize", mode="improve") == 1

    def test_improve_research_not_found(self):
        assert phase_index("Research", mode="improve") == -1

    def test_generic_research_found(self):
        assert phase_index("Research", mode=None) == 2

    def test_completed_phases_mode_aware(self):
        state = FactoryLiveState(current_phase="Build", current_mode="improve")
        assert completed_phases(state) == ["Observe", "Hypothesize"]

    def test_completed_phases_generic(self):
        state = FactoryLiveState(current_phase="Build", current_mode=None)
        assert completed_phases(state) == ["Detect", "Discover", "Research", "Strategize"]


class TestGetPhasesForMode:
    def test_improve(self):
        phases = get_phases_for_mode("improve")
        assert phases[0] == "Observe"
        assert "Archive" in phases

    def test_research(self):
        phases = get_phases_for_mode("research")
        assert phases[0] == "Baseline"
        assert "Run" in phases

    def test_unknown_mode(self):
        assert get_phases_for_mode("unknown") == PHASES

    def test_none_mode(self):
        assert get_phases_for_mode(None) == PHASES


class TestInferModeFromArtifacts:
    def test_improve_from_config(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "config.json").write_text('{"goal":"test"}')
        assert infer_mode_from_artifacts(factory_dir) == "improve"

    def test_research_from_target(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "config.json").write_text(
            '{"goal":"test","research_target":{"metric":"latency"}}'
        )
        assert infer_mode_from_artifacts(factory_dir) == "research"

    def test_discover_from_profile(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "eval_profile.json").write_text('{"project_type":"python"}')
        assert infer_mode_from_artifacts(factory_dir) == "discover"

    def test_none_from_empty(self, tmp_path):
        assert infer_mode_from_artifacts(tmp_path / ".factory") is None

    def test_none_from_missing(self, tmp_path):
        assert infer_mode_from_artifacts(tmp_path / "nonexistent") is None
