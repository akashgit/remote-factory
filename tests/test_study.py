"""Tests for factory.study — interaction log reading."""

import json
from pathlib import Path

from factory.study import _path_to_slug, _find_log_files, _extract_messages, study_project


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
        result = study_project(tmp_path / "nonexistent")
        assert result == "No interaction logs found."

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

        result = study_project(project_path)
        assert "# Interaction Study" in result
        assert "myapp" in result
        assert "1 conversation log(s)" in result
        assert "Add tests" in result
        assert "Errors and Issues" in result


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

        result = main(["study", str(project_path)])
        assert result == 0

        obs_path = project_path / ".factory" / "strategy" / "observations.md"
        assert obs_path.exists()
        content = obs_path.read_text()
        assert "Hello world" in content
        assert "Hello world" in capsys.readouterr().out
