"""Tests for build-root pipeline state reconstruction (_build_pipeline_state)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from factory.dashboard.app import _build_pipeline_state, create_app


def _make_event(
    event_type: str,
    stage: str = "",
    ts: str | None = None,
    **extra_data: object,
) -> dict:
    data: dict = {}
    if stage:
        data["stage"] = stage
    data.update(extra_data)
    return {
        "type": event_type,
        "timestamp": ts or datetime.now(timezone.utc).isoformat(),
        "project": "test-project",
        "agent": None,
        "data": data,
    }


def _ts(offset_seconds: int) -> str:
    base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=offset_seconds)).isoformat()


class TestBuildPipelineStateEmpty:
    def test_empty_events_all_pending(self):
        state = _build_pipeline_state([])
        assert len(state["stages"]) == 4
        for s in state["stages"]:
            assert s["status"] == "pending"
            assert s["cycles"] == 0
            assert s["metric"] is None
            assert s["trend"] == "flat"
            assert s["elapsed_seconds"] is None
            assert s["gate"] is False

    def test_stage_display_names(self):
        state = _build_pipeline_state([])
        names = [s["display_name"] for s in state["stages"]]
        assert names == ["DEP RESOLVE", "ARTIFACT RECOVERY", "COMPILE", "TEST"]

    def test_stage_names(self):
        state = _build_pipeline_state([])
        names = [s["name"] for s in state["stages"]]
        assert names == ["dep_resolve", "artifact_recovery", "compile", "test"]

    def test_recent_events_empty(self):
        state = _build_pipeline_state([])
        assert state["recent_events"] == []


class TestStatusTransitions:
    def test_pending_to_active(self):
        events = [_make_event("stage.entered", "dep_resolve", ts=_ts(0))]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["status"] == "active"
        assert state["stages"][1]["status"] == "pending"

    def test_active_to_completed(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("stage.completed", "dep_resolve", ts=_ts(60)),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["status"] == "completed"

    def test_active_to_gated(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("gate.raised", "dep_resolve", ts=_ts(30)),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["status"] == "gated"
        assert state["stages"][0]["gate"] is True

    def test_gated_to_active_on_gate_resolved(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("gate.raised", "dep_resolve", ts=_ts(30)),
            _make_event("gate.resolved", "dep_resolve", ts=_ts(60)),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["status"] == "active"
        assert state["stages"][0]["gate"] is False

    def test_completed_clears_gate(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("gate.raised", "dep_resolve", ts=_ts(30)),
            _make_event("stage.completed", "dep_resolve", ts=_ts(60)),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["status"] == "completed"
        assert state["stages"][0]["gate"] is False

    def test_multi_stage_progression(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("stage.completed", "dep_resolve", ts=_ts(60)),
            _make_event("stage.entered", "artifact_recovery", ts=_ts(61)),
            _make_event("stage.completed", "artifact_recovery", ts=_ts(120)),
            _make_event("stage.entered", "compile", ts=_ts(121)),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["status"] == "completed"
        assert state["stages"][1]["status"] == "completed"
        assert state["stages"][2]["status"] == "active"
        assert state["stages"][3]["status"] == "pending"


class TestCycleCountsAndMetrics:
    def test_cycle_count(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(10), resolved=5, total=10),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(20), resolved=7, total=10),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(30), resolved=9, total=10),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["cycles"] == 3
        assert state["stages"][0]["metric"] == 9
        assert state["stages"][0]["metric_total"] == 10

    def test_cycle_count_resets_on_reenter(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(10), resolved=5, total=10),
            _make_event("stage.entered", "dep_resolve", ts=_ts(20)),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(30), resolved=3, total=10),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["cycles"] == 1

    def test_no_metric_when_absent(self):
        events = [
            _make_event("stage.entered", "compile", ts=_ts(0)),
            _make_event("stage.cycle", "compile", ts=_ts(10)),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][2]["cycles"] == 1
        assert state["stages"][2]["metric"] is None


class TestTrendIndicators:
    def test_trend_up(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(10), resolved=3, total=10),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(20), resolved=5, total=10),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(30), resolved=8, total=10),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["trend"] == "up"

    def test_trend_down(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(10), resolved=8, total=10),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(20), resolved=6, total=10),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(30), resolved=3, total=10),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["trend"] == "down"

    def test_trend_flat(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(10), resolved=5, total=10),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(20), resolved=5, total=10),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(30), resolved=5, total=10),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["trend"] == "flat"

    def test_trend_not_computed_under_3_cycles(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(10), resolved=3, total=10),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(20), resolved=8, total=10),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["trend"] == "flat"


class TestElapsedTime:
    def test_elapsed_for_active_stage(self):
        events = [
            _make_event("stage.entered", "compile", ts=_ts(0)),
            _make_event("stage.cycle", "compile", ts=_ts(45)),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][2]["elapsed_seconds"] == pytest.approx(45.0)

    def test_no_elapsed_for_pending(self):
        state = _build_pipeline_state([])
        assert state["stages"][0]["elapsed_seconds"] is None

    def test_no_elapsed_for_completed(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("stage.completed", "dep_resolve", ts=_ts(60)),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["elapsed_seconds"] is None

    def test_elapsed_uses_latest_stage_event(self):
        events = [
            _make_event("stage.entered", "compile", ts=_ts(0)),
            _make_event("stage.cycle", "compile", ts=_ts(30)),
            _make_event("stage.cycle", "compile", ts=_ts(90)),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][2]["elapsed_seconds"] == pytest.approx(90.0)


class TestGateIndicator:
    def test_gate_raised(self):
        events = [
            _make_event("stage.entered", "artifact_recovery", ts=_ts(0)),
            _make_event("gate.raised", "artifact_recovery", ts=_ts(30)),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][1]["gate"] is True
        assert state["stages"][1]["status"] == "gated"

    def test_gate_resolved(self):
        events = [
            _make_event("stage.entered", "artifact_recovery", ts=_ts(0)),
            _make_event("gate.raised", "artifact_recovery", ts=_ts(30)),
            _make_event("gate.resolved", "artifact_recovery", ts=_ts(60)),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][1]["gate"] is False
        assert state["stages"][1]["status"] == "active"

    def test_gate_on_different_stages(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("gate.raised", "dep_resolve", ts=_ts(10)),
            _make_event("stage.entered", "compile", ts=_ts(20)),
            _make_event("gate.raised", "compile", ts=_ts(30)),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["gate"] is True
        assert state["stages"][2]["gate"] is True


class TestRecentEvents:
    def test_recent_events_are_stage_events_only(self):
        events = [
            _make_event("agent.started", ts=_ts(0)),
            _make_event("stage.entered", "dep_resolve", ts=_ts(1)),
            _make_event("agent.completed", ts=_ts(2)),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(3), resolved=5, total=10),
        ]
        state = _build_pipeline_state(events)
        assert len(state["recent_events"]) == 2
        assert state["recent_events"][0]["type"] == "stage.entered"
        assert state["recent_events"][1]["type"] == "stage.cycle"

    def test_recent_events_capped_at_20(self):
        events = [_make_event("stage.entered", "dep_resolve", ts=_ts(0))]
        for i in range(25):
            events.append(
                _make_event("stage.cycle", "dep_resolve", ts=_ts(i + 1), resolved=i, total=30)
            )
        state = _build_pipeline_state(events)
        assert len(state["recent_events"]) == 20


class TestBuildRootStatusAPI:
    @pytest.fixture()
    def br_projects_dir(self, tmp_path: Path) -> Path:
        proj = tmp_path / "proj-br"
        factory = proj / ".factory"
        factory.mkdir(parents=True)
        return tmp_path

    @pytest.fixture()
    def br_client(self, br_projects_dir: Path) -> TestClient:
        app = create_app(br_projects_dir)
        return TestClient(app)

    def test_empty_project_returns_all_pending(self, br_client: TestClient):
        resp = br_client.get("/api/projects/proj-br/build-root-status")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["stages"]) == 4
        for s in data["stages"]:
            assert s["status"] == "pending"

    def test_with_stage_events(
        self, br_client: TestClient, br_projects_dir: Path
    ):
        from factory.events import emit_event

        proj = br_projects_dir / "proj-br"
        emit_event(proj, "stage.entered", data={"stage": "dep_resolve"})
        emit_event(
            proj,
            "stage.cycle",
            data={"stage": "dep_resolve", "resolved": 7, "total": 10},
        )

        resp = br_client.get("/api/projects/proj-br/build-root-status")
        data = resp.json()
        assert data["stages"][0]["status"] == "active"
        assert data["stages"][0]["cycles"] == 1
        assert data["stages"][0]["metric"] == 7
        assert data["stages"][0]["metric_total"] == 10

    def test_invalid_project_name_rejected(self, br_client: TestClient):
        resp = br_client.get("/api/projects/../etc/build-root-status")
        assert resp.status_code in (400, 404, 422)
