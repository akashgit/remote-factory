"""Tests for the cycle graph API endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from factory.dashboard.app import create_app
from factory.sessions import begin_session, complete_session, init_db


@pytest.fixture()
def graph_project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Create a project with a realistic cycle: CEO + 3 children + events."""
    proj = tmp_path / "proj-graph"
    factory = proj / ".factory"
    factory.mkdir(parents=True)
    (factory / "config.json").write_text(json.dumps({"goal": "Test graph"}))

    init_db(proj)

    ceo_id = begin_session(proj, "ceo", title="Improve cycle")
    researcher_id = begin_session(
        proj, "researcher", parent_id=ceo_id, title="Investigate codebase",
    )
    strategist_id = begin_session(
        proj, "strategist", parent_id=ceo_id, title="Plan improvements",
    )
    builder_id = begin_session(
        proj, "builder", parent_id=ceo_id, title="Build feature",
    )

    complete_session(proj, researcher_id, status="completed", output="Found 5 issues")
    complete_session(proj, strategist_id, status="completed", output="Strategy ready")
    complete_session(proj, builder_id, status="failed", output="Build error")
    complete_session(proj, ceo_id, status="completed", output="Cycle done")

    events = [
        {"type": "phase.started", "timestamp": "2025-01-01T00:00:00+00:00",
         "project": "proj-graph", "agent": "ceo", "data": {"phase": "Research"}},
        {"type": "phase.completed", "timestamp": "2025-01-01T00:05:00+00:00",
         "project": "proj-graph", "agent": "ceo", "data": {"phase": "Research"}},
        {"type": "agent.started", "timestamp": "2025-01-01T00:00:10+00:00",
         "project": "proj-graph", "agent": "researcher", "data": {}},
        {"type": "agent.completed", "timestamp": "2025-01-01T00:04:00+00:00",
         "project": "proj-graph", "agent": "researcher", "data": {"total_cost_usd": 0.10}},
    ]
    events_file = factory / "events.jsonl"
    events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    ids = {
        "ceo": ceo_id,
        "researcher": researcher_id,
        "strategist": strategist_id,
        "builder": builder_id,
    }
    return tmp_path, ids


@pytest.fixture()
def graph_client(graph_project: tuple[Path, dict]) -> TestClient:
    projects_dir, _ = graph_project
    return TestClient(create_app(projects_dir))


@pytest.fixture()
def graph_ids(graph_project: tuple[Path, dict]) -> dict[str, str]:
    _, ids = graph_project
    return ids


class TestGraphEndpoint:
    def test_graph_returns_correct_node_count(
        self, graph_client: TestClient, graph_ids: dict,
    ):
        resp = graph_client.get(
            f"/api/projects/proj-graph/cycles/{graph_ids['ceo']}/graph"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) == 4

    def test_graph_has_spawned_edges(
        self, graph_client: TestClient, graph_ids: dict,
    ):
        resp = graph_client.get(
            f"/api/projects/proj-graph/cycles/{graph_ids['ceo']}/graph"
        )
        data = resp.json()
        spawned = [e for e in data["edges"] if e["data"]["type"] == "spawned"]
        assert len(spawned) == 3
        sources = {e["data"]["source"] for e in spawned}
        assert sources == {graph_ids["ceo"]}
        targets = {e["data"]["target"] for e in spawned}
        assert targets == {
            graph_ids["researcher"],
            graph_ids["strategist"],
            graph_ids["builder"],
        }

    def test_graph_has_sequential_edges(
        self, graph_client: TestClient, graph_ids: dict,
    ):
        resp = graph_client.get(
            f"/api/projects/proj-graph/cycles/{graph_ids['ceo']}/graph"
        )
        data = resp.json()
        sequential = [e for e in data["edges"] if e["data"]["type"] == "sequential"]
        assert len(sequential) == 2

    def test_graph_has_timeline(
        self, graph_client: TestClient, graph_ids: dict,
    ):
        resp = graph_client.get(
            f"/api/projects/proj-graph/cycles/{graph_ids['ceo']}/graph"
        )
        data = resp.json()
        tl = data["timeline"]
        assert "start" in tl
        assert "end" in tl
        assert tl["start"] > 0
        assert tl["end"] >= tl["start"]

    def test_graph_node_has_expected_fields(
        self, graph_client: TestClient, graph_ids: dict,
    ):
        resp = graph_client.get(
            f"/api/projects/proj-graph/cycles/{graph_ids['ceo']}/graph"
        )
        data = resp.json()
        node_data = data["nodes"][0]["data"]
        for field in ["id", "role", "status", "label", "duration_ms",
                      "total_cost_usd", "start_time", "end_time", "type"]:
            assert field in node_data, f"Missing field: {field}"

    def test_unknown_role_produces_valid_node(
        self, graph_project: tuple[Path, dict], graph_client: TestClient,
    ):
        projects_dir, ids = graph_project
        proj = projects_dir / "proj-graph"
        custom_id = begin_session(
            proj, "my_custom_agent_v2", parent_id=ids["ceo"],
            title="Custom task",
        )
        complete_session(proj, custom_id, status="completed", output="Done")

        resp = graph_client.get(
            f"/api/projects/proj-graph/cycles/{ids['ceo']}/graph"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) == 5
        custom_nodes = [
            n for n in data["nodes"] if n["data"]["role"] == "my_custom_agent_v2"
        ]
        assert len(custom_nodes) == 1
        assert custom_nodes[0]["data"]["label"] == "my_custom_agent_v2"

    def test_graph_not_found(self, graph_client: TestClient):
        resp = graph_client.get(
            "/api/projects/proj-graph/cycles/nonexistent/graph"
        )
        assert resp.status_code == 404

    def test_empty_cycle_returns_minimal_graph(
        self, graph_project: tuple[Path, dict],
        graph_client: TestClient,
    ):
        projects_dir, _ = graph_project
        proj = projects_dir / "proj-graph"
        solo_id = begin_session(proj, "ceo", title="Solo cycle")
        complete_session(proj, solo_id, status="completed", output="Nothing")

        resp = graph_client.get(
            f"/api/projects/proj-graph/cycles/{solo_id}/graph"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) == 1
        assert len(data["edges"]) == 0

    def test_failed_child_status_in_graph(
        self, graph_client: TestClient, graph_ids: dict,
    ):
        resp = graph_client.get(
            f"/api/projects/proj-graph/cycles/{graph_ids['ceo']}/graph"
        )
        data = resp.json()
        builder_node = next(
            n for n in data["nodes"] if n["data"]["id"] == graph_ids["builder"]
        )
        assert builder_node["data"]["status"] == "failed"
