"""Tests for factory/forge.py — ForgeOps abstraction over gh/glab CLIs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from factory.forge import ForgeOps


# ── Construction & detection ──────────────────────────────────


class TestForgeOpsInit:
    def test_explicit_github(self, tmp_path: Path) -> None:
        ops = ForgeOps(tmp_path, forge="github", repo="owner/repo")
        assert ops.forge == "github"
        assert ops.repo == "owner/repo"
        assert ops._cli == "gh"

    def test_explicit_gitlab(self, tmp_path: Path) -> None:
        ops = ForgeOps(tmp_path, forge="gitlab", repo="team/project")
        assert ops.forge == "gitlab"
        assert ops.repo == "team/project"
        assert ops._cli == "glab"

    def test_infer_from_remote(self, tmp_project: Path) -> None:
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/owner/repo.git"],
            cwd=tmp_project, capture_output=True, check=True,
        )
        ops = ForgeOps(tmp_project)
        assert ops.forge == "github"
        assert ops.repo == "owner/repo"

    def test_infer_gitlab_from_remote(self, tmp_project: Path) -> None:
        subprocess.run(
            ["git", "remote", "add", "origin", "git@gitlab.com:team/project.git"],
            cwd=tmp_project, capture_output=True, check=True,
        )
        ops = ForgeOps(tmp_project)
        assert ops.forge == "gitlab"
        assert ops.repo == "team/project"


# ── Command construction helpers ──────────────────────────────


def _gh_ops(tmp_path: Path) -> ForgeOps:
    return ForgeOps(tmp_path, forge="github", repo="owner/repo")


def _gl_ops(tmp_path: Path) -> ForgeOps:
    return ForgeOps(tmp_path, forge="gitlab", repo="team/project")


def _mock_result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# ── issue_list ────────────────────────────────────────────────


class TestIssueList:
    def test_github_returns_normalized(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        gh_output = json.dumps([
            {
                "number": 42,
                "title": "Fix bug",
                "labels": [{"name": "bug"}],
                "body": "Details.",
                "author": {"login": "alice"},
            },
        ])
        with patch.object(ops, "_run", return_value=_mock_result(gh_output)):
            issues = ops.issue_list(state="open")
        assert len(issues) == 1
        assert issues[0]["number"] == 42
        assert issues[0]["author"] == "alice"
        assert issues[0]["labels"] == ["bug"]

    def test_gitlab_returns_normalized(self, tmp_path: Path) -> None:
        ops = _gl_ops(tmp_path)
        gl_output = json.dumps([
            {
                "iid": 7,
                "title": "Fix login",
                "labels": ["bug"],
                "description": "Login broken.",
                "author": {"username": "bob"},
            },
        ])
        with patch.object(ops, "_run", return_value=_mock_result(gl_output)):
            issues = ops.issue_list(state="open")
        assert len(issues) == 1
        assert issues[0]["number"] == 7
        assert issues[0]["author"] == "bob"
        assert issues[0]["body"] == "Login broken."

    def test_github_with_labels_filter(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result("[]")) as mock_run:
            ops.issue_list(labels=["plan"], limit=5)
        cmd = mock_run.call_args[0][0]
        assert "--label" in cmd
        assert "plan" in cmd

    def test_gitlab_state_mapping(self, tmp_path: Path) -> None:
        ops = _gl_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result("[]")) as mock_run:
            ops.issue_list(state="open")
        cmd = mock_run.call_args[0][0]
        assert "opened" in cmd
        assert "open" not in cmd[cmd.index("--state") + 1:cmd.index("--state") + 2]

    def test_returns_empty_on_cli_not_found(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", side_effect=FileNotFoundError):
            assert ops.issue_list() == []

    def test_returns_empty_on_timeout(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", side_effect=subprocess.TimeoutExpired("gh", 30)):
            assert ops.issue_list() == []

    def test_returns_empty_on_nonzero_exit(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result("", 1)):
            assert ops.issue_list() == []

    def test_returns_empty_on_invalid_json(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result("not json")):
            assert ops.issue_list() == []

    def test_body_truncated_to_300(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        data = json.dumps([{
            "number": 1, "title": "Long",
            "labels": [], "body": "x" * 500,
            "author": {"login": "someone"},
        }])
        with patch.object(ops, "_run", return_value=_mock_result(data)):
            issues = ops.issue_list()
        assert len(issues[0]["body"]) == 300


# ── post_review ───────────────────────────────────────────────


class TestPostReview:
    def test_github_keep_uses_approve(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result()) as mock_run:
            result = ops.post_review(42, "LGTM", "KEEP")
        assert result is True
        cmd = mock_run.call_args[0][0]
        assert "--approve" in cmd
        assert "review" in cmd

    def test_github_revert_uses_request_changes(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result()) as mock_run:
            result = ops.post_review(42, "Needs fixes", "REVERT")
        assert result is True
        cmd = mock_run.call_args[0][0]
        assert "--request-changes" in cmd

    def test_gitlab_keep_approves_and_notes(self, tmp_path: Path) -> None:
        ops = _gl_ops(tmp_path)
        calls: list[list[str]] = []
        with patch.object(ops, "_run", side_effect=lambda cmd, **kw: (calls.append(cmd), _mock_result())[1]):
            result = ops.post_review(7, "LGTM", "KEEP")
        assert result is True
        assert len(calls) == 2
        assert "approve" in calls[0]
        assert "note" in calls[1]

    def test_gitlab_revert_notes_only(self, tmp_path: Path) -> None:
        ops = _gl_ops(tmp_path)
        calls: list[list[str]] = []
        with patch.object(ops, "_run", side_effect=lambda cmd, **kw: (calls.append(cmd), _mock_result())[1]):
            result = ops.post_review(7, "Needs fixes", "REVERT")
        assert result is True
        assert len(calls) == 1
        assert "note" in calls[0]
        assert "approve" not in calls[0]

    def test_returns_false_on_failure(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result("", 1)):
            assert ops.post_review(42, "body", "KEEP") is False


# ── search_repos ──────────────────────────────────────────────


class TestSearchRepos:
    def test_github_search(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        data = json.dumps([
            {"fullName": "org/tool", "url": "https://github.com/org/tool", "description": "A tool", "stargazersCount": 100},
        ])
        with patch.object(ops, "_run", return_value=_mock_result(data)):
            results = ops.search_repos("task runner")
        assert len(results) == 1
        assert results[0]["name"] == "org/tool"
        assert results[0]["stars"] == 100

    def test_gitlab_search(self, tmp_path: Path) -> None:
        ops = _gl_ops(tmp_path)
        data = json.dumps([
            {"path_with_namespace": "team/tool", "web_url": "https://gitlab.com/team/tool", "description": "A tool", "star_count": 50},
        ])
        with patch.object(ops, "_run", return_value=_mock_result(data)):
            results = ops.search_repos("task runner")
        assert len(results) == 1
        assert results[0]["name"] == "team/tool"
        assert results[0]["stars"] == 50

    def test_returns_empty_on_failure(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", side_effect=FileNotFoundError):
            assert ops.search_repos("query") == []

    def test_null_description_becomes_empty(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        data = json.dumps([
            {"fullName": "org/x", "url": "https://github.com/org/x", "description": None, "stargazersCount": 0},
        ])
        with patch.object(ops, "_run", return_value=_mock_result(data)):
            results = ops.search_repos("q")
        assert results[0]["description"] == ""


# ── get_user ──────────────────────────────────────────────────


class TestGetUser:
    def test_github_user(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result("alice\n")):
            assert ops.get_user() == "alice"

    def test_gitlab_user(self, tmp_path: Path) -> None:
        ops = _gl_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result("bob\n")) as mock_run:
            assert ops.get_user() == "bob"
        cmd = mock_run.call_args[0][0]
        assert "glab" in cmd
        assert ".username" in " ".join(cmd)

    def test_returns_none_on_failure(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result("", 1)):
            assert ops.get_user() is None

    def test_returns_none_on_empty(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result("")):
            assert ops.get_user() is None

    def test_returns_none_on_cli_not_found(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", side_effect=FileNotFoundError):
            assert ops.get_user() is None


# ── pr_diff_names ─────────────────────────────────────────────


class TestPrDiffNames:
    def test_github_uses_name_only(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result("file1.py\nfile2.py\n")) as mock_run:
            names = ops.pr_diff_names(42)
        assert names == ["file1.py", "file2.py"]
        cmd = mock_run.call_args[0][0]
        assert "--name-only" in cmd

    def test_gitlab_parses_diff_headers(self, tmp_path: Path) -> None:
        ops = _gl_ops(tmp_path)
        diff = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            "+new line\n"
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        with patch.object(ops, "_run", return_value=_mock_result(diff)):
            names = ops.pr_diff_names(7)
        assert names == ["src/main.py", "README.md"]

    def test_gitlab_deduplicates(self, tmp_path: Path) -> None:
        ops = _gl_ops(tmp_path)
        diff = "+++ b/file.py\n+++ b/file.py\n"
        with patch.object(ops, "_run", return_value=_mock_result(diff)):
            names = ops.pr_diff_names(7)
        assert names == ["file.py"]


# ── pr_create / pr_list / pr_close / pr_ready ─────────────────


class TestPrOps:
    def test_github_pr_create(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        data = json.dumps({"number": 1, "url": "https://github.com/owner/repo/pull/1"})
        with patch.object(ops, "_run", return_value=_mock_result(data)) as mock_run:
            result = ops.pr_create("title", "body", "main", draft=True)
        assert result is not None
        cmd = mock_run.call_args[0][0]
        assert "--draft" in cmd
        assert "--base" in cmd

    def test_gitlab_mr_create(self, tmp_path: Path) -> None:
        ops = _gl_ops(tmp_path)
        data = json.dumps({"iid": 1, "web_url": "https://gitlab.com/team/project/-/merge_requests/1"})
        with patch.object(ops, "_run", return_value=_mock_result(data)) as mock_run:
            result = ops.pr_create("title", "body", "main", draft=True)
        assert result is not None
        cmd = mock_run.call_args[0][0]
        assert "mr" in cmd
        assert "--target-branch" in cmd
        assert "--description" in cmd
        assert "--draft" in cmd

    def test_pr_close(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result()):
            assert ops.pr_close(42) is True

    def test_pr_close_failure(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result("", 1)):
            assert ops.pr_close(42) is False

    def test_pr_ready_github(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result()) as mock_run:
            assert ops.pr_ready(42) is True
        cmd = mock_run.call_args[0][0]
        assert "ready" in cmd

    def test_pr_ready_gitlab(self, tmp_path: Path) -> None:
        ops = _gl_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result()) as mock_run:
            assert ops.pr_ready(7) is True
        cmd = mock_run.call_args[0][0]
        assert "update" in cmd
        assert "--ready" in cmd

    def test_pr_list_github(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        data = json.dumps([{"number": 1, "title": "PR1", "url": "u", "state": "open"}])
        with patch.object(ops, "_run", return_value=_mock_result(data)):
            prs = ops.pr_list()
        assert len(prs) == 1
        assert prs[0]["number"] == 1

    def test_pr_list_gitlab(self, tmp_path: Path) -> None:
        ops = _gl_ops(tmp_path)
        data = json.dumps([{"iid": 3, "title": "MR3", "web_url": "u", "state": "opened"}])
        with patch.object(ops, "_run", return_value=_mock_result(data)):
            prs = ops.pr_list()
        assert prs[0]["number"] == 3
        assert prs[0]["url"] == "u"


# ── issue_create ──────────────────────────────────────────────


class TestIssueCreate:
    def test_github_issue_create(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        data = json.dumps({"number": 99, "title": "New", "url": "u"})
        with patch.object(ops, "_run", return_value=_mock_result(data)) as mock_run:
            result = ops.issue_create("New", "Body", labels=["bug"])
        assert result is not None
        assert result["number"] == 99
        cmd = mock_run.call_args[0][0]
        assert "--label" in cmd
        assert "bug" in cmd

    def test_gitlab_issue_create(self, tmp_path: Path) -> None:
        ops = _gl_ops(tmp_path)
        data = json.dumps({"iid": 5, "title": "New"})
        with patch.object(ops, "_run", return_value=_mock_result(data)) as mock_run:
            result = ops.issue_create("New", "Body")
        assert result is not None
        cmd = mock_run.call_args[0][0]
        assert "--description" in cmd

    def test_returns_none_on_failure(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", return_value=_mock_result("", 1)):
            assert ops.issue_create("t", "b") is None

    def test_returns_none_on_cli_not_found(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        with patch.object(ops, "_run", side_effect=FileNotFoundError):
            assert ops.issue_create("t", "b") is None


# ── _parse_diff_filenames ─────────────────────────────────────


class TestParseDiffFilenames:
    def test_empty(self) -> None:
        assert ForgeOps._parse_diff_filenames("") == []

    def test_single_file(self) -> None:
        diff = "+++ b/factory/forge.py\n"
        assert ForgeOps._parse_diff_filenames(diff) == ["factory/forge.py"]

    def test_multiple_files(self) -> None:
        diff = (
            "+++ b/a.py\n"
            "+++ b/b.py\n"
            "+++ b/c/d.py\n"
        )
        assert ForgeOps._parse_diff_filenames(diff) == ["a.py", "b.py", "c/d.py"]

    def test_ignores_other_lines(self) -> None:
        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1 +1 @@\n"
            "+new line\n"
        )
        assert ForgeOps._parse_diff_filenames(diff) == ["x.py"]


# ── _repo_args ────────────────────────────────────────────────


class TestRepoArgs:
    def test_github_repo_args(self, tmp_path: Path) -> None:
        ops = _gh_ops(tmp_path)
        assert ops._repo_args() == ["-R", "owner/repo"]

    def test_gitlab_repo_args(self, tmp_path: Path) -> None:
        ops = _gl_ops(tmp_path)
        assert ops._repo_args() == ["--repo", "team/project"]

    def test_empty_repo(self, tmp_path: Path) -> None:
        ops = ForgeOps(tmp_path, forge="github", repo="")
        assert ops._repo_args() == []


# ── Integration with review.py ────────────────────────────────


class TestReviewIntegration:
    def test_post_review_with_forge_kwarg(self, tmp_path: Path) -> None:
        from factory.review import post_review

        with patch("factory.forge.ForgeOps.post_review", return_value=True) as mock_pr:
            result = post_review(42, "LGTM", "KEEP", forge="gitlab", project_path=tmp_path)
        assert result is True
        mock_pr.assert_called_once_with(42, "LGTM", "KEEP")

    def test_post_review_legacy_path(self) -> None:
        from factory.review import post_review

        with patch("factory.review.subprocess.run") as mock_run:
            mock_run.return_value = _mock_result()
            result = post_review(42, "LGTM", "KEEP", repo="owner/repo")
        assert result is True
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["gh", "pr", "review"]


# ── Integration with study.py ─────────────────────────────────


class TestStudyIntegration:
    def test_search_similar_uses_forge(self, tmp_path: Path) -> None:
        from factory.study import _search_similar_projects

        project = tmp_path / "myapp"
        project.mkdir()
        (project / "README.md").write_text("# Task Runner\nRun tasks efficiently.\n")

        data = json.dumps([
            {"fullName": "org/runner", "url": "u", "description": "fast", "stargazersCount": 10},
        ])
        with (
            patch("factory.forge.infer_remote", return_value=("github", "owner/repo")),
            patch("factory.forge.ForgeOps._run", return_value=_mock_result(data)),
        ):
            results = _search_similar_projects(project)
        assert len(results) == 1
        assert results[0]["name"] == "org/runner"

    def test_get_forge_user_returns_username(self, tmp_path: Path) -> None:
        from factory.study import _get_forge_user

        with (
            patch("factory.forge.infer_remote", return_value=("github", "owner/repo")),
            patch("factory.forge.ForgeOps._run", return_value=_mock_result("akashgit\n")),
        ):
            assert _get_forge_user(tmp_path) == "akashgit"

    def test_get_github_user_alias_works(self) -> None:
        from factory.study import _get_github_user
        assert _get_github_user is not None

    def test_fetch_open_issues_uses_forge(self, tmp_path: Path) -> None:
        from factory.study import _fetch_open_issues

        data = json.dumps([
            {"number": 1, "title": "Bug", "labels": [{"name": "bug"}], "body": "fix", "author": {"login": "alice"}},
        ])
        with (
            patch("factory.forge.infer_remote", return_value=("github", "owner/repo")),
            patch("factory.forge.ForgeOps._run", return_value=_mock_result(data)),
        ):
            issues = _fetch_open_issues(tmp_path)
        assert len(issues) == 1
        assert issues[0]["number"] == 1
        assert issues[0]["author"] == "alice"
