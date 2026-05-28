"""Tests for factory.state — project state detection."""

import json
from unittest.mock import patch

from factory.models import ProjectState
from factory.state import _has_open_plan_issues, detect_state


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

    def test_evals_pending_review_without_config(self, tmp_project):
        """After discover: eval_profile.json exists but config.json does not."""
        factory_dir = tmp_project / ".factory"
        factory_dir.mkdir()
        (factory_dir / "eval_profile.json").write_text(json.dumps({
            "project_type": "bot",
            "dimensions": [],
            "tier": "discovered",
            "confidence": 0.8,
            "human_reviewed": False,
        }))
        assert detect_state(tmp_project) == ProjectState.EVALS_PENDING_REVIEW

    def test_evals_pending_review_with_config(self, tmp_project):
        """After init but before human review: both config.json and unreviewed eval_profile exist."""
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

    def test_malformed_eval_profile_json(self, tmp_project):
        """detect_state handles malformed eval_profile.json gracefully."""
        factory_dir = tmp_project / ".factory"
        factory_dir.mkdir()
        (factory_dir / "eval_profile.json").write_text("NOT VALID JSON {{{")
        # Should not raise — malformed JSON means human_reviewed check returns False
        state = detect_state(tmp_project)
        # Falls through to NO_FACTORY since config.json doesn't exist
        assert state == ProjectState.NO_FACTORY

    def test_eval_profile_missing_human_reviewed_key(self, tmp_project):
        """eval_profile.json without human_reviewed defaults to pending review."""
        factory_dir = tmp_project / ".factory"
        factory_dir.mkdir()
        (factory_dir / "eval_profile.json").write_text(json.dumps({
            "project_type": "bot",
            "dimensions": [],
            "tier": "discovered",
            "confidence": 0.8,
            # no human_reviewed key — .get defaults to False, treated as pending
        }))
        assert detect_state(tmp_project) == ProjectState.EVALS_PENDING_REVIEW


class TestHasOpenPlanIssues:
    def test_returns_false_when_gh_not_found(self, tmp_project):
        """_has_open_plan_issues returns False when gh CLI is not available."""
        with patch(
            "factory.state.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert _has_open_plan_issues(tmp_project) is False

    def test_returns_false_on_timeout(self, tmp_project):
        """_has_open_plan_issues returns False on subprocess timeout."""
        import subprocess as sp

        with patch(
            "factory.state.subprocess.run",
            side_effect=sp.TimeoutExpired("gh", 15),
        ):
            assert _has_open_plan_issues(tmp_project) is False

    def test_returns_false_on_empty_response(self, tmp_project):
        """_has_open_plan_issues returns False when gh returns empty list."""
        mock_result = type("R", (), {"returncode": 0, "stdout": "[]"})()
        with patch("factory.state.subprocess.run", return_value=mock_result):
            assert _has_open_plan_issues(tmp_project) is False

    def test_returns_true_on_open_issues(self, tmp_project):
        """_has_open_plan_issues returns True when gh returns issues."""
        mock_result = type("R", (), {"returncode": 0, "stdout": '[{"number": 1}]'})()
        with patch("factory.state.subprocess.run", return_value=mock_result):
            assert _has_open_plan_issues(tmp_project) is True

    def test_returns_false_on_nonzero_returncode(self, tmp_project):
        """_has_open_plan_issues returns False when gh returns non-zero."""
        mock_result = type("R", (), {"returncode": 1, "stdout": ""})()
        with patch("factory.state.subprocess.run", return_value=mock_result):
            assert _has_open_plan_issues(tmp_project) is False


class TestDetectStateWithIssues:
    def test_repo_incomplete_with_open_issues(self, tmp_project):
        """detect_state returns REPO_INCOMPLETE when plan issues exist on an unbuilt repo."""
        mock_result = type("R", (), {"returncode": 0, "stdout": '[{"number": 1}]'})()
        with patch("factory.state.subprocess.run", return_value=mock_result):
            assert detect_state(tmp_project) == ProjectState.REPO_INCOMPLETE

    def test_built_repo_not_incomplete_despite_open_issues(self, python_project):
        """A built repo (manifest + source) is NOT REPO_INCOMPLETE even with open issues.

        Regression test for #378: the open-issue heuristic keys off the factory's own
        'implementation' backlog label, so a mature repo must not be misclassified.
        """
        import subprocess as _sp

        _sp.run(["git", "init"], cwd=python_project, capture_output=True, check=True)
        # gh is never even called for a built repo — but mock it to prove the point.
        open_issues = type("R", (), {"returncode": 0, "stdout": '[{"number": 1}]'})()
        with patch("factory.state.subprocess.run", return_value=open_issues):
            assert detect_state(python_project) == ProjectState.NO_FACTORY

    def test_factory_md_repo_not_incomplete_despite_open_issues(self, tmp_project):
        """A repo with a committed factory.md is factory-managed → never REPO_INCOMPLETE."""
        (tmp_project / "factory.md").write_text("# Factory Configuration\n## Goal\nx\n")
        open_issues = type("R", (), {"returncode": 0, "stdout": '[{"number": 1}]'})()
        with patch("factory.state.subprocess.run", return_value=open_issues):
            assert detect_state(tmp_project) == ProjectState.NO_FACTORY


class TestIsBuiltProject:
    def test_bare_repo_not_built(self, tmp_project):
        from factory.state import _is_built_project

        assert _is_built_project(tmp_project) is False

    def test_factory_md_is_built(self, tmp_project):
        from factory.state import _is_built_project

        (tmp_project / "factory.md").write_text("# Factory Configuration\n")
        assert _is_built_project(tmp_project) is True

    def test_manifest_plus_source_is_built(self, python_project):
        from factory.state import _is_built_project

        assert _is_built_project(python_project) is True

    def test_manifest_without_source_not_built(self, tmp_project):
        """A manifest alone (empty source tree) is not enough — it's a scaffold."""
        from factory.state import _is_built_project

        (tmp_project / "pyproject.toml").write_text('[project]\nname = "x"\n')
        assert _is_built_project(tmp_project) is False
