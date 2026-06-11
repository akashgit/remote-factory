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

    def test_unparseable_url(self, tmp_project: Path) -> None:
        subprocess.run(
            ["git", "remote", "add", "origin", "file:///local/path"],
            cwd=tmp_project, capture_output=True, check=True,
        )
        with pytest.raises(RuntimeError, match="Cannot parse git remote URL"):
            infer_remote(tmp_project)


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


# ── extract_issue_refs ──────────────────────────────────────


class TestExtractIssueRefs:
    def test_addresses_pattern(self) -> None:
        from factory.issue import extract_issue_refs
        assert extract_issue_refs("Addresses #42: fix the login bug") == [42]

    def test_addresses_without_colon(self) -> None:
        from factory.issue import extract_issue_refs
        assert extract_issue_refs("Addresses #42") == [42]

    def test_backlog_item_tag(self) -> None:
        from factory.issue import extract_issue_refs
        assert extract_issue_refs("**Backlog item:** #352 implement handoff") == [352]

    def test_issue_keyword(self) -> None:
        from factory.issue import extract_issue_refs
        assert extract_issue_refs("This is related to issue #99") == [99]

    def test_bare_single_ref(self) -> None:
        from factory.issue import extract_issue_refs
        assert extract_issue_refs("Fix the chain bug from #18") == [18]

    def test_bare_multiple_refs_ambiguous(self) -> None:
        from factory.issue import extract_issue_refs
        assert extract_issue_refs("Related to #18 and #42") == []

    def test_explicit_takes_priority_over_bare(self) -> None:
        from factory.issue import extract_issue_refs
        assert extract_issue_refs("Addresses #42, also see #99") == [42]

    def test_no_refs(self) -> None:
        from factory.issue import extract_issue_refs
        assert extract_issue_refs("Add structured logging to the API layer") == []

    def test_multiple_explicit_refs(self) -> None:
        from factory.issue import extract_issue_refs
        result = extract_issue_refs("Addresses #42, also addresses #99")
        assert result == [42, 99]

    def test_does_not_match_path_fragments(self) -> None:
        from factory.issue import extract_issue_refs
        assert extract_issue_refs("See file path/to/#42/config") == []


# ── resolve_reusable_issue ──────────────────────────────────


class TestResolveReusableIssue:
    def test_returns_number_for_open_relevant_issue(self, tmp_project: Path) -> None:
        from factory.issue import resolve_reusable_issue
        gh_response = json.dumps({
            "number": 42, "title": "Add widget support",
            "body": "Details.", "labels": [], "url": "",
        })
        state_response = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="OPEN\n", stderr="",
        )
        with (
            patch("factory.issue.infer_remote", return_value=("github", "org/repo")),
            patch("factory.issue.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                subprocess.CompletedProcess(args=[], returncode=0, stdout=gh_response, stderr=""),
                state_response,
            ]
            result = resolve_reusable_issue("Addresses #42: add widget support", tmp_project)
        assert result == 42

    def test_returns_none_for_closed_issue(self, tmp_project: Path) -> None:
        from factory.issue import resolve_reusable_issue
        gh_response = json.dumps({
            "number": 42, "title": "Add widgets",
            "body": "Done.", "labels": [], "url": "",
        })
        state_response = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="CLOSED\n", stderr="",
        )
        with (
            patch("factory.issue.infer_remote", return_value=("github", "org/repo")),
            patch("factory.issue.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                subprocess.CompletedProcess(args=[], returncode=0, stdout=gh_response, stderr=""),
                state_response,
            ]
            result = resolve_reusable_issue("Addresses #42: add widgets", tmp_project)
        assert result is None

    def test_returns_none_when_title_irrelevant(self, tmp_project: Path) -> None:
        from factory.issue import resolve_reusable_issue
        gh_response = json.dumps({
            "number": 42, "title": "Fix login page CSS",
            "body": "Details.", "labels": [], "url": "",
        })
        state_response = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="OPEN\n", stderr="",
        )
        with (
            patch("factory.issue.infer_remote", return_value=("github", "org/repo")),
            patch("factory.issue.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                subprocess.CompletedProcess(args=[], returncode=0, stdout=gh_response, stderr=""),
                state_response,
            ]
            result = resolve_reusable_issue("Add structured logging to API", tmp_project)
        assert result is None

    def test_returns_none_when_no_refs(self, tmp_project: Path) -> None:
        from factory.issue import resolve_reusable_issue
        result = resolve_reusable_issue("Add structured logging", tmp_project)
        assert result is None


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


# ── _build_ceo_task issue embedding ─────────────────────────


class TestBuildCeoTaskIssue:
    """Test that _build_ceo_task embeds issue metadata in the CEO task string."""

    def test_focus_with_issue_number(self) -> None:
        from factory.cli import _build_ceo_task

        task = _build_ceo_task(
            Path("/tmp/fake"), "improve",
            focus="Add widgets (issue #42)",
            issue_number=42,
        )
        assert "## Focus Directive (Targeted Mode)" in task
        assert "Target: Add widgets (issue #42)" in task
        assert "This target is from issue #42" in task
        assert "## Issue Tracking" in task
        assert "--issue 42" in task

    def test_focus_with_issue_number_and_url(self) -> None:
        from factory.cli import _build_ceo_task

        task = _build_ceo_task(
            Path("/tmp/fake"), "improve",
            focus="Fix bug (issue #99)",
            issue_number=99,
            issue_url="https://github.com/acme/repo/issues/99",
        )
        assert "#99 (https://github.com/acme/repo/issues/99)" in task
        assert "## Issue Tracking" in task

    def test_focus_without_issue(self) -> None:
        from factory.cli import _build_ceo_task

        task = _build_ceo_task(
            Path("/tmp/fake"), "improve",
            focus="eval reliability",
        )
        assert "## Focus Directive (Targeted Mode)" in task
        assert "Target: eval reliability" in task
        assert "## Issue Tracking" not in task
        assert "This target is from issue" not in task


# ── cmd_run --focus + --no-github ───────────────────────────


class TestCmdRunFocusNoGithub:
    """Test that cmd_run checks no_github before resolving issue refs."""

    def test_run_focus_no_github_with_issue_ref_fails(self) -> None:
        import sys
        from unittest.mock import patch as mock_patch

        with mock_patch.object(
            sys, "argv",
            ["factory", "run", "/tmp/fake", "--focus", "42", "--no-github"],
        ):
            from factory.cli import main

            code = main()
        assert code == 1
