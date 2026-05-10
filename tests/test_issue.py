"""Tests for factory/issue.py — issue parsing, fetching, and formatting."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.issue import (
    IssueSpec,
    fetch_issue,
    format_issue_as_spec,
    infer_remote,
    parse_issue_ref,
)


# ── parse_issue_ref ──────────────────────────────────────────


class TestParseIssueRef:
    def test_bare_number(self, tmp_project: Path) -> None:
        with patch("factory.issue.infer_remote", return_value=("github", "owner/repo")):
            forge, owner_repo, number = parse_issue_ref("42", tmp_project)
        assert forge == "github"
        assert owner_repo == "owner/repo"
        assert number == 42

    def test_github_url(self, tmp_project: Path) -> None:
        url = "https://github.com/acme/widgets/issues/99"
        forge, owner_repo, number = parse_issue_ref(url, tmp_project)
        assert forge == "github"
        assert owner_repo == "acme/widgets"
        assert number == 99

    def test_gitlab_url(self, tmp_project: Path) -> None:
        url = "https://gitlab.com/acme/widgets/-/issues/7"
        forge, owner_repo, number = parse_issue_ref(url, tmp_project)
        assert forge == "gitlab"
        assert owner_repo == "acme/widgets"
        assert number == 7

    def test_github_shorthand(self, tmp_project: Path) -> None:
        forge, owner_repo, number = parse_issue_ref("owner/repo#123", tmp_project)
        assert forge == "github"
        assert owner_repo == "owner/repo"
        assert number == 123

    def test_github_url_without_trailing_slash(self, tmp_project: Path) -> None:
        url = "https://github.com/org/project/issues/1"
        forge, owner_repo, number = parse_issue_ref(url, tmp_project)
        assert forge == "github"
        assert owner_repo == "org/project"
        assert number == 1

    def test_gitlab_self_hosted(self, tmp_project: Path) -> None:
        url = "https://gitlab.ibm.com/team/repo/-/issues/55"
        forge, owner_repo, number = parse_issue_ref(url, tmp_project)
        assert forge == "gitlab"
        assert owner_repo == "team/repo"
        assert number == 55

    def test_invalid_ref(self, tmp_project: Path) -> None:
        with pytest.raises(ValueError, match="Cannot parse issue reference"):
            parse_issue_ref("not-a-ref", tmp_project)

    def test_whitespace_stripped(self, tmp_project: Path) -> None:
        url = "  https://github.com/a/b/issues/3  "
        forge, owner_repo, number = parse_issue_ref(url, tmp_project)
        assert number == 3


# ── infer_remote ─────────────────────────────────────────────


class TestInferRemote:
    def test_https_github(self, tmp_project: Path) -> None:
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/owner/repo.git"],
            cwd=tmp_project, capture_output=True, check=True,
        )
        forge, owner_repo = infer_remote(tmp_project)
        assert forge == "github"
        assert owner_repo == "owner/repo"

    def test_ssh_github(self, tmp_project: Path) -> None:
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:owner/repo.git"],
            cwd=tmp_project, capture_output=True, check=True,
        )
        forge, owner_repo = infer_remote(tmp_project)
        assert forge == "github"
        assert owner_repo == "owner/repo"

    def test_https_gitlab(self, tmp_project: Path) -> None:
        subprocess.run(
            ["git", "remote", "add", "origin", "https://gitlab.com/team/project.git"],
            cwd=tmp_project, capture_output=True, check=True,
        )
        forge, owner_repo = infer_remote(tmp_project)
        assert forge == "gitlab"
        assert owner_repo == "team/project"

    def test_ssh_gitlab(self, tmp_project: Path) -> None:
        subprocess.run(
            ["git", "remote", "add", "origin", "git@gitlab.com:team/project.git"],
            cwd=tmp_project, capture_output=True, check=True,
        )
        forge, owner_repo = infer_remote(tmp_project)
        assert forge == "gitlab"
        assert owner_repo == "team/project"

    def test_no_remote(self, tmp_project: Path) -> None:
        with pytest.raises(RuntimeError, match="Cannot infer remote"):
            infer_remote(tmp_project)

    def test_https_without_dot_git(self, tmp_project: Path) -> None:
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/owner/repo"],
            cwd=tmp_project, capture_output=True, check=True,
        )
        forge, owner_repo = infer_remote(tmp_project)
        assert forge == "github"
        assert owner_repo == "owner/repo"


# ── format_issue_as_spec ─────────────────────────────────────


class TestFormatIssueAsSpec:
    def test_basic(self) -> None:
        spec = IssueSpec(
            number=42,
            title="Add widget support",
            body="We need widgets.\n\nDetails here.",
            labels=["enhancement", "v2"],
            url="https://github.com/org/repo/issues/42",
            forge="github",
        )
        result = format_issue_as_spec(spec)
        assert result.startswith("# Add widget support\n")
        assert "Issue: https://github.com/org/repo/issues/42" in result
        assert "Labels: enhancement, v2" in result
        assert "We need widgets." in result

    def test_no_labels(self) -> None:
        spec = IssueSpec(number=1, title="Bug", body="Fix it.", forge="github")
        result = format_issue_as_spec(spec)
        assert "Labels:" not in result
        assert "# Bug\n" in result
        assert "Fix it." in result

    def test_no_url(self) -> None:
        spec = IssueSpec(number=1, title="Bug", body="Fix it.", forge="github")
        result = format_issue_as_spec(spec)
        assert "Issue:" not in result


# ── fetch_issue ──────────────────────────────────────────────


class TestFetchIssue:
    def test_github(self, tmp_project: Path) -> None:
        gh_response = json.dumps({
            "number": 42,
            "title": "Add widgets",
            "body": "We need widgets.",
            "labels": [{"name": "enhancement"}],
            "url": "https://github.com/org/repo/issues/42",
        })
        with patch("factory.issue.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=gh_response, stderr="",
            )
            spec = fetch_issue("https://github.com/org/repo/issues/42", tmp_project)

        assert spec.number == 42
        assert spec.title == "Add widgets"
        assert spec.body == "We need widgets."
        assert spec.labels == ["enhancement"]
        assert spec.forge == "github"
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[:3] == ["gh", "issue", "view"]

    def test_gitlab(self, tmp_project: Path) -> None:
        gl_response = json.dumps({
            "iid": 7,
            "title": "Fix login",
            "description": "Login is broken.",
            "labels": ["bug"],
            "web_url": "https://gitlab.com/team/repo/-/issues/7",
        })
        with patch("factory.issue.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=gl_response, stderr="",
            )
            spec = fetch_issue("https://gitlab.com/team/repo/-/issues/7", tmp_project)

        assert spec.number == 7
        assert spec.title == "Fix login"
        assert spec.body == "Login is broken."
        assert spec.labels == ["bug"]
        assert spec.forge == "gitlab"
        call_args = mock_run.call_args[0][0]
        assert call_args[:3] == ["glab", "issue", "view"]

    def test_not_found(self, tmp_project: Path) -> None:
        with patch("factory.issue.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                1, "gh", stderr="issue not found",
            )
            with pytest.raises(RuntimeError, match="Failed to fetch"):
                fetch_issue("https://github.com/org/repo/issues/999", tmp_project)

    def test_cli_not_installed(self, tmp_project: Path) -> None:
        with patch("factory.issue.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with pytest.raises(RuntimeError, match="CLI not found"):
                fetch_issue("https://github.com/org/repo/issues/1", tmp_project)


# ── CLI mutual exclusion ────────────────────────────────────


class TestCLIMutualExclusion:
    """Test that --issue is mutually exclusive with --prompt, --focus, --no-github."""

    def _parse_args(self, *argv: str) -> int:
        """Run main() with given argv and return exit code."""
        import sys
        from unittest.mock import patch as mock_patch

        with mock_patch.object(sys, "argv", ["factory", *argv]):
            from factory.cli import main
            return main()

    def test_issue_prompt_mutual_exclusion_ceo(self) -> None:
        code = self._parse_args("ceo", "/tmp/fake", "--issue", "42", "--prompt", "foo.md")
        assert code == 1

    def test_issue_focus_mutual_exclusion_ceo(self) -> None:
        code = self._parse_args("ceo", "/tmp/fake", "--issue", "42", "--focus", "bar")
        assert code == 1

    def test_issue_interactive_mutual_exclusion_ceo(self) -> None:
        code = self._parse_args(
            "ceo", "some idea", "--issue", "42", "--mode", "interactive",
        )
        assert code == 1

    def test_issue_research_mutual_exclusion_ceo(self) -> None:
        code = self._parse_args(
            "ceo", "some idea", "--issue", "42", "--mode", "research",
        )
        assert code == 1
