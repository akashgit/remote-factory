"""Tests for session API endpoints in factory.dashboard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from factory.dashboard.app import create_app
from factory.sessions import begin_session, complete_session, init_db


@pytest.fixture()
def projects_dir(tmp_path: Path) -> Path:
    """Create a projects directory with a factory-managed project containing sessions."""
    proj = tmp_path / "proj-a"
    factory = proj / ".factory"
    factory.mkdir(parents=True)
    (factory / "config.json").write_text(json.dumps({"goal": "Test sessions"}))

    init_db(proj)

    ceo_id = begin_session(proj, "ceo", title="Improve cycle #1")
    researcher_id = begin_session(
        proj, "researcher", parent_id=ceo_id, title="Research task",
    )
    builder_id = begin_session(
        proj, "builder", parent_id=ceo_id, title="Build task",
    )

    complete_session(proj, researcher_id, status="completed", output="Found 3 issues")
    complete_session(proj, builder_id, status="completed", output="Fixed 2 files")
    complete_session(proj, ceo_id, status="completed", output="Cycle complete")

    (tmp_path / "_ids.json").write_text(json.dumps({
        "ceo": ceo_id,
        "researcher": researcher_id,
        "builder": builder_id,
    }))

    return tmp_path


@pytest.fixture()
def session_ids(projects_dir: Path) -> dict[str, str]:
    return json.loads((projects_dir / "_ids.json").read_text())


@pytest.fixture()
def client(projects_dir: Path) -> TestClient:
    app = create_app(projects_dir)
    return TestClient(app)


class TestCyclesAPI:
    def test_list_cycles(self, client: TestClient, session_ids: dict):
        resp = client.get("/api/projects/proj-a/cycles")
        assert resp.status_code == 200
        cycles = resp.json()
        assert len(cycles) >= 1
        ceo_cycle = next(c for c in cycles if c["id"] == session_ids["ceo"])
        assert ceo_cycle["agent_role"] == "ceo"
        assert ceo_cycle["child_count"] == 2
        assert ceo_cycle["status"] == "completed"

    def test_list_cycles_with_limit(self, client: TestClient):
        resp = client.get("/api/projects/proj-a/cycles?limit=1")
        assert resp.status_code == 200
        cycles = resp.json()
        assert len(cycles) == 1

    def test_list_cycles_empty_project(self, client: TestClient, projects_dir: Path):
        proj_b = projects_dir / "proj-b"
        (proj_b / ".factory").mkdir(parents=True)
        resp = client.get("/api/projects/proj-b/cycles")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_cycle_detail(self, client: TestClient, session_ids: dict):
        ceo_id = session_ids["ceo"]
        resp = client.get(f"/api/projects/proj-a/cycles/{ceo_id}")
        assert resp.status_code == 200
        cycle = resp.json()
        assert cycle["id"] == ceo_id
        assert cycle["status"] == "completed"
        assert len(cycle["children"]) == 2
        child_roles = {c["agent_role"] for c in cycle["children"]}
        assert child_roles == {"researcher", "builder"}
        assert cycle["total_cost"] >= 0
        assert cycle["total_duration"] >= 0

    def test_cycle_detail_not_found(self, client: TestClient):
        resp = client.get("/api/projects/proj-a/cycles/nonexistent")
        assert resp.status_code == 404


class TestSessionAPI:
    def test_session_detail(self, client: TestClient, session_ids: dict):
        researcher_id = session_ids["researcher"]
        resp = client.get(f"/api/projects/proj-a/sessions/{researcher_id}")
        assert resp.status_code == 200
        session = resp.json()
        assert session["id"] == researcher_id
        assert session["agent_role"] == "researcher"
        assert session["status"] == "completed"
        assert "items" in session

    def test_session_detail_has_output_item(self, client: TestClient, session_ids: dict):
        builder_id = session_ids["builder"]
        resp = client.get(f"/api/projects/proj-a/sessions/{builder_id}")
        assert resp.status_code == 200
        session = resp.json()
        items = session["items"]
        assert len(items) >= 1
        assert any(i["data"] == "Fixed 2 files" for i in items)

    def test_session_not_found(self, client: TestClient):
        resp = client.get("/api/projects/proj-a/sessions/nonexistent")
        assert resp.status_code == 404


class TestSendMessageAPI:
    def test_message_no_claude_session(self, client: TestClient, session_ids: dict):
        ceo_id = session_ids["ceo"]
        resp = client.post(
            f"/api/projects/proj-a/sessions/{ceo_id}/message",
            json={"message": "hello"},
        )
        assert resp.status_code == 400
        assert "claude_session_id" in resp.json()["detail"]

    def test_message_empty_body(self, client: TestClient, session_ids: dict):
        ceo_id = session_ids["ceo"]
        resp = client.post(
            f"/api/projects/proj-a/sessions/{ceo_id}/message",
            json={"message": ""},
        )
        assert resp.status_code == 400

    def test_message_session_not_found(self, client: TestClient):
        resp = client.post(
            "/api/projects/proj-a/sessions/nonexistent/message",
            json={"message": "hello"},
        )
        assert resp.status_code == 404

    def test_resume_injects_system_prompt_file(
        self, client: TestClient, session_ids: dict, projects_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """When a session has an agent_role, resume should pass --system-prompt-file."""
        import sqlite3
        import subprocess

        proj = projects_dir / "proj-a"
        researcher_id = session_ids["researcher"]

        db_path = proj / ".factory" / "sessions.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE sessions SET claude_session_id = ? WHERE id = ?",
            ("fake-claude-sid", researcher_id),
        )
        conn.commit()
        conn.close()

        captured_cmd: list[str] = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)

        resp = client.post(
            f"/api/projects/proj-a/sessions/{researcher_id}/message",
            json={"message": "hello"},
        )
        assert resp.status_code == 200
        assert "--system-prompt-file" in captured_cmd
        idx = captured_cmd.index("--system-prompt-file")
        prompt_file = Path(captured_cmd[idx + 1])
        assert prompt_file.suffix == ".md"


class TestSessionsPage:
    def test_sessions_html_served(self, client: TestClient):
        resp = client.get("/sessions/proj-a")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Sessions" in resp.text
        assert "marked.parse" in resp.text


class TestCycleAggregation:
    def test_cycle_aggregates_cost(
        self, client: TestClient, session_ids: dict, projects_dir: Path,
    ):
        ceo_id = session_ids["ceo"]
        resp = client.get(f"/api/projects/proj-a/cycles/{ceo_id}")
        cycle = resp.json()
        assert "total_cost" in cycle
        assert "total_duration" in cycle
        assert isinstance(cycle["total_cost"], (int, float))
        assert isinstance(cycle["total_duration"], (int, float))

    def test_cycles_list_has_child_roles(self, client: TestClient, session_ids: dict):
        resp = client.get("/api/projects/proj-a/cycles")
        cycles = resp.json()
        ceo_cycle = next(c for c in cycles if c["id"] == session_ids["ceo"])
        child_roles = ceo_cycle.get("child_roles", "")
        assert "researcher" in child_roles
        assert "builder" in child_roles
