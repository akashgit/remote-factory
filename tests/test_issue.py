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
    is_issue_ref,
    parse_issue_ref,
)


# ── is_issue_ref ────────────────────────────────────────────


class TestIsIssueRef:
    def test_bare_number(self) -> None:
        assert is_issue_ref("42") is True

    def test_github_url(self) -> None:
        assert is_issue_ref("https://github.com/owner/repo/issues/99") is True

    def test_gitlab_url(self) -> None:
        assert is_issue_ref("https://gitlab.com/team/repo/-/issues/7") is True

    def test_shorthand(self) -> None:
        assert is_issue_ref("owner/repo#42") is True

    def test_plain_text(self) -> None:
        assert is_issue_ref("dashboard UI") is False

    def test_plain_text_with_slash(self) -> None:
        assert is_issue_ref("eval/reliability") is False

    def test_whitespace_stripped(self) -> None:
        assert is_issue_ref("  42  ") is True

    def test_nested_gitlab_group(self) -> None:
        assert is_issue_ref("https://gitlab.com/g/s/p/-/issues/3") is True


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

    def test_gitlab_nested_groups(self, tmp_project: Path) -> None:
        url = "https://gitlab.com/group/subgroup/project/-/issues/12"
        forge, owner_repo, number = parse_issue_ref(url, tmp_project)
        assert forge == "gitlab"
        assert owner_repo == "group/subgroup/project"
        assert number == 12

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


# ── CLI focus-as-issue integration ─────────────────────────


class TestFocusIssueIntegration:
    """Test that --focus with issue refs works correctly via _resolve_focus_issue."""

    def test_focus_plain_text_not_resolved(self) -> None:
        from factory.cli import _resolve_focus_issue
        result = _resolve_focus_issue("dashboard UI", Path("/tmp/fake"))
        assert result is None

    def test_focus_bare_number_resolved(self) -> None:
        from factory.cli import _resolve_focus_issue

        gh_response = json.dumps({
            "number": 42,
            "title": "Add widgets",
            "body": "Details.",
            "labels": [],
            "url": "https://github.com/org/repo/issues/42",
        })
        with (
            patch("factory.issue.infer_remote", return_value=("github", "org/repo")),
            patch("factory.issue.subprocess.run") as mock_run,
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=gh_response, stderr="",
            )
            result = _resolve_focus_issue("42", Path("/tmp/fake"))

        assert result is not None
        title, context, number, url = result
        assert number == 42
        assert title == "Add widgets"
        assert "Add widgets" in context

    def test_focus_no_github_checked_by_caller(self) -> None:
        """no_github is the caller's responsibility — _resolve_focus_issue doesn't check it."""
        import sys
        from unittest.mock import patch as mock_patch

        with mock_patch.object(sys, "argv", ["factory", "ceo", "/tmp/fake", "--focus", "42", "--no-github"]):
            from factory.cli import main
            code = main()
        assert code == 1

    def test_focus_url_resolved(self) -> None:
        from factory.cli import _resolve_focus_issue

        gh_response = json.dumps({
            "number": 99,
            "title": "Fix bug",
            "body": "Broken.",
            "labels": [{"name": "bug"}],
            "url": "https://github.com/acme/repo/issues/99",
        })
        with (
            patch("factory.issue.subprocess.run") as mock_run,
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=gh_response, stderr="",
            )
            result = _resolve_focus_issue(
                "https://github.com/acme/repo/issues/99",
                Path("/tmp/fake"),
            )

        assert result is not None
        title, context, number, url = result
        assert number == 99
        assert title == "Fix bug"
        assert "Fix bug" in context

    def test_focus_updates_name_with_issue_title(self) -> None:
        """When --focus resolves to an issue, the focus name should include the issue title."""
        from factory.cli import _resolve_focus_issue

        gh_response = json.dumps({
            "number": 42,
            "title": "Add widgets",
            "body": "Details.",
            "labels": [],
            "url": "https://github.com/org/repo/issues/42",
        })
        with (
            patch("factory.issue.infer_remote", return_value=("github", "org/repo")),
            patch("factory.issue.subprocess.run") as mock_run,
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=gh_response, stderr="",
            )
            result = _resolve_focus_issue("42", Path("/tmp/fake"))

        assert result is not None
        title, _context, number, _url = result
        focus = f"{title} (issue #{number})"
        assert focus == "Add widgets (issue #42)"
