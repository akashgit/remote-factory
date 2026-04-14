"""Tests for factory.study — interaction log reading."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from factory.study import (
    _detect_self_improvement,
    _extract_keywords,
    _extract_messages,
    _find_log_files,
    _load_cross_project_insights,
    _path_to_slug,
    _read_obsidian_notes,
    _search_similar_projects,
    study_project,
    study_project_local,
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

    def test_includes_obsidian_notes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
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


class TestReadObsidianNotes:
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
