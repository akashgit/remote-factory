"""Tests for build-root mode in the visualizer state module."""

import json

from factory.visualizer.state import (
    MODE_AGENT_TO_PHASE,
    MODE_EVENT_TO_PHASE,
    MODE_PHASES,
    FactoryLiveState,
    infer_mode_from_artifacts,
    infer_state,
    update_state,
)


def _event(
    event_type: str,
    *,
    agent: str | None = None,
    data: dict | None = None,
    ts: str = "2026-05-03T12:00:00Z",
) -> dict:
    return {
        "type": event_type,
        "timestamp": ts,
        "project": "test-project",
        "agent": agent,
        "data": data or {},
    }


class TestBuildRootModePhases:
    def test_mode_phases_defined(self):
        phases = MODE_PHASES["build-root"]
        assert len(phases) == 4
        names = [p[0] for p in phases]
        assert names == ["Dep Resolve", "Artifact Recovery", "Compile", "Test"]
        assert all(p[2] is True for p in phases)

    def test_mode_agent_to_phase_defined(self):
        mapping = MODE_AGENT_TO_PHASE["build-root"]
        assert mapping["builder"] == "Compile"
        assert mapping["researcher"] == "Artifact Recovery"
        assert mapping["evaluator"] == "Test"
        assert mapping["build-root-ceo"] == "Dep Resolve"

    def test_mode_event_to_phase_defined(self):
        mapping = MODE_EVENT_TO_PHASE["build-root"]
        assert "stage.entered" in mapping
        assert "stage.completed" in mapping
        assert "stage.cycle" in mapping
        assert "gate.raised" in mapping
        assert "gate.resolved" in mapping


class TestInferModeBuildRoot:
    def test_infer_mode_build_root(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        config = {"goal": "build root", "build_root": {"project_repo": "foo", "version_tag": "v1"}}
        (factory_dir / "config.json").write_text(json.dumps(config))
        assert infer_mode_from_artifacts(factory_dir) == "build-root"

    def test_infer_mode_build_root_priority(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        config = {
            "goal": "test",
            "build_root": {"project_repo": "foo", "version_tag": "v1"},
            "research_target": {"metric": "latency"},
        }
        (factory_dir / "config.json").write_text(json.dumps(config))
        assert infer_mode_from_artifacts(factory_dir) == "build-root"


class TestUpdateStateBuildRoot:
    def test_update_state_stage_entered(self):
        state = FactoryLiveState()
        state = update_state(state, _event("stage.entered", data={"stage": 1, "name": "DEP_RESOLVE"}))
        assert state.current_phase == "Dep Resolve"
        assert state.current_mode == "build-root"

    def test_update_state_stage_cycle(self):
        state = FactoryLiveState()
        state = update_state(state, _event("stage.entered", data={"stage": 3, "name": "COMPILE"}))
        assert state.current_phase == "Compile"
        state = update_state(
            state,
            _event("stage.cycle", data={"stage": 3, "cycle": 2, "metric": {"passed": 10, "failed": 5}}),
        )
        assert state.current_phase == "Compile"
        assert state.current_mode == "build-root"

    def test_update_state_stage_completed(self):
        events = [
            _event("stage.entered", data={"stage": 2, "name": "ARTIFACT_RECOVERY"}),
            _event("stage.completed", data={"stage": 2, "name": "ARTIFACT_RECOVERY", "cycles": 5}),
            _event("stage.entered", data={"stage": 3, "name": "COMPILE"}),
        ]
        state = infer_state(events)
        assert state.current_phase == "Compile"

    def test_update_state_all_stages(self):
        stage_map = {1: "Dep Resolve", 2: "Artifact Recovery", 3: "Compile", 4: "Test"}
        for num, expected_phase in stage_map.items():
            state = FactoryLiveState()
            state = update_state(state, _event("stage.entered", data={"stage": num}))
            assert state.current_phase == expected_phase

    def test_agent_phase_in_build_root_mode(self):
        events = [
            _event("cycle.started", data={"mode": "build-root"}),
            _event("agent.started", agent="builder", data={"task": "fix compile"}),
        ]
        state = infer_state(events)
        assert state.current_phase == "Compile"

    def test_to_dict_build_root_phases(self):
        events = [_event("cycle.started", data={"mode": "build-root"})]
        state = infer_state(events)
        d = state.to_dict()
        assert d["phases"] == ["Dep Resolve", "Artifact Recovery", "Compile", "Test"]
        assert len(d["loop_phases"]) == 4
