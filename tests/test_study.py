"""Tests for factory.study — interaction log reading."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.study import (
    _detect_self_improvement,
    _extract_deferred_bullets,
    _extract_keywords,
    _extract_messages,
    _fetch_open_issues,
    _find_log_files,
    _get_github_user,
    _load_cross_project_insights,
    _parse_deferred_items,
    _path_to_slug,
    _persist_deferred_items,
    _read_obsidian_notes,
    _search_similar_projects,
    remove_deferred_item,
    study_project,
    study_project_local,
)


class TestPathToSlug:
    def test_simple_unix_path(self):
        result = _path_to_slug(Path("/home/dev/projects/my-app"))
        assert result == "-home-dev-projects-my-app"

    def test_preserves_hyphens(self):
        result = _path_to_slug(Path("/home/user/my-project"))
        assert result == "-home-user-my-project"

    def test_replaces_dots(self):
        result = _path_to_slug(Path("/home/user/app.v2"))
        assert result == "-home-user-app-v2"

    def test_replaces_underscores(self):
        result = _path_to_slug(Path("/home/user/my_project"))
        assert result == "-home-user-my-project"

    def test_replaces_spaces(self):
        result = _path_to_slug(Path("/home/user/my project"))
        assert result == "-home-user-my-project"


class TestFindLogFiles:
    def test_no_project_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = _find_log_files(tmp_path / "nonexistent")
        assert result == []

    def test_finds_jsonl_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "myapp"
        project_path.mkdir()

        slug = _path_to_slug(project_path.resolve())
        log_dir = tmp_path / ".claude" / "projects" / slug
        log_dir.mkdir(parents=True)

        (log_dir / "conv1.jsonl").write_text("")
        (log_dir / "conv2.jsonl").write_text("")
        (log_dir / "other.txt").write_text("")  # should be ignored

        result = _find_log_files(project_path)
        assert len(result) == 2
        assert all(f.suffix == ".jsonl" for f in result)

    def test_returns_sorted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "myapp"
        project_path.mkdir()

        slug = _path_to_slug(project_path.resolve())
        log_dir = tmp_path / ".claude" / "projects" / slug
        log_dir.mkdir(parents=True)

        (log_dir / "b.jsonl").write_text("")
        (log_dir / "a.jsonl").write_text("")

        result = _find_log_files(project_path)
        assert result[0].name == "a.jsonl"
        assert result[1].name == "b.jsonl"


class TestExtractMessages:
    def test_extracts_user_messages(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"content": "Fix the login bug"}}),
            json.dumps({"type": "user", "message": {"content": "Add dark mode"}}),
        ]
        log_file.write_text("\n".join(lines))

        messages = _extract_messages(log_file)
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) == 2
        assert user_msgs[0]["text"] == "Fix the login bug"
        assert user_msgs[1]["text"] == "Add dark mode"

    def test_extracts_error_mentions(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        lines = [
            json.dumps({
                "type": "assistant",
                "message": {"content": "I found an error in the config.\nThe import failed."},
            }),
        ]
        log_file.write_text("\n".join(lines))

        messages = _extract_messages(log_file)
        errors = [m for m in messages if m["role"] == "error"]
        assert len(errors) == 2
        assert "error" in errors[0]["text"].lower()
        assert "failed" in errors[1]["text"].lower()

    def test_handles_content_blocks(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        lines = [
            json.dumps({
                "type": "user",
                "message": {
                    "content": [
                        {"type": "text", "text": "Hello "},
                        {"type": "text", "text": "world"},
                    ],
                },
            }),
        ]
        log_file.write_text("\n".join(lines))

        messages = _extract_messages(log_file)
        assert len(messages) == 1
        assert messages[0]["text"] == "Hello world"

    def test_skips_invalid_json(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text("not valid json\n{also bad")

        messages = _extract_messages(log_file)
        assert messages == []

    def test_skips_long_messages(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"content": "x" * 2001}}),
        ]
        log_file.write_text("\n".join(lines))

        messages = _extract_messages(log_file)
        assert messages == []

    def test_skips_system_prompts(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        lines = [
            json.dumps({
                "type": "user",
                "message": {"content": "Base directory: /foo/bar"},
            }),
            json.dumps({
                "type": "user",
                "message": {"content": "<task-notification>something</task-notification>"},
            }),
        ]
        log_file.write_text("\n".join(lines))

        messages = _extract_messages(log_file)
        assert messages == []

    def test_truncates_user_text_to_500(self, tmp_path):
        log_file = tmp_path / "test.jsonl"
        long_text = "a" * 600
        lines = [
            json.dumps({"type": "user", "message": {"content": long_text}}),
        ]
        log_file.write_text("\n".join(lines))

        messages = _extract_messages(log_file)
        assert len(messages[0]["text"]) == 500


class TestStudyProjectLocal:
    def test_no_logs_returns_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with patch("factory.study._search_similar_projects", return_value=[]):
            result = study_project_local(tmp_path / "nonexistent")
        assert "No interaction logs found." in result
        assert "## Similar Projects" in result
        assert "## Prior Knowledge (Obsidian)" in result

    def test_produces_summary(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "myapp"
        project_path.mkdir()

        slug = _path_to_slug(project_path.resolve())
        log_dir = tmp_path / ".claude" / "projects" / slug
        log_dir.mkdir(parents=True)

        lines = [
            json.dumps({"type": "user", "message": {"content": "Add tests"}}),
            json.dumps({
                "type": "assistant",
                "message": {"content": "The build failed due to a missing import."},
            }),
        ]
        (log_dir / "conv.jsonl").write_text("\n".join(lines))

        with patch("factory.study._search_similar_projects", return_value=[]):
            result = study_project_local(project_path)
        assert "# Interaction Study" in result
        assert "myapp" in result
        assert "1 conversation log(s)" in result
        assert "Add tests" in result
        assert "Errors and Issues" in result
        assert "## Similar Projects" in result
        assert "## Prior Knowledge (Obsidian)" in result

    def test_includes_similar_projects(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "myapp"
        project_path.mkdir()

        similar = [
            {
                "name": "org/cool-project",
                "url": "https://github.com/org/cool-project",
                "description": "A cool project",
                "stars": 42,
            },
        ]
        with patch("factory.study._search_similar_projects", return_value=similar):
            result = study_project_local(project_path)
        assert "## Similar Projects" in result
        assert "org/cool-project" in result
        assert "42 stars" in result
        assert "A cool project" in result

    def test_hypothesis_budget_base(self, tmp_path, monkeypatch):
        """No open issues → fix=0, growth=2, flex=2 → total=4."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with (
            patch("factory.study._search_similar_projects", return_value=[]),
            patch("factory.study._fetch_open_issues", return_value=[]),
            patch("factory.study._get_github_user", return_value="owner"),
        ):
            result = study_project_local(tmp_path / "myapp")
        assert "## Hypothesis Budget" in result
        assert "**Fix slots** | 0" in result
        assert "**Growth slots** | 2" in result

    def test_hypothesis_budget_with_own_issues(self, tmp_path, monkeypatch):
        """9 own issues → fix=3, growth=2, flex=2 → total=7."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        issues = [
            {"number": i, "title": f"Issue {i}", "labels": [], "body": "", "author": "owner"}
            for i in range(9)
        ]
        with (
            patch("factory.study._search_similar_projects", return_value=[]),
            patch("factory.study._fetch_open_issues", return_value=issues),
            patch("factory.study._get_github_user", return_value="owner"),
        ):
            result = study_project_local(tmp_path / "myapp")
        assert "**Fix slots** | 3" in result
        assert "**Recommended hypotheses: 7**" in result
        assert "Your Issues (9)" in result

    def test_community_issues_do_not_drive_fix_slots(self, tmp_path, monkeypatch):
        """9 community issues → fix=0 (none are yours), growth=2, flex=2 → total=4."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        issues = [
            {"number": i, "title": f"Issue {i}", "labels": [], "body": "", "author": "external"}
            for i in range(9)
        ]
        with (
            patch("factory.study._search_similar_projects", return_value=[]),
            patch("factory.study._fetch_open_issues", return_value=issues),
            patch("factory.study._get_github_user", return_value="owner"),
        ):
            result = study_project_local(tmp_path / "myapp")
        assert "**Fix slots** | 0" in result
        assert "Community Issues (9)" in result
        assert "do NOT auto-fix" in result

    def test_mixed_issues_split_correctly(self, tmp_path, monkeypatch):
        """3 own + 6 community → fix=1, own section + community section."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        own = [
            {"number": i, "title": f"Own {i}", "labels": [], "body": "", "author": "owner"}
            for i in range(3)
        ]
        community = [
            {"number": i + 10, "title": f"Ext {i}", "labels": [], "body": "", "author": "someone"}
            for i in range(6)
        ]
        with (
            patch("factory.study._search_similar_projects", return_value=[]),
            patch("factory.study._fetch_open_issues", return_value=own + community),
            patch("factory.study._get_github_user", return_value="owner"),
        ):
            result = study_project_local(tmp_path / "myapp")
        assert "Your Issues (3)" in result
        assert "Community Issues (6)" in result
        assert "**Fix slots** | 1" in result

    def test_hypothesis_budget_small_issue_count(self, tmp_path, monkeypatch):
        """2 own issues → fix=0 (2//3=0), growth=2, flex=2 → total=4."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        issues = [
            {"number": i, "title": f"Issue {i}", "labels": [], "body": "", "author": "owner"}
            for i in range(2)
        ]
        with (
            patch("factory.study._search_similar_projects", return_value=[]),
            patch("factory.study._fetch_open_issues", return_value=issues),
            patch("factory.study._get_github_user", return_value="owner"),
        ):
            result = study_project_local(tmp_path / "myapp")
        assert "**Fix slots** | 0" in result
        assert "**Recommended hypotheses: 4**" in result

    def test_includes_obsidian_notes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("FACTORY_VAULT_PATH", raising=False)
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))

        project_path = tmp_path / "myapp"
        project_path.mkdir()

        # Create an Obsidian note under the new per-project structure
        notes_dir = tmp_path / "vault" / "10-Projects" / "myapp"
        notes_dir.mkdir(parents=True)
        (notes_dir / "myapp.md").write_text(
            "---\ntags:\n  - factory\n---\n\n# Dashboard for myapp\nSome content here."
        )

        with patch("factory.study._search_similar_projects", return_value=[]):
            result = study_project_local(project_path)
        assert "## Prior Knowledge (Obsidian)" in result
        assert "Dashboard for myapp" in result


class TestStudyProject:
    def test_delegates_to_local(self, tmp_path, monkeypatch):
        """study_project() delegates to study_project_local() for backward compat."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with patch("factory.study._search_similar_projects", return_value=[]):
            local_result = study_project_local(tmp_path / "nonexistent")
            wrapper_result = study_project(tmp_path / "nonexistent")
        assert local_result == wrapper_result

    def test_no_logs_returns_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with patch("factory.study._search_similar_projects", return_value=[]):
            result = study_project(tmp_path / "nonexistent")
        assert "No interaction logs found." in result


class TestCmdStudy:
    def test_study_cli_subcommand(self):
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["study", "/some/path"])
        assert args.command == "study"
        assert args.path == "/some/path"

    def test_study_writes_observations(self, tmp_path, monkeypatch, capsys):
        from factory.cli import main

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "myapp"
        project_path.mkdir()

        slug = _path_to_slug(project_path.resolve())
        log_dir = tmp_path / ".claude" / "projects" / slug
        log_dir.mkdir(parents=True)

        lines = [
            json.dumps({"type": "user", "message": {"content": "Hello world"}}),
        ]
        (log_dir / "conv.jsonl").write_text("\n".join(lines))

        with patch("factory.study._search_similar_projects", return_value=[]):
            result = main(["study", str(project_path)])
        assert result == 0

        obs_path = project_path / ".factory" / "strategy" / "observations.md"
        assert obs_path.exists()
        content = obs_path.read_text()
        assert "Hello world" in content
        assert "Hello world" in capsys.readouterr().out


class TestExtractKeywords:
    def test_from_readme(self, tmp_path):
        project = tmp_path / "myapp"
        project.mkdir()
        (project / "README.md").write_text("# Cloud Gateway\nA lightweight API gateway.\n")
        keywords = _extract_keywords(project)
        assert "cloud" in keywords
        assert "gateway" in keywords
        assert len(keywords) <= 5

    def test_from_pyproject(self, tmp_path):
        project = tmp_path / "myapp"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname = "data-pipeline"\n'
            'description = "Stream processing toolkit"\n'
        )
        keywords = _extract_keywords(project)
        assert "data" in keywords
        assert "pipeline" in keywords

    def test_fallback_to_dirname(self, tmp_path):
        project = tmp_path / "my-cool-tool"
        project.mkdir()
        keywords = _extract_keywords(project)
        assert "cool" in keywords
        assert "tool" in keywords

    def test_filters_stop_words(self, tmp_path):
        project = tmp_path / "myapp"
        project.mkdir()
        (project / "README.md").write_text(
            "# The Project\nThis is a tool for the web.\n"
        )
        keywords = _extract_keywords(project)
        assert "the" not in keywords
        assert "this" not in keywords
        assert "project" in keywords

    def test_returns_max_five(self, tmp_path):
        project = tmp_path / "myapp"
        project.mkdir()
        (project / "README.md").write_text(
            "# Alpha Beta Gamma Delta Epsilon Zeta Eta Theta\n"
        )
        keywords = _extract_keywords(project)
        assert len(keywords) <= 5

    def test_deduplicates(self, tmp_path):
        project = tmp_path / "myapp"
        project.mkdir()
        (project / "README.md").write_text("# Factory Factory Factory\n")
        keywords = _extract_keywords(project)
        assert keywords.count("factory") == 1


class TestSearchSimilarProjects:
    def test_success(self, tmp_path):
        project = tmp_path / "myapp"
        project.mkdir()
        (project / "README.md").write_text("# Task Runner\nRun tasks efficiently.\n")

        gh_output = json.dumps([
            {
                "fullName": "org/task-runner",
                "url": "https://github.com/org/task-runner",
                "description": "A fast task runner",
                "stargazersCount": 100,
            },
            {
                "fullName": "user/runner2",
                "url": "https://github.com/user/runner2",
                "description": None,
                "stargazersCount": 50,
            },
        ])
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=gh_output, stderr=""
        )
        with patch("factory.study.subprocess.run", return_value=mock_result):
            results = _search_similar_projects(project)

        assert len(results) == 2
        assert results[0]["name"] == "org/task-runner"
        assert results[0]["stars"] == 100
        assert results[0]["description"] == "A fast task runner"
        # None description should become empty string
        assert results[1]["description"] == ""

    def test_gh_not_found(self, tmp_path):
        project = tmp_path / "myapp"
        project.mkdir()
        (project / "README.md").write_text("# Some Project\n")

        with patch(
            "factory.study.subprocess.run", side_effect=FileNotFoundError("gh not found")
        ):
            results = _search_similar_projects(project)
        assert results == []

    def test_gh_timeout(self, tmp_path):
        project = tmp_path / "myapp"
        project.mkdir()
        (project / "README.md").write_text("# Some Project\n")

        with patch(
            "factory.study.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=15),
        ):
            results = _search_similar_projects(project)
        assert results == []

    def test_gh_nonzero_exit(self, tmp_path):
        project = tmp_path / "myapp"
        project.mkdir()
        (project / "README.md").write_text("# Some Project\n")

        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="auth required"
        )
        with patch("factory.study.subprocess.run", return_value=mock_result):
            results = _search_similar_projects(project)
        assert results == []

    def test_no_keywords(self, tmp_path):
        project = tmp_path / "myapp"
        project.mkdir()
        # Empty README
        (project / "README.md").write_text("")
        results = _search_similar_projects(project)
        assert results == []

    def test_invalid_json_output(self, tmp_path):
        project = tmp_path / "myapp"
        project.mkdir()
        (project / "README.md").write_text("# Some Project\n")

        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json", stderr=""
        )
        with patch("factory.study.subprocess.run", return_value=mock_result):
            results = _search_similar_projects(project)
        assert results == []


class TestGetGithubUser:
    def test_returns_login(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="akashgit\n", stderr=""
        )
        with patch("factory.study.subprocess.run", return_value=mock_result):
            assert _get_github_user() == "akashgit"

    def test_returns_none_on_failure(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not logged in"
        )
        with patch("factory.study.subprocess.run", return_value=mock_result):
            assert _get_github_user() is None

    def test_returns_none_on_missing_gh(self):
        with patch(
            "factory.study.subprocess.run", side_effect=FileNotFoundError("gh not found")
        ):
            assert _get_github_user() is None

    def test_returns_none_on_empty_output(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with patch("factory.study.subprocess.run", return_value=mock_result):
            assert _get_github_user() is None


class TestFetchOpenIssues:
    def test_success(self, tmp_path):
        gh_output = json.dumps([
            {
                "number": 42,
                "title": "Fix login bug",
                "labels": [{"name": "bug"}, {"name": "priority"}],
                "body": "Login fails when password contains special chars.",
                "author": {"login": "owner"},
            },
            {
                "number": 7,
                "title": "Add dark mode",
                "labels": [],
                "body": None,
                "author": {"login": "contributor"},
            },
        ])
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=gh_output, stderr=""
        )
        with patch("factory.study.subprocess.run", return_value=mock_result):
            issues = _fetch_open_issues(tmp_path)

        assert len(issues) == 2
        assert issues[0]["number"] == 42
        assert issues[0]["title"] == "Fix login bug"
        assert issues[0]["labels"] == ["bug", "priority"]
        assert "special chars" in issues[0]["body"]
        assert issues[0]["author"] == "owner"
        assert issues[1]["body"] == ""
        assert issues[1]["author"] == "contributor"

    def test_gh_not_found(self, tmp_path):
        with patch(
            "factory.study.subprocess.run", side_effect=FileNotFoundError("gh not found")
        ):
            assert _fetch_open_issues(tmp_path) == []

    def test_gh_timeout(self, tmp_path):
        with patch(
            "factory.study.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=15),
        ):
            assert _fetch_open_issues(tmp_path) == []

    def test_nonzero_exit(self, tmp_path):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not a git repo"
        )
        with patch("factory.study.subprocess.run", return_value=mock_result):
            assert _fetch_open_issues(tmp_path) == []

    def test_invalid_json(self, tmp_path):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json", stderr=""
        )
        with patch("factory.study.subprocess.run", return_value=mock_result):
            assert _fetch_open_issues(tmp_path) == []

    def test_body_truncated_to_300(self, tmp_path):
        long_body = "x" * 500
        gh_output = json.dumps([{
            "number": 1, "title": "Long issue",
            "labels": [], "body": long_body,
            "author": {"login": "someone"},
        }])
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=gh_output, stderr=""
        )
        with patch("factory.study.subprocess.run", return_value=mock_result):
            issues = _fetch_open_issues(tmp_path)
        assert len(issues[0]["body"]) == 300


class TestReadObsidianNotes:
    @pytest.fixture(autouse=True)
    def _clear_factory_vault(self, monkeypatch):
        monkeypatch.delenv("FACTORY_VAULT_PATH", raising=False)

    def test_reads_notes(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault"
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

        experiments_dir = vault / "10-Projects" / "myapp" / "Experiments"
        experiments_dir.mkdir(parents=True)
        (experiments_dir / "myapp-001.md").write_text(
            "---\ntags:\n  - factory\n---\n\n# Experiment #1\nImproved performance."
        )

        summaries = _read_obsidian_notes("myapp")
        assert len(summaries) == 1
        assert "Experiment #1" in summaries[0]

    def test_multiple_subdirs(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault"
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

        project_dir = vault / "10-Projects" / "myapp"
        for subdir in ["Experiments", "Strategies"]:
            d = project_dir / subdir
            d.mkdir(parents=True)
            (d / "myapp-note.md").write_text(f"---\n---\n\n# {subdir} note")
        # Also create dashboard
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "myapp.md").write_text("---\n---\n\n# Dashboard note")

        summaries = _read_obsidian_notes("myapp")
        assert len(summaries) == 3

    def test_empty_vault(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

        summaries = _read_obsidian_notes("myapp")
        assert summaries == []

    def test_no_vault(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "nonexistent"))
        summaries = _read_obsidian_notes("myapp")
        assert summaries == []

    def test_skips_frontmatter(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault"
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

        project_dir = vault / "10-Projects" / "myapp"
        project_dir.mkdir(parents=True)
        (project_dir / "myapp.md").write_text(
            "---\ntags:\n  - factory\n  - project\ndate: 2026-04-11\n---\n\nActual content here."
        )

        summaries = _read_obsidian_notes("myapp")
        assert len(summaries) == 1
        assert "tags:" not in summaries[0]
        assert "Actual content here." in summaries[0]

    def test_truncates_to_200_chars(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault"
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

        project_dir = vault / "10-Projects" / "myapp"
        project_dir.mkdir(parents=True)
        (project_dir / "myapp.md").write_text("x" * 500)

        summaries = _read_obsidian_notes("myapp")
        assert len(summaries) == 1
        assert len(summaries[0]) == 200

    def test_cross_project_knowledge(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault"
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

        concepts_dir = vault / "20-Knowledge" / "Concepts"
        concepts_dir.mkdir(parents=True)
        (concepts_dir / "caching.md").write_text(
            "---\ntags:\n  - concept\n---\n\n# Caching patterns for APIs"
        )

        summaries = _read_obsidian_notes("myapp")
        assert len(summaries) == 1
        assert "Caching patterns" in summaries[0]


# ── TestDetectSelfImprovement ─────────────────────────────────────


class TestDetectSelfImprovement:
    def test_detects_factory_project(self, tmp_path):
        factory_dir = tmp_path / "factory"
        factory_dir.mkdir()
        (factory_dir / "cli.py").write_text("# cli")
        (factory_dir / "insights.py").write_text("# insights")
        assert _detect_self_improvement(tmp_path) is True

    def test_rejects_non_factory_project(self, tmp_path):
        assert _detect_self_improvement(tmp_path) is False

    def test_rejects_partial_factory(self, tmp_path):
        factory_dir = tmp_path / "factory"
        factory_dir.mkdir()
        (factory_dir / "cli.py").write_text("# cli")
        # Missing insights.py
        assert _detect_self_improvement(tmp_path) is False


# ── TestLoadCrossProjectInsights ──────────────────────────────────


class TestLoadCrossProjectInsights:
    def test_loads_from_multi_project_dir(self, tmp_path):
        # Create two projects with TSV data
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        for name in ["alpha", "beta"]:
            proj = projects_dir / name
            proj.mkdir()
            factory = proj / ".factory"
            factory.mkdir()
            lines = [
                "id\ttimestamp\thypothesis\tchange_summary\tissue_number\tpr_number\t"
                "score_before\tscore_after\tdelta\tverdict\tcost_usd\tnotes",
                f"1\t2026-04-13T12:00:00\tFix a bug in {name}\tsummary\t\t\t\t\t\tkeep\t\t",
            ]
            (factory / "results.tsv").write_text("\n".join(lines) + "\n")

        # Target project for output
        target = tmp_path / "target"
        target.mkdir()
        (target / ".factory").mkdir()

        result = _load_cross_project_insights(target, projects_dir)
        assert "Cross-Project Insights" in result
        assert "alpha" in result
        assert "beta" in result
        # Check insights.md was written
        insights_path = target / ".factory" / "strategy" / "insights.md"
        assert insights_path.exists()

    def test_returns_empty_for_no_projects(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        target = tmp_path / "target"
        target.mkdir()
        result = _load_cross_project_insights(target, empty_dir)
        assert result == ""

    def test_returns_empty_for_no_histories(self, tmp_path):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        proj = projects_dir / "empty-proj"
        proj.mkdir()
        factory = proj / ".factory"
        factory.mkdir()
        (factory / "results.tsv").write_text(
            "id\ttimestamp\thypothesis\tchange_summary\tissue_number\tpr_number\t"
            "score_before\tscore_after\tdelta\tverdict\tcost_usd\tnotes\n"
        )
        target = tmp_path / "target"
        target.mkdir()
        result = _load_cross_project_insights(target, projects_dir)
        assert result == ""


# ── TestStudyWithInsights ─────────────────────────────────────────


class TestStudyWithInsights:
    def test_observations_include_cross_project(self, tmp_path, monkeypatch):
        # Set up a minimal project so study_project doesn't crash
        monkeypatch.setattr("factory.study._find_log_files", lambda _: [])
        monkeypatch.setattr("factory.study._search_similar_projects", lambda _: [])
        monkeypatch.setattr("factory.study._read_obsidian_notes", lambda _: [])
        monkeypatch.setattr(
            "factory.study._analyze_observability",
            lambda _path, _lang: {
                "observability_score": 0.5,
                "function_coverage": 0.5,
                "total_functions": 10,
                "logged_functions": 5,
                "total_log_statements": 20,
                "has_structured_logging": True,
                "has_request_tracing": False,
                "logging_framework": "structlog",
                "gaps": [],
                "recommendations": [],
            },
        )
        monkeypatch.setattr(
            "factory.discovery.introspect._detect_language",
            lambda _: "python",
        )

        # Create projects dir with data
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        proj = projects_dir / "some-project"
        proj.mkdir()
        factory = proj / ".factory"
        factory.mkdir()
        lines = [
            "id\ttimestamp\thypothesis\tchange_summary\tissue_number\tpr_number\t"
            "score_before\tscore_after\tdelta\tverdict\tcost_usd\tnotes",
            "1\t2026-04-13T12:00:00\tFix a bug\tsummary\t\t\t\t\t\tkeep\t\t",
        ]
        (factory / "results.tsv").write_text("\n".join(lines) + "\n")

        # Target project
        target = tmp_path / "target"
        target.mkdir()
        (target / ".factory").mkdir()

        summary = study_project(target, projects_dir=str(projects_dir))
        assert "Cross-Project Insights" in summary

    def test_self_improvement_context_added(self, tmp_path, monkeypatch):
        monkeypatch.setattr("factory.study._find_log_files", lambda _: [])
        monkeypatch.setattr("factory.study._search_similar_projects", lambda _: [])
        monkeypatch.setattr("factory.study._read_obsidian_notes", lambda _: [])
        monkeypatch.setattr(
            "factory.study._analyze_observability",
            lambda _path, _lang: {
                "observability_score": 0.5,
                "function_coverage": 0.5,
                "total_functions": 10,
                "logged_functions": 5,
                "total_log_statements": 20,
                "has_structured_logging": True,
                "has_request_tracing": False,
                "logging_framework": "structlog",
                "gaps": [],
                "recommendations": [],
            },
        )
        monkeypatch.setattr(
            "factory.discovery.introspect._detect_language",
            lambda _: "python",
        )

        # Make project look like the factory
        target = tmp_path / "target"
        target.mkdir()
        (target / ".factory").mkdir()
        factory_dir = target / "factory"
        factory_dir.mkdir()
        (factory_dir / "cli.py").write_text("# cli")
        (factory_dir / "insights.py").write_text("# insights")

        summary = study_project(target)
        assert "Self-Improvement Context" in summary
        assert "Self-evolution" in summary


# ── TestCmdStudyProjectsDir ───────────────────────────────────────


class TestCmdStudyProjectsDir:
    def test_parser_accepts_projects_dir(self):
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["study", "/some/path", "--projects-dir", "/other/path"])
        assert args.projects_dir == "/other/path"

    def test_parser_projects_dir_default_is_none(self):
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["study", "/some/path"])
        assert args.projects_dir is None


class TestExtractDeferredBullets:
    def test_extracts_from_deferred_heading(self):
        content = "## Deferred\n- Camera integration\n- Genre expansion\n"
        assert _extract_deferred_bullets(content) == [
            "Camera integration",
            "Genre expansion",
        ]

    def test_extracts_from_post_mvp_heading(self):
        content = "### Post-MVP Items\n- OAuth login\n- Admin dashboard\n"
        assert _extract_deferred_bullets(content) == ["OAuth login", "Admin dashboard"]

    def test_extracts_from_future_work_heading(self):
        content = "## Future Work\n* Internationalization\n"
        assert _extract_deferred_bullets(content) == ["Internationalization"]

    def test_extracts_from_backlog_heading(self):
        content = "### Backlog\n- Rate limiting\n"
        assert _extract_deferred_bullets(content) == ["Rate limiting"]

    def test_stops_at_next_heading(self):
        content = (
            "## Deferred\n- Item one\n- Item two\n"
            "## Next Section\n- Not deferred\n"
        )
        assert _extract_deferred_bullets(content) == ["Item one", "Item two"]

    def test_handles_multiple_deferred_sections(self):
        content = (
            "## Deferred\n- First\n"
            "## Other\n- Skip\n"
            "### Backlog\n- Second\n"
        )
        assert _extract_deferred_bullets(content) == ["First", "Second"]

    def test_skips_empty_bullets(self):
        content = "## Deferred\n- \n- Real item\n-  \n"
        assert _extract_deferred_bullets(content) == ["Real item"]

    def test_returns_empty_for_no_deferred_section(self):
        content = "## Hypotheses\n- H1: Add tests\n## FEEC\n- Fix stuff\n"
        assert _extract_deferred_bullets(content) == []

    def test_handles_bullet_prefix(self):
        content = "## Deferred\n• Unicode bullet item\n"
        assert _extract_deferred_bullets(content) == ["Unicode bullet item"]

    def test_case_insensitive_heading(self):
        content = "## POST-MVP\n- Something\n## DEFERRED items\n- Another\n"
        assert _extract_deferred_bullets(content) == ["Something", "Another"]

    def test_preserves_bold_in_items(self):
        content = "## Deferred\n- **Docker-Wyze-Bridge** camera integration\n"
        assert _extract_deferred_bullets(content) == [
            "**Docker-Wyze-Bridge** camera integration"
        ]

    def test_ignores_non_bullet_lines(self):
        content = "## Deferred\nSome paragraph text.\n- Actual item\n\nMore text.\n"
        assert _extract_deferred_bullets(content) == ["Actual item"]

    def test_bold_text_heading(self):
        content = (
            "**Total estimated phases:** 5\n\n"
            "**What is deferred (post-MVP):**\n"
            "- Docker-Wyze-Bridge\n- RSS feed\n- Deployment\n\n"
        )
        assert _extract_deferred_bullets(content) == [
            "Docker-Wyze-Bridge", "RSS feed", "Deployment",
        ]

    def test_bold_heading_stops_at_next_bold_heading(self):
        content = (
            "**Deferred:**\n- Item one\n- Item two\n"
            "**Other section:**\n- Not deferred\n"
        )
        assert _extract_deferred_bullets(content) == ["Item one", "Item two"]

    def test_bold_heading_stops_at_markdown_heading(self):
        content = "**Backlog:**\n- Backlog item\n## Next Phase\n- Phase item\n"
        assert _extract_deferred_bullets(content) == ["Backlog item"]


class TestParseDeferredItems:
    def test_returns_empty_when_no_factory_dir(self, tmp_path):
        assert _parse_deferred_items(tmp_path) == []

    def test_reads_from_current_md(self, tmp_path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text(
            "## Hypotheses\n- H1\n## Deferred\n- Camera feed\n- Genre expansion\n"
        )
        assert _parse_deferred_items(tmp_path) == ["Camera feed", "Genre expansion"]

    def test_reads_from_deferred_md(self, tmp_path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "deferred.md").write_text("- OAuth login\n- Rate limiting\n")
        assert _parse_deferred_items(tmp_path) == ["OAuth login", "Rate limiting"]

    def test_merges_both_files_without_duplicates(self, tmp_path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "deferred.md").write_text("- Camera feed\n- OAuth login\n")
        (strategy_dir / "current.md").write_text(
            "## Deferred\n- Camera feed\n- Genre expansion\n"
        )
        result = _parse_deferred_items(tmp_path)
        assert result == ["Camera feed", "OAuth login", "Genre expansion"]

    def test_deferred_md_takes_precedence_in_order(self, tmp_path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "deferred.md").write_text("- Persistent item\n")
        (strategy_dir / "current.md").write_text("## Deferred\n- New item\n")
        result = _parse_deferred_items(tmp_path)
        assert result[0] == "Persistent item"

    def test_survives_current_md_rewrite(self, tmp_path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "deferred.md").write_text("- Camera feed\n- OAuth login\n")
        (strategy_dir / "current.md").write_text(
            "## Hypotheses\n- H1: Add tests\n- H2: Improve logging\n"
        )
        result = _parse_deferred_items(tmp_path)
        assert result == ["Camera feed", "OAuth login"]


class TestPersistDeferredItems:
    def test_writes_deferred_file(self, tmp_path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        _persist_deferred_items(tmp_path, ["Camera feed", "OAuth login"])
        content = (strategy_dir / "deferred.md").read_text()
        assert "- Camera feed\n" in content
        assert "- OAuth login\n" in content

    def test_creates_strategy_dir_if_missing(self, tmp_path):
        _persist_deferred_items(tmp_path, ["Some item"])
        assert (tmp_path / ".factory" / "strategy" / "deferred.md").exists()

    def test_no_op_for_empty_list(self, tmp_path):
        _persist_deferred_items(tmp_path, [])
        assert not (tmp_path / ".factory" / "strategy" / "deferred.md").exists()

    def test_overwrites_existing_file(self, tmp_path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "deferred.md").write_text("- Old item\n")
        _persist_deferred_items(tmp_path, ["New item"])
        content = (strategy_dir / "deferred.md").read_text()
        assert "Old item" not in content
        assert "- New item\n" in content


class TestStudyDeferredIntegration:
    def test_observations_include_deferred_items(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "myapp"
        project_path.mkdir()
        strategy_dir = project_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text(
            "## Hypotheses\n- H1\n## Deferred\n- Camera integration\n"
        )
        with patch("factory.study._search_similar_projects", return_value=[]):
            result = study_project_local(project_path)
        assert "## Deferred Items from Build Mode" in result
        assert "Camera integration" in result

    def test_deferred_items_persisted_to_deferred_md(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "myapp"
        project_path.mkdir()
        strategy_dir = project_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text("## Deferred\n- Camera feed\n")
        with patch("factory.study._search_similar_projects", return_value=[]):
            study_project_local(project_path)
        deferred_path = strategy_dir / "deferred.md"
        assert deferred_path.exists()
        assert "Camera feed" in deferred_path.read_text()

    def test_deferred_slots_in_budget(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "myapp"
        project_path.mkdir()
        strategy_dir = project_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text(
            "## Deferred\n- Item 1\n- Item 2\n- Item 3\n"
        )
        with patch("factory.study._search_similar_projects", return_value=[]):
            result = study_project_local(project_path)
        assert "**Deferred slots** | 2" in result

    def test_no_deferred_section_when_no_items(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "myapp"
        project_path.mkdir()
        with patch("factory.study._search_similar_projects", return_value=[]):
            result = study_project_local(project_path)
        assert "Deferred Items" not in result
        assert "Deferred slots" not in result


class TestRemoveDeferredItem:
    def test_removes_exact_match(self, tmp_path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "deferred.md").write_text(
            "- Camera feed\n- OAuth login\n- Genre expansion\n"
        )
        assert remove_deferred_item(tmp_path, "OAuth login") is True
        content = (strategy_dir / "deferred.md").read_text()
        assert "OAuth login" not in content
        assert "Camera feed" in content
        assert "Genre expansion" in content

    def test_returns_false_when_not_found(self, tmp_path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "deferred.md").write_text("- Camera feed\n")
        assert remove_deferred_item(tmp_path, "Nonexistent item") is False

    def test_returns_false_when_no_file(self, tmp_path):
        assert remove_deferred_item(tmp_path, "Anything") is False

    def test_deletes_file_when_last_item_removed(self, tmp_path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "deferred.md").write_text("- Only item\n")
        assert remove_deferred_item(tmp_path, "Only item") is True
        assert not (strategy_dir / "deferred.md").exists()

    def test_no_partial_match(self, tmp_path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "deferred.md").write_text("- Camera feed integration\n")
        assert remove_deferred_item(tmp_path, "Camera feed") is False
        assert (strategy_dir / "deferred.md").exists()

    def test_handles_different_bullet_styles(self, tmp_path):
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "deferred.md").write_text("* Camera feed\n")
        assert remove_deferred_item(tmp_path, "Camera feed") is True
        assert not (strategy_dir / "deferred.md").exists()


class TestCmdDeferredRemove:
    def test_cli_subcommand_exists(self):
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["deferred-remove", "/some/path", "some item"])
        assert args.command == "deferred-remove"
        assert args.path == "/some/path"
        assert args.item == "some item"

    def test_removes_item_via_cli(self, tmp_path):
        from factory.cli import main

        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "deferred.md").write_text("- Camera feed\n- OAuth\n")
        result = main(["deferred-remove", str(tmp_path), "Camera feed"])
        assert result == 0
        assert "Camera feed" not in (strategy_dir / "deferred.md").read_text()

    def test_returns_error_when_not_found(self, tmp_path):
        from factory.cli import main

        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "deferred.md").write_text("- Camera feed\n")
        result = main(["deferred-remove", str(tmp_path), "Nonexistent"])
        assert result == 1


class TestCmdDeferredList:
    def test_cli_subcommand_exists(self):
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["deferred-list", "/some/path"])
        assert args.command == "deferred-list"

    def test_lists_items(self, tmp_path, capsys):
        from factory.cli import main

        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "deferred.md").write_text("- Camera feed\n- OAuth login\n")
        result = main(["deferred-list", str(tmp_path)])
        assert result == 0
        output = capsys.readouterr().out
        assert "Camera feed" in output
        assert "OAuth login" in output

    def test_empty_list(self, tmp_path, capsys):
        from factory.cli import main

        result = main(["deferred-list", str(tmp_path)])
        assert result == 0
        assert "No deferred items" in capsys.readouterr().out

    def test_b5a_items_survive_current_md_rewrite(self, tmp_path):
        """B5a scenario: Builder adds deferred items to current.md, deferred-list
        persists them, then Strategist rewrites current.md — items survive."""
        from factory.cli import main

        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text(
            "## Build Plan\n- Phase 1: scaffold\n## Deferred\n"
            "- Camera integration\n- Docker-Wyze-Bridge setup\n"
        )
        main(["deferred-list", str(tmp_path)])
        deferred_path = strategy_dir / "deferred.md"
        assert deferred_path.exists()
        assert "Camera integration" in deferred_path.read_text()

        (strategy_dir / "current.md").write_text(
            "## Hypotheses\n- H1: Add test coverage\n- H2: Refactor auth\n"
        )
        items = _parse_deferred_items(tmp_path)
        assert "Camera integration" in items
        assert "Docker-Wyze-Bridge setup" in items
