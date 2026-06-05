"""Tests for build-root stage and gate event emission."""

from factory.events import emit_event, load_events


class TestStageEvents:
    def test_stage_entered_event(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        event = emit_event(
            project,
            "stage.entered",
            agent="build-root-ceo",
            data={"stage": 1, "name": "DEP_RESOLVE"},
        )
        assert event["type"] == "stage.entered"
        assert event["agent"] == "build-root-ceo"
        assert event["data"]["stage"] == 1
        assert event["data"]["name"] == "DEP_RESOLVE"

        events = load_events(project)
        assert len(events) == 1
        assert events[0]["type"] == "stage.entered"

    def test_stage_cycle_event(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        event = emit_event(
            project,
            "stage.cycle",
            agent="build-root-ceo",
            data={"stage": 3, "cycle": 2, "metric": {"passed": 10, "failed": 5}},
        )
        assert event["type"] == "stage.cycle"
        assert event["data"]["stage"] == 3
        assert event["data"]["cycle"] == 2
        assert event["data"]["metric"]["passed"] == 10
        assert event["data"]["metric"]["failed"] == 5

    def test_stage_completed_event(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        event = emit_event(
            project,
            "stage.completed",
            agent="build-root-ceo",
            data={"stage": 2, "name": "ARTIFACT_RECOVERY", "cycles": 5},
        )
        assert event["type"] == "stage.completed"
        assert event["data"]["stage"] == 2
        assert event["data"]["cycles"] == 5


class TestGateEvents:
    def test_gate_raised_event(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        event = emit_event(
            project,
            "gate.raised",
            agent="build-root-ceo",
            data={
                "gate_type": "plateau",
                "stage": 3,
                "context": "Stage 3 stuck after 3 cycles, 45 min elapsed",
            },
        )
        assert event["type"] == "gate.raised"
        assert event["data"]["gate_type"] == "plateau"
        assert event["data"]["stage"] == 3
        assert "context" in event["data"]

    def test_gate_resolved_event(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        event = emit_event(
            project,
            "gate.resolved",
            agent="build-root-ceo",
            data={
                "gate_type": "plateau",
                "stage": 3,
                "resolution": "continue",
                "action": "Try excluding spring-webmvc module",
            },
        )
        assert event["type"] == "gate.resolved"
        assert event["data"]["gate_type"] == "plateau"
        assert event["data"]["resolution"] == "continue"
        assert event["data"]["action"] == "Try excluding spring-webmvc module"

    def test_gate_events_persist(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        emit_event(project, "gate.raised", agent="build-root-ceo", data={"gate_type": "timeout", "stage": 1})
        emit_event(project, "gate.resolved", agent="build-root-ceo", data={"gate_type": "timeout", "resolution": "skip"})

        events = load_events(project)
        assert len(events) == 2
        assert events[0]["type"] == "gate.raised"
        assert events[1]["type"] == "gate.resolved"
