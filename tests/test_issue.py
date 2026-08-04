"""Tests for factory/issue.py — issue parsing, fetching, and formatting."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.issue import (
    IssueSpec,
    fetch_issue,
    format_issue_as_spec,
    has_multi_issue_refs,
    infer_remote,
    is_issue_ref,
    parse_issue_ref,
    parse_multi_issue_refs,
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


class TestFocusIssueIntegration:
    """Test that --focus with issue refs works correctly via _resolve_focus_issue."""

    def test_focus_plain_text_not_resolved(self) -> None:
        from factory.cli._path_resolver import _resolve_focus_issue
        result = _resolve_focus_issue("dashboard UI", Path("/tmp/fake"))
        assert result is None

    def test_focus_bare_number_resolved(self) -> None:
        from factory.cli._path_resolver import _resolve_focus_issue

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
        from factory.cli._path_resolver import _resolve_focus_issue

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
        from factory.cli._path_resolver import _resolve_focus_issue

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
        from factory.cli._task_builder import _build_ceo_task

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
        from factory.cli._task_builder import _build_ceo_task

        task = _build_ceo_task(
            Path("/tmp/fake"), "improve",
            focus="Fix bug (issue #99)",
            issue_number=99,
            issue_url="https://github.com/acme/repo/issues/99",
        )
        assert "#99 (https://github.com/acme/repo/issues/99)" in task
        assert "## Issue Tracking" in task

    def test_focus_without_issue(self) -> None:
        from factory.cli._task_builder import _build_ceo_task

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


# ── parse_multi_issue_refs ───────────────────────────────────


class TestParseMultiIssueRefs:
    def test_and_separator(self) -> None:
        assert parse_multi_issue_refs("111 and 112") == ["111", "112"]

    def test_issue_keyword_and(self) -> None:
        assert parse_multi_issue_refs("issue 111 and issue 112") == ["111", "112"]

    def test_hash_prefix(self) -> None:
        assert parse_multi_issue_refs("#111 #112") == ["111", "112"]

    def test_comma_no_space(self) -> None:
        assert parse_multi_issue_refs("111,112") == ["111", "112"]

    def test_comma_with_space(self) -> None:
        assert parse_multi_issue_refs("111, 112") == ["111", "112"]

    def test_space_separated(self) -> None:
        assert parse_multi_issue_refs("111 112") == ["111", "112"]

    def test_single_ref(self) -> None:
        assert parse_multi_issue_refs("42") == ["42"]

    def test_plain_text_returns_empty(self) -> None:
        assert parse_multi_issue_refs("dashboard UI") == []

    def test_owner_repo_shorthand_pair(self) -> None:
        result = parse_multi_issue_refs("owner/repo#111 owner/repo#112")
        assert result == ["owner/repo#111", "owner/repo#112"]

    def test_mixed_bare_and_url(self) -> None:
        result = parse_multi_issue_refs("111 and https://github.com/o/r/issues/112")
        assert result == ["111", "https://github.com/o/r/issues/112"]

    def test_empty_string(self) -> None:
        assert parse_multi_issue_refs("") == []

    def test_whitespace_only(self) -> None:
        assert parse_multi_issue_refs("   ") == []

    def test_freeform_with_number(self) -> None:
        assert parse_multi_issue_refs("fix issue 42 in the dashboard") == []

    def test_three_issues(self) -> None:
        assert parse_multi_issue_refs("1, 2, 3") == ["1", "2", "3"]

    def test_hash_prefix_single(self) -> None:
        assert parse_multi_issue_refs("#42") == ["42"]

    def test_issue_keyword_single(self) -> None:
        assert parse_multi_issue_refs("issue 42") == ["42"]


# ── has_multi_issue_refs ─────────────────────────────────────


class TestHasMultiIssueRefs:
    def test_true_for_multi(self) -> None:
        assert has_multi_issue_refs("111 and 112") is True

    def test_true_for_single(self) -> None:
        assert has_multi_issue_refs("42") is True

    def test_false_for_plain_text(self) -> None:
        assert has_multi_issue_refs("dashboard UI") is False

    def test_false_for_empty(self) -> None:
        assert has_multi_issue_refs("") is False


# ── _resolve_focus_issues integration ────────────────────────


class TestResolveFocusIssues:
    """Test that _resolve_focus_issues fetches multiple issues and writes combined spec."""

    def test_single_issue(self) -> None:
        from factory.cli._path_resolver import _resolve_focus_issues

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
            result = _resolve_focus_issues("42", Path("/tmp/fake"))

        assert result is not None
        assert len(result) == 1
        assert result[0][2] == 42

    def test_multi_issues(self) -> None:
        from factory.cli._path_resolver import _resolve_focus_issues

        responses = [
            json.dumps({
                "number": 111,
                "title": "First issue",
                "body": "Body 1.",
                "labels": [],
                "url": "https://github.com/org/repo/issues/111",
            }),
            json.dumps({
                "number": 112,
                "title": "Second issue",
                "body": "Body 2.",
                "labels": [],
                "url": "https://github.com/org/repo/issues/112",
            }),
        ]
        call_count = 0

        def fake_run(*a: object, **kw: object) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=resp, stderr="")

        with (
            patch("factory.issue.infer_remote", return_value=("github", "org/repo")),
            patch("factory.issue.subprocess.run", side_effect=fake_run),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text") as mock_write,
        ):
            result = _resolve_focus_issues("111 and 112", Path("/tmp/fake"))

        assert result is not None
        assert len(result) == 2
        assert result[0][2] == 111
        assert result[1][2] == 112
        written = mock_write.call_args[0][0]
        assert "First issue" in written
        assert "Second issue" in written
        assert "---" in written

    def test_plain_text_returns_none(self) -> None:
        from factory.cli._path_resolver import _resolve_focus_issues

        result = _resolve_focus_issues("dashboard UI", Path("/tmp/fake"))
        assert result is None


# ── _build_ceo_task multi-issue ──────────────────────────────


class TestBuildCeoTaskMultiIssue:
    """Test that _build_ceo_task embeds multi-issue metadata correctly."""

    def test_multi_issue_focus_directive(self) -> None:
        from factory.cli._task_builder import _build_ceo_task

        task = _build_ceo_task(
            Path("/tmp/fake"), "improve",
            focus="First (issue #111) + Second (issue #112)",
            issue_numbers=[111, 112],
            issue_urls=[
                "https://github.com/org/repo/issues/111",
                "https://github.com/org/repo/issues/112",
            ],
        )
        assert "## Focus Directive (Targeted Mode)" in task
        assert "These targets are from issues" in task
        assert "#111" in task
        assert "#112" in task
        assert "## Issue Tracking" in task
        assert "--issue 111" in task
        assert "--issue 112" in task

    def test_single_issue_still_works(self) -> None:
        from factory.cli._task_builder import _build_ceo_task

        task = _build_ceo_task(
            Path("/tmp/fake"), "improve",
            focus="Add widgets (issue #42)",
            issue_number=42,
            issue_url="https://github.com/org/repo/issues/42",
        )
        assert "This target is from issue #42" in task
        assert "## Issue Tracking" in task
        assert "--issue 42" in task

    def test_empty_issue_numbers_uses_single(self) -> None:
        from factory.cli._task_builder import _build_ceo_task

        task = _build_ceo_task(
            Path("/tmp/fake"), "improve",
            focus="Add widgets (issue #42)",
            issue_number=42,
            issue_numbers=[],
            issue_urls=[],
        )
        assert "This target is from issue #42" in task

    def test_multi_issue_numbers_without_urls(self) -> None:
        from factory.cli._task_builder import _build_ceo_task

        task = _build_ceo_task(
            Path("/tmp/fake"), "improve",
            focus="First (issue #10) + Second (issue #20)",
            issue_numbers=[10, 20],
            issue_urls=[],
        )
        assert "These targets are from issues" in task
        assert "#10" in task
        assert "#20" in task
        assert "## Issue Tracking" in task
        assert "--issue 10" in task
        assert "--issue 20" in task
        assert "https://" not in task.split("These targets")[1].split("All issue")[0]

    def test_multi_issue_numbers_with_partial_urls(self) -> None:
        from factory.cli._task_builder import _build_ceo_task

        task = _build_ceo_task(
            Path("/tmp/fake"), "improve",
            focus="First (issue #10) + Second (issue #20)",
            issue_numbers=[10, 20],
            issue_urls=["https://github.com/o/r/issues/10"],
        )
        assert "#10 (https://github.com/o/r/issues/10)" in task
        assert "#20" in task


# ── parse_multi_issue_refs — slash / http branches ──────────


class TestParseMultiIssueRefsSlashAndHttp:
    """Cover the '/' token accumulator and 'http' prefix branches."""

    def test_url_only(self) -> None:
        result = parse_multi_issue_refs("https://github.com/o/r/issues/42")
        assert result == ["https://github.com/o/r/issues/42"]

    def test_two_urls(self) -> None:
        result = parse_multi_issue_refs(
            "https://github.com/o/r/issues/1, https://github.com/o/r/issues/2"
        )
        assert result == [
            "https://github.com/o/r/issues/1",
            "https://github.com/o/r/issues/2",
        ]

    def test_slash_token_not_issue_ref_returns_empty(self) -> None:
        """A bare 'some/path' that never combines into a valid ref → empty list."""
        result = parse_multi_issue_refs("some/path")
        assert result == []

    def test_slash_token_with_trailing_noise_returns_empty(self) -> None:
        """'org/repo stuff' — slash token accumulates but never forms a valid ref."""
        result = parse_multi_issue_refs("org/repo stuff")
        assert result == []

    def test_owner_repo_hash_single(self) -> None:
        """owner/repo#42 — has both / and # so skips the slash branch."""
        result = parse_multi_issue_refs("owner/repo#42")
        assert result == ["owner/repo#42"]

    def test_slash_token_accumulates_into_shorthand(self) -> None:
        """'owner/repo#42 owner/repo#43' — each has / and # so uses the shorthand path."""
        result = parse_multi_issue_refs("owner/repo#42 owner/repo#43")
        assert result == ["owner/repo#42", "owner/repo#43"]

    def test_only_noise_words_returns_empty(self) -> None:
        """Input with only noise words should return empty list."""
        result = parse_multi_issue_refs("issue and issues")
        assert result == []


# ── cmd_ceo multi-issue path ────────────────────────────────


class TestCmdCeoMultiIssue:
    """Cover the multi-issue branch in cmd_ceo (lines 75-82)."""

    def test_cmd_ceo_multi_focus_assembles_correctly(self) -> None:
        """When _resolve_focus_issues returns 2+ items, cmd_ceo joins them."""
        ns = argparse.Namespace(
            path="/tmp/fake",
            profile=None,
            mode="improve",
            headless=False,
            bg=False,
            bg_agents=False,
            prompt=None,
            focus="111 and 112",
            dir=None,
            refine=None,
            no_github=False,
            use_profile=False,
            model=None,
            tmux_persist=False,
            background=False,
            clean_pr=None,
            run_id=None,
            no_worktree=False,
            overwrite=None,
        )

        multi_result = [
            ("First issue", "ctx1", 111, "https://github.com/o/r/issues/111"),
            ("Second issue", "ctx2", 112, "https://github.com/o/r/issues/112"),
        ]

        with (
            patch("factory.user_config.load_config"),
            patch("factory.cli.ceo._validate_ceo_flags") as mock_validate,
            patch("factory.cli.ceo._resolve_ceo_project") as mock_resolve,
            patch("factory.cli.ceo._resolve_focus_issues", return_value=multi_result),
            patch("factory.cli.ceo._validate_late_flags", return_value=None),
            patch("factory.cli.ceo._execute_ceo", return_value=0) as mock_exec,
        ):
            mock_validate.return_value = (
                "improve", False, False, False, None, "111 and 112", None, None,
            )
            mock_resolve.return_value = (
                Path("/tmp/fake"), None, None, None,
                None, False, False, None, None,
            )
            from factory.cli.ceo import cmd_ceo
            code = cmd_ceo(ns)

        assert code == 0
        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["issue_numbers"] == [111, 112]
        assert call_kwargs["issue_urls"] == [
            "https://github.com/o/r/issues/111",
            "https://github.com/o/r/issues/112",
        ]
        assert "First issue (issue #111)" in call_kwargs["focus"]
        assert "Second issue (issue #112)" in call_kwargs["focus"]

    def test_cmd_ceo_single_focus_assembles_correctly(self) -> None:
        """When _resolve_focus_issues returns exactly 1 item, cmd_ceo uses single-issue path."""
        ns = argparse.Namespace(
            path="/tmp/fake",
            profile=None,
            mode="improve",
            headless=False,
            bg=False,
            bg_agents=False,
            prompt=None,
            focus="42",
            dir=None,
            refine=None,
            no_github=False,
            use_profile=False,
            model=None,
            tmux_persist=False,
            background=False,
            clean_pr=None,
            run_id=None,
            no_worktree=False,
            overwrite=None,
        )

        single_result = [
            ("Add widgets", "ctx", 42, "https://github.com/o/r/issues/42"),
        ]

        with (
            patch("factory.user_config.load_config"),
            patch("factory.cli.ceo._validate_ceo_flags") as mock_validate,
            patch("factory.cli.ceo._resolve_ceo_project") as mock_resolve,
            patch("factory.cli.ceo._resolve_focus_issues", return_value=single_result),
            patch("factory.cli.ceo._validate_late_flags", return_value=None),
            patch("factory.cli.ceo._execute_ceo", return_value=0) as mock_exec,
        ):
            mock_validate.return_value = (
                "improve", False, False, False, None, "42", None, None,
            )
            mock_resolve.return_value = (
                Path("/tmp/fake"), None, None, None,
                None, False, False, None, None,
            )
            from factory.cli.ceo import cmd_ceo
            code = cmd_ceo(ns)

        assert code == 0
        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["issue_number"] == 42
        assert call_kwargs["issue_url"] == "https://github.com/o/r/issues/42"
        assert "Add widgets (issue #42)" == call_kwargs["focus"]

    def test_cmd_ceo_multi_focus_no_github_fails(self) -> None:
        ns = argparse.Namespace(
            path="/tmp/fake",
            profile=None,
            mode="improve",
            headless=False,
            bg=False,
            bg_agents=False,
            prompt=None,
            focus="111 and 112",
            dir=None,
            refine=None,
            no_github=True,
            use_profile=False,
            model=None,
        )
        with (
            patch("factory.user_config.load_config"),
            patch("factory.cli.ceo._validate_ceo_flags") as mock_validate,
            patch("factory.cli.ceo._resolve_ceo_project") as mock_resolve,
        ):
            mock_validate.return_value = (
                "improve", False, False, False, None, "111 and 112", None, None,
            )
            mock_resolve.return_value = (
                Path("/tmp/fake"), None, None, None,
                None, False, False, None, None,
            )
            from factory.cli.ceo import cmd_ceo
            code = cmd_ceo(ns)

        assert code == 1


# ── cmd_run multi-issue path ────────────────────────────────


class TestCmdRunMultiIssue:
    """Cover the multi-issue branch in cmd_run (lines 394-406)."""

    def test_cmd_run_multi_focus_assembles_correctly(self) -> None:
        ns = argparse.Namespace(
            path="/tmp/fake",
            profile=None,
            mode="improve",
            loop=False,
            focus="111 and 112",
            discover_only=False,
            no_github=False,
            min_growth=None,
            max_new=None,
            branch=None,
            run_id=None,
            model=None,
            use_profile=False,
            tmux_persist=False,
            background=False,
            bg_agents=False,
            prompt=None,
            clean_pr=None,
            no_worktree=False,
            overwrite=None,
        )

        multi_result = [
            ("First", "ctx1", 111, "https://github.com/o/r/issues/111"),
            ("Second", "ctx2", 112, "https://github.com/o/r/issues/112"),
        ]

        with (
            patch("factory.user_config.load_config"),
            patch("factory.cli.run._resolve_input", return_value=(Path("/tmp/fake"), None)),
            patch("factory.cli.run._resolve_model", return_value=None),
            patch("factory.cli.run._resolve_tmux_persist", return_value=False),
            patch("factory.cli.run._resolve_background", return_value=False),
            patch("factory.cli.run._resolve_bg_agents", return_value=False),
            patch("factory.cli.run._resolve_focus_issues", return_value=multi_result),
            patch("factory.cli.run.warn_deprecated_mode"),
            patch("factory.cli.run._print_banner"),
            patch("factory.cli.run._ensure_dashboard"),
            patch("factory.cli.run._run_single_cycle", return_value=0) as mock_cycle,
            patch("factory.cli.run._chain_modes", return_value=0),
            patch("factory.worktree.prune_stale", return_value=[]),
            patch("pathlib.Path.is_dir", return_value=True),
        ):
            from factory.cli.run import cmd_run
            code = cmd_run(ns)

        assert code == 0
        call_kwargs = mock_cycle.call_args[1]
        assert call_kwargs["issue_numbers"] == [111, 112]
        assert call_kwargs["issue_urls"] == [
            "https://github.com/o/r/issues/111",
            "https://github.com/o/r/issues/112",
        ]
        assert "First (issue #111)" in call_kwargs["focus"]
        assert "Second (issue #112)" in call_kwargs["focus"]

    def test_cmd_run_single_focus_assembles_correctly(self) -> None:
        ns = argparse.Namespace(
            path="/tmp/fake",
            profile=None,
            mode="improve",
            loop=False,
            focus="42",
            discover_only=False,
            no_github=False,
            min_growth=None,
            max_new=None,
            branch=None,
            run_id=None,
            model=None,
            use_profile=False,
            tmux_persist=False,
            background=False,
            bg_agents=False,
            prompt=None,
            clean_pr=None,
            no_worktree=False,
            overwrite=None,
        )

        single_result = [
            ("Add widgets", "ctx", 42, "https://github.com/o/r/issues/42"),
        ]

        with (
            patch("factory.user_config.load_config"),
            patch("factory.cli.run._resolve_input", return_value=(Path("/tmp/fake"), None)),
            patch("factory.cli.run._resolve_model", return_value=None),
            patch("factory.cli.run._resolve_tmux_persist", return_value=False),
            patch("factory.cli.run._resolve_background", return_value=False),
            patch("factory.cli.run._resolve_bg_agents", return_value=False),
            patch("factory.cli.run._resolve_focus_issues", return_value=single_result),
            patch("factory.cli.run.warn_deprecated_mode"),
            patch("factory.cli.run._print_banner"),
            patch("factory.cli.run._ensure_dashboard"),
            patch("factory.cli.run._run_single_cycle", return_value=0) as mock_cycle,
            patch("factory.cli.run._chain_modes", return_value=0),
            patch("factory.worktree.prune_stale", return_value=[]),
            patch("pathlib.Path.is_dir", return_value=True),
        ):
            from factory.cli.run import cmd_run
            code = cmd_run(ns)

        assert code == 0
        call_kwargs = mock_cycle.call_args[1]
        assert call_kwargs["issue_number"] == 42
        assert call_kwargs["issue_url"] == "https://github.com/o/r/issues/42"
        assert "Add widgets (issue #42)" == call_kwargs["focus"]

    def test_cmd_run_multi_focus_no_github_fails(self) -> None:
        ns = argparse.Namespace(
            path="/tmp/fake",
            profile=None,
            mode="improve",
            loop=False,
            focus="111 and 112",
            discover_only=False,
            no_github=True,
            min_growth=None,
            max_new=None,
            branch=None,
            run_id=None,
            model=None,
            use_profile=False,
            tmux_persist=False,
            background=False,
            bg_agents=False,
            prompt=None,
            clean_pr=None,
            no_worktree=False,
            overwrite=None,
        )
        with (
            patch("factory.user_config.load_config"),
            patch("factory.cli.run._resolve_input", return_value=(Path("/tmp/fake"), None)),
            patch("factory.cli.run._resolve_model", return_value=None),
            patch("factory.cli.run._resolve_tmux_persist", return_value=False),
            patch("factory.cli.run._resolve_background", return_value=False),
            patch("factory.cli.run._resolve_bg_agents", return_value=False),
        ):
            from factory.cli.run import cmd_run
            code = cmd_run(ns)

        assert code == 1
