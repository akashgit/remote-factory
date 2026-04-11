"""Tests for factory.state — project state detection."""

import json


from factory.models import ProjectState
from factory.state import detect_state


class TestDetectState:
    def test_no_repo_when_path_missing(self, tmp_path):
        assert detect_state(tmp_path / "nonexistent") == ProjectState.NO_REPO

    def test_no_repo_when_no_git(self, tmp_path):
        project = tmp_path / "no-git"
        project.mkdir()
        assert detect_state(project) == ProjectState.NO_REPO

    def test_no_factory_with_git(self, tmp_project):
        assert detect_state(tmp_project) == ProjectState.NO_FACTORY

    def test_has_factory_with_config(self, tmp_project):
        factory_dir = tmp_project / ".factory"
        factory_dir.mkdir()
        (factory_dir / "config.json").write_text('{"goal":"x","scope":[],"guards":[],"eval_command":"x","eval_threshold":0.8,"constraints":[]}')
        assert detect_state(tmp_project) == ProjectState.HAS_FACTORY

    def test_evals_pending_review(self, tmp_project):
        factory_dir = tmp_project / ".factory"
        factory_dir.mkdir()
        (factory_dir / "config.json").write_text('{"goal":"x","scope":[],"guards":[],"eval_command":"x","eval_threshold":0.8,"constraints":[]}')
        (factory_dir / "eval_profile.json").write_text(json.dumps({
            "project_type": "bot",
            "dimensions": [],
            "tier": "discovered",
            "confidence": 0.8,
            "human_reviewed": False,
        }))
        assert detect_state(tmp_project) == ProjectState.EVALS_PENDING_REVIEW

    def test_has_factory_when_reviewed(self, tmp_project):
        factory_dir = tmp_project / ".factory"
        factory_dir.mkdir()
        (factory_dir / "config.json").write_text('{"goal":"x","scope":[],"guards":[],"eval_command":"x","eval_threshold":0.8,"constraints":[]}')
        (factory_dir / "eval_profile.json").write_text(json.dumps({
            "project_type": "bot",
            "dimensions": [],
            "tier": "discovered",
            "confidence": 0.8,
            "human_reviewed": True,
        }))
        assert detect_state(tmp_project) == ProjectState.HAS_FACTORY
