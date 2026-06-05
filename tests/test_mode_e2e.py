"""End-to-end integration tests for build-root mode.

Validates the full mode works: CLI routing -> agent prompt loading ->
config parsing -> event emission -> pipeline state reconstruction.

Marked @pytest.mark.integration — does NOT run in the default pytest suite.
Run explicitly: python -m pytest tests/test_mode_e2e.py -x -q
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.dashboard.app import _build_pipeline_state
from factory.events import emit_event
from factory.models import FactoryConfig
from factory.store import ExperimentStore

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mini-java-project"


@pytest.fixture
def java_project(tmp_path: Path) -> Path:
    """Copy the mini-java-project fixture into a temp directory."""
    dest = tmp_path / "mini-java-project"
    shutil.copytree(FIXTURE_DIR, dest)
    return dest


def _write_factory_md(project_path: Path, **overrides: str) -> Path:
    """Write a factory.md with a Build Root section."""
    fields = {
        "project_repo": str(project_path),
        "version_tag": "HEAD",
        "jdk_version": "11",
        "build_system": "gradle",
    }
    fields.update(overrides)
    lines = [
        "## Goal\n",
        "Build a verified build environment\n",
        "## Scope\n",
        "## Guards\n",
        "## Command\n",
        "echo ok\n",
        "## Threshold\n",
        "0.8\n",
        "## Build Root\n",
    ]
    for k, v in fields.items():
        lines.append(f"- {k}: {v}\n")
    md_path = project_path / "factory.md"
    md_path.write_text("".join(lines))
    return md_path


def _init_factory(project_path: Path) -> FactoryConfig:
    """Write factory.md, parse it, and initialize .factory/ directory."""
    _write_factory_md(project_path)
    store = ExperimentStore(project_path)
    stub = FactoryConfig(
        goal="stub", scope=[], guards=[], eval_command="echo ok", eval_threshold=0.8,
        constraints=[],
    )
    asyncio.run(store.init(stub))
    config = asyncio.run(store.reparse_config())
    return config


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


@pytest.mark.integration
class TestBuildRootModeRoutesCorrectly:
    """Create temp project with factory.md Build Root section, verify CLI routes to build-root-ceo."""

    def test_headless_loads_build_root_ceo_prompt(self, java_project: Path):
        _init_factory(java_project)

        mock_agent = AsyncMock(return_value=("Build root complete", 0))
        with patch("factory.agents.runner.invoke_agent", mock_agent):
            from factory.cli import main
            result = main(["ceo", str(java_project), "--mode", "build-root", "--headless"])

        assert result == 0
        call_args = mock_agent.call_args
        assert call_args[0][0] == "build-root-ceo"

    def test_task_contains_build_root_config_values(self, java_project: Path):
        _init_factory(java_project)

        mock_agent = AsyncMock(return_value=("Build root complete", 0))
        with patch("factory.agents.runner.invoke_agent", mock_agent):
            from factory.cli import main
            main(["ceo", str(java_project), "--mode", "build-root", "--headless"])

        task = mock_agent.call_args[0][1]
        assert "Mode: build-root" in task
        assert str(java_project) in task
        assert "HEAD" in task
        assert "11" in task
        assert "gradle" in task


@pytest.mark.integration
class TestBuildRootAutoDetection:
    """Verify auto-detection resolves to build-root when factory.md has Build Root section."""

    def test_auto_detect_resolves_to_build_root(self, java_project: Path, tmp_path: Path):
        import subprocess
        subprocess.run(["git", "init"], cwd=java_project, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=java_project, capture_output=True, check=True,
            env={
                "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
                "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com",
                "HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin",
            },
        )

        _init_factory(java_project)

        from factory.cli import _auto_detect_mode
        mode = _auto_detect_mode(java_project, force_fresh=True)
        assert mode == "build-root"

    def test_no_build_root_does_not_auto_detect(self, java_project: Path, tmp_path: Path):
        import subprocess
        subprocess.run(["git", "init"], cwd=java_project, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=java_project, capture_output=True, check=True,
            env={
                "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
                "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com",
                "HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin",
            },
        )

        config = FactoryConfig(
            goal="Test", scope=[], guards=[], eval_command="echo ok", eval_threshold=0.8,
            constraints=[],
        )
        store = ExperimentStore(java_project)
        asyncio.run(store.init(config))

        from factory.cli import _auto_detect_mode
        mode = _auto_detect_mode(java_project, force_fresh=True)
        assert mode != "build-root"


@pytest.mark.integration
class TestBuildRootConfigRoundtrip:
    """Parse factory.md with Build Root section, verify fields match."""

    def _init_store(self, project_path: Path) -> ExperimentStore:
        stub = FactoryConfig(
            goal="stub", scope=[], guards=[], eval_command="echo ok", eval_threshold=0.8,
            constraints=[],
        )
        store = ExperimentStore(project_path)
        asyncio.run(store.init(stub))
        return store

    def test_config_fields_match(self, java_project: Path):
        _write_factory_md(
            java_project,
            project_repo="https://github.com/spring-projects/spring-framework",
            version_tag="v5.2.9.RELEASE",
            jdk_version="11",
            build_system="gradle",
        )
        store = self._init_store(java_project)
        config = asyncio.run(store.reparse_config())

        assert config.build_root is not None
        assert config.build_root.project_repo == "https://github.com/spring-projects/spring-framework"
        assert config.build_root.version_tag == "v5.2.9.RELEASE"
        assert config.build_root.jdk_version == 11
        assert config.build_root.build_system == "gradle"

    def test_config_persists_to_json_and_reloads(self, java_project: Path):
        _write_factory_md(java_project, version_tag="v1.0.0")
        store = self._init_store(java_project)
        config = asyncio.run(store.reparse_config())
        assert config.build_root is not None

        reloaded = asyncio.run(store.read_config())
        assert reloaded.build_root is not None
        assert reloaded.build_root.version_tag == "v1.0.0"
        assert reloaded.build_root.project_repo == str(java_project)

    def test_defaults_applied(self, java_project: Path):
        _write_factory_md(java_project)
        store = self._init_store(java_project)
        config = asyncio.run(store.reparse_config())
        br = config.build_root
        assert br is not None
        assert br.known_fixes_path == "config/known-fixes.yaml"
        assert br.local_repo_path == "local-repo/"


@pytest.mark.integration
class TestBuildRootEventsEmitted:
    """Simulate stage.entered event emission, verify events.jsonl has correct payload."""

    def test_stage_entered_event_written(self, java_project: Path):
        event = emit_event(
            java_project,
            "stage.entered",
            data={"stage": "dep_resolve"},
        )

        assert event["type"] == "stage.entered"
        assert event["data"]["stage"] == "dep_resolve"

        events_file = java_project / ".factory" / "events.jsonl"
        assert events_file.exists()
        lines = events_file.read_text().strip().splitlines()
        assert len(lines) == 1
        stored = json.loads(lines[0])
        assert stored["type"] == "stage.entered"
        assert stored["data"]["stage"] == "dep_resolve"

    def test_multiple_events_appended(self, java_project: Path):
        emit_event(java_project, "stage.entered", data={"stage": "dep_resolve"})
        emit_event(
            java_project, "stage.cycle",
            data={"stage": "dep_resolve", "resolved": 5, "total": 10},
        )
        emit_event(java_project, "stage.completed", data={"stage": "dep_resolve"})

        events_file = java_project / ".factory" / "events.jsonl"
        lines = events_file.read_text().strip().splitlines()
        assert len(lines) == 3
        types = [json.loads(line)["type"] for line in lines]
        assert types == ["stage.entered", "stage.cycle", "stage.completed"]

    def test_event_has_timestamp(self, java_project: Path):
        event = emit_event(java_project, "stage.entered", data={"stage": "compile"})
        assert "timestamp" in event
        ts = datetime.fromisoformat(event["timestamp"])
        assert ts.tzinfo is not None


@pytest.mark.integration
class TestPipelineStateFromEvents:
    """Feed realistic stage.* events to _build_pipeline_state(), verify state."""

    def test_full_pipeline_progression(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(30), resolved=5, total=10),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(60), resolved=8, total=10),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(90), resolved=10, total=10),
            _make_event("stage.completed", "dep_resolve", ts=_ts(100)),
            _make_event("stage.entered", "artifact_recovery", ts=_ts(101)),
            _make_event("stage.completed", "artifact_recovery", ts=_ts(150)),
            _make_event("stage.entered", "compile", ts=_ts(151)),
            _make_event("stage.cycle", "compile", ts=_ts(200), resolved=18, total=20),
            _make_event("stage.cycle", "compile", ts=_ts(250), resolved=19, total=20),
            _make_event("stage.cycle", "compile", ts=_ts(300), resolved=20, total=20),
            _make_event("stage.completed", "compile", ts=_ts(310)),
            _make_event("stage.entered", "test", ts=_ts(311)),
            _make_event("stage.cycle", "test", ts=_ts(400), resolved=95, total=100),
            _make_event("stage.completed", "test", ts=_ts(500)),
        ]
        state = _build_pipeline_state(events)

        assert len(state["stages"]) == 4
        for s in state["stages"]:
            assert s["status"] == "completed"

    def test_mid_pipeline_with_gate(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("stage.completed", "dep_resolve", ts=_ts(60)),
            _make_event("stage.entered", "artifact_recovery", ts=_ts(61)),
            _make_event("stage.cycle", "artifact_recovery", ts=_ts(90), resolved=2, total=5),
            _make_event("gate.raised", "artifact_recovery", ts=_ts(120)),
        ]
        state = _build_pipeline_state(events)

        assert state["stages"][0]["status"] == "completed"
        assert state["stages"][1]["status"] == "gated"
        assert state["stages"][1]["gate"] is True
        assert state["stages"][1]["cycles"] == 1
        assert state["stages"][2]["status"] == "pending"
        assert state["stages"][3]["status"] == "pending"

    def test_trend_computed_from_metrics(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(10), resolved=3, total=10),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(20), resolved=5, total=10),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(30), resolved=8, total=10),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][0]["trend"] == "up"

    def test_elapsed_seconds_computed(self):
        events = [
            _make_event("stage.entered", "compile", ts=_ts(0)),
            _make_event("stage.cycle", "compile", ts=_ts(120)),
        ]
        state = _build_pipeline_state(events)
        assert state["stages"][2]["elapsed_seconds"] == pytest.approx(120.0)

    def test_recent_events_populated(self):
        events = [
            _make_event("stage.entered", "dep_resolve", ts=_ts(0)),
            _make_event("stage.cycle", "dep_resolve", ts=_ts(10), resolved=5, total=10),
            _make_event("stage.completed", "dep_resolve", ts=_ts(20)),
        ]
        state = _build_pipeline_state(events)
        assert len(state["recent_events"]) == 3
