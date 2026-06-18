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
        """detect_state returns REPO_INCOMPLETE when plan issues exist."""
        mock_result = type("R", (), {"returncode": 0, "stdout": '[{"number": 1}]'})()
        with patch("factory.state.subprocess.run", return_value=mock_result):
            assert detect_state(tmp_project) == ProjectState.REPO_INCOMPLETE

    def test_implementation_only_issues_not_repo_incomplete(self, tmp_project):
        """Regression (#378): an open 'implementation' issue must NOT flag an unbuilt repo.

        'implementation' is the factory's OWN backlog label, created on already-built
        repos. Only the external 'plan' label signals a genuinely unbuilt scaffold, so a
        repo with only 'implementation' issues open must resolve to NO_FACTORY, not
        REPO_INCOMPLETE.
        """
        def fake_run(args, **kwargs):
            label = args[args.index("--label") + 1] if "--label" in args else ""
            stdout = '[{"number": 1}]' if label == "implementation" else "[]"
            return type("R", (), {"returncode": 0, "stdout": stdout})()

        with patch("factory.state.subprocess.run", side_effect=fake_run):
            assert detect_state(tmp_project) == ProjectState.NO_FACTORY
