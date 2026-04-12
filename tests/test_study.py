"""Tests for factory.study — interaction log reading."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from factory.study import (
    _extract_keywords,
    _extract_messages,
    _find_log_files,
    _path_to_slug,
    _read_obsidian_notes,
    _search_similar_projects,
    study_project,
)


class TestPathToSlug:
    def test_simple_unix_path(self):
        result = _path_to_slug(Path("~/projects/my-app"))
        assert result == "-Users-akash-projects-my-app"

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


class TestStudyProject:
    def test_no_logs_returns_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with patch("factory.study._search_similar_projects", return_value=[]):
            result = study_project(tmp_path / "nonexistent")
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
            result = study_project(project_path)
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
            result = study_project(project_path)
        assert "## Similar Projects" in result
        assert "org/cool-project" in result
        assert "42 stars" in result
        assert "A cool project" in result

    def test_includes_obsidian_notes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))

        project_path = tmp_path / "myapp"
        project_path.mkdir()

        # Create an Obsidian note
        notes_dir = tmp_path / "vault" / "Work" / "Factory" / "Projects"
        notes_dir.mkdir(parents=True)
        (notes_dir / "myapp.md").write_text(
            "---\ntags:\n  - factory\n---\n\n# Dashboard for myapp\nSome content here."
        )

        with patch("factory.study._search_similar_projects", return_value=[]):
            result = study_project(project_path)
        assert "## Prior Knowledge (Obsidian)" in result
        assert "Dashboard for myapp" in result


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


class TestReadObsidianNotes:
    def test_reads_notes(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault"
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

        experiments_dir = vault / "Work" / "Factory" / "Experiments"
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

        for subdir in ["Experiments", "Projects", "Strategies"]:
            d = vault / "Work" / "Factory" / subdir
            d.mkdir(parents=True)
            (d / "myapp-note.md").write_text(f"---\n---\n\n# {subdir} note")

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

        projects_dir = vault / "Work" / "Factory" / "Projects"
        projects_dir.mkdir(parents=True)
        (projects_dir / "myapp.md").write_text(
            "---\ntags:\n  - factory\n  - project\ndate: 2026-04-11\n---\n\nActual content here."
        )

        summaries = _read_obsidian_notes("myapp")
        assert len(summaries) == 1
        assert "tags:" not in summaries[0]
        assert "Actual content here." in summaries[0]

    def test_truncates_to_200_chars(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault"
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

        projects_dir = vault / "Work" / "Factory" / "Projects"
        projects_dir.mkdir(parents=True)
        (projects_dir / "myapp.md").write_text("x" * 500)

        summaries = _read_obsidian_notes("myapp")
        assert len(summaries) == 1
        assert len(summaries[0]) == 200
