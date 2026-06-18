"""Tests for session-related dashboard API endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from factory.dashboard.app import create_app
from factory.sessions import begin_session, complete_session, init_db


@pytest.fixture()
def session_projects_dir(tmp_path: Path) -> Path:
    """Create a projects directory with a project that has session data."""
    proj = tmp_path / "proj-sess"
    factory = proj / ".factory"
    factory.mkdir(parents=True)

    init_db(proj)

    root_id = begin_session(proj, "ceo", title="cycle-1")
    builder_id = begin_session(
        proj, "builder", parent_id=root_id, root_id=root_id, title="build task",
    )
    reviewer_id = begin_session(
        proj, "reviewer", parent_id=root_id, root_id=root_id, title="review task",
    )

    complete_session(proj, builder_id, status="completed", output="Built the feature successfully.")
    complete_session(proj, reviewer_id, status="completed", output="Review looks good.")
    complete_session(proj, root_id, status="completed", output="Cycle complete.")

    return tmp_path


@pytest.fixture()
def session_client(session_projects_dir: Path) -> TestClient:
    app = create_app(session_projects_dir)
    return TestClient(app)


class TestSessionsListAPI:
    def test_list_sessions_returns_200(self, session_client: TestClient):
        resp = session_client.get("/api/projects/proj-sess/sessions")
        assert resp.status_code == 200
        sessions = resp.json()
        assert isinstance(sessions, list)
        assert len(sessions) == 3

    def test_list_sessions_includes_child_count(self, session_client: TestClient):
        resp = session_client.get("/api/projects/proj-sess/sessions")
        sessions = resp.json()
        ceo = next(s for s in sessions if s["agent_role"] == "ceo")
        assert ceo["child_count"] == 2
        builder = next(s for s in sessions if s["agent_role"] == "builder")
        assert builder["child_count"] == 0

    def test_list_sessions_filter_by_role(self, session_client: TestClient):
        resp = session_client.get("/api/projects/proj-sess/sessions?role=builder")
        sessions = resp.json()
        assert len(sessions) == 1
        assert sessions[0]["agent_role"] == "builder"

    def test_list_sessions_filter_by_cycle(self, session_projects_dir: Path, session_client: TestClient):
        resp = session_client.get("/api/projects/proj-sess/sessions?role=ceo")
        ceo = resp.json()[0]
        root_id = ceo["id"]

        resp2 = session_client.get(f"/api/projects/proj-sess/sessions?cycle={root_id}")
        sessions = resp2.json()
        assert len(sessions) == 3
        for s in sessions:
            assert s["root_id"] == root_id

    def test_list_sessions_empty_project(self, tmp_path: Path):
        proj = tmp_path / "proj-empty"
        (proj / ".factory").mkdir(parents=True)
        app = create_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/projects/proj-empty/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_sessions_limit(self, session_client: TestClient):
        resp = session_client.get("/api/projects/proj-sess/sessions?limit=1")
        assert len(resp.json()) == 1


class TestSessionDetailAPI:
    def test_get_session_with_items(self, session_client: TestClient):
        list_resp = session_client.get("/api/projects/proj-sess/sessions?role=builder")
        builder = list_resp.json()[0]
        session_id = builder["id"]

        resp = session_client.get(f"/api/projects/proj-sess/sessions/{session_id}")
        assert resp.status_code == 200
        session = resp.json()
        assert session["id"] == session_id
        assert session["agent_role"] == "builder"
        assert "items" in session
        assert len(session["items"]) == 1
        assert session["items"][0]["type"] == "message"
        assert session["items"][0]["role"] == "assistant"
        assert "Built the feature" in session["items"][0]["data"]

    def test_get_session_404_on_missing(self, session_client: TestClient):
        resp = session_client.get("/api/projects/proj-sess/sessions/sess_nonexist")
        assert resp.status_code == 404

    def test_get_session_invalid_id_rejected(self, session_client: TestClient):
        resp = session_client.get("/api/projects/proj-sess/sessions/../etc")
        assert resp.status_code in (400, 404, 422)


class TestSessionChildrenAPI:
    def test_get_children(self, session_client: TestClient):
        list_resp = session_client.get("/api/projects/proj-sess/sessions?role=ceo")
        ceo = list_resp.json()[0]
        ceo_id = ceo["id"]

        resp = session_client.get(f"/api/projects/proj-sess/sessions/{ceo_id}/children")
        assert resp.status_code == 200
        children = resp.json()
        assert len(children) == 2
        roles = {c["agent_role"] for c in children}
        assert roles == {"builder", "reviewer"}

    def test_children_include_child_count(self, session_client: TestClient):
        list_resp = session_client.get("/api/projects/proj-sess/sessions?role=ceo")
        ceo_id = list_resp.json()[0]["id"]

        resp = session_client.get(f"/api/projects/proj-sess/sessions/{ceo_id}/children")
        children = resp.json()
        for child in children:
            assert "child_count" in child
            assert child["child_count"] == 0

    def test_children_include_last_message_preview(self, session_client: TestClient):
        list_resp = session_client.get("/api/projects/proj-sess/sessions?role=ceo")
        ceo_id = list_resp.json()[0]["id"]

        resp = session_client.get(f"/api/projects/proj-sess/sessions/{ceo_id}/children")
        children = resp.json()
        builder = next(c for c in children if c["agent_role"] == "builder")
        assert builder["last_message_preview"] is not None
        assert "Built the feature" in builder["last_message_preview"]

    def test_children_empty_for_leaf_session(self, session_client: TestClient):
        list_resp = session_client.get("/api/projects/proj-sess/sessions?role=builder")
        builder_id = list_resp.json()[0]["id"]

        resp = session_client.get(f"/api/projects/proj-sess/sessions/{builder_id}/children")
        assert resp.status_code == 200
        assert resp.json() == []


class TestSessionsPathValidation:
    def test_invalid_project_name_rejected(self, session_client: TestClient):
        resp = session_client.get("/api/projects/../etc/sessions")
        assert resp.status_code in (400, 404, 422)

    def test_invalid_session_id_rejected(self, session_client: TestClient):
        resp = session_client.get("/api/projects/proj-sess/sessions/../../etc")
        assert resp.status_code in (400, 404, 422)


class TestSessionsHTMLView:
    def test_sessions_view_returns_html(self, session_client: TestClient):
        resp = session_client.get("/sessions/proj-sess")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Sessions" in resp.text
        assert "Phase 3" in resp.text

    def test_sessions_view_invalid_name(self, session_client: TestClient):
        resp = session_client.get("/sessions/../etc")
        assert resp.status_code in (400, 404, 422)
