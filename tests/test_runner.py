"""Tests for agent runner — output capture, review file saving, and profile injection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from factory.agents.runner import _format_trace_summary, _save_review, resolve_prompt
from factory.runners.types import AgentStep, ExecutionTrace, ToolCallTrace, ToolKind


class TestResolvePromptWithProfile:
    def test_default_no_profile_injection(self) -> None:
        prompt = resolve_prompt("ceo")
        assert "## User Profile" not in prompt

    def test_use_profile_false_no_injection(self) -> None:
        prompt = resolve_prompt("ceo", use_profile=False)
        assert "## User Profile" not in prompt

    def test_use_profile_true_with_profile_file(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "profile.md"
        profile_path.write_text("---\ngenerated: 2024-01-01\n---\n\nThe user is an expert.")
        with patch("factory.profile._PROFILE_PATH", profile_path):
            prompt = resolve_prompt("ceo", use_profile=True)
        assert "## User Profile" in prompt
        assert "The user is an expert." in prompt

    def test_use_profile_true_without_profile_file(self) -> None:
        with patch("factory.profile._PROFILE_PATH", Path("/nonexistent/profile.md")):
            prompt = resolve_prompt("ceo", use_profile=True)
        assert "## User Profile" not in prompt

    def test_profile_after_playbook(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "profile.md"
        profile_path.write_text("The user prefers small PRs.")
        with patch("factory.profile._PROFILE_PATH", profile_path), \
             patch("factory.ace.injector.load_playbook", return_value="DO: write tests"):
            prompt = resolve_prompt("ceo", use_profile=True)
        assert "Behavioral Playbook" in prompt
        playbook_idx = prompt.index("Behavioral Playbook")
        profile_idx = prompt.index("User Profile")
        assert profile_idx > playbook_idx


class TestSaveReview:
    def test_creates_reviews_dir(self, tmp_path: Path) -> None:
        project = tmp_path / "myproject"
        project.mkdir()
        _save_review(project, "researcher", "some output", 0)
        assert (project / ".factory" / "reviews").is_dir()

    def test_writes_latest_file(self, tmp_path: Path) -> None:
        project = tmp_path / "myproject"
        project.mkdir()
        _save_review(project, "strategist", "strategy output here", 0)
        review_file = project / ".factory" / "reviews" / "strategist-latest.md"
        assert review_file.exists()
        content = review_file.read_text()
        assert "strategy output here" in content

    def test_includes_header_metadata(self, tmp_path: Path) -> None:
        project = tmp_path / "myproject"
        project.mkdir()
        _save_review(project, "builder", "build output", 1)
        content = (project / ".factory" / "reviews" / "builder-latest.md").read_text()
        assert "# Builder Agent Output" in content
        assert "exit_code:** 1" in content
        assert "timestamp:**" in content

    def test_overwrites_previous(self, tmp_path: Path) -> None:
        project = tmp_path / "myproject"
        project.mkdir()
        _save_review(project, "researcher", "first run", 0)
        _save_review(project, "researcher", "second run", 0)
        content = (project / ".factory" / "reviews" / "researcher-latest.md").read_text()
        assert "second run" in content
        assert "first run" not in content

    def test_different_roles_separate_files(self, tmp_path: Path) -> None:
        project = tmp_path / "myproject"
        project.mkdir()
        _save_review(project, "researcher", "research output", 0)
        _save_review(project, "strategist", "strategy output", 0)
        assert (project / ".factory" / "reviews" / "researcher-latest.md").exists()
        assert (project / ".factory" / "reviews" / "strategist-latest.md").exists()

    def test_swallows_errors(self, tmp_path: Path) -> None:
        """Should not raise even if path is invalid."""
        # /nonexistent can't be written to — should not raise
        _save_review(Path("/nonexistent/path"), "builder", "output", 0)

    def test_appends_trace_summary(self, tmp_path: Path) -> None:
        project = tmp_path / "myproject"
        project.mkdir()
        trace = ExecutionTrace(
            files_read=["src/main.py"],
            files_written=["src/main.py"],
            commands_executed=["pytest"],
            steps=[AgentStep(step_index=0, tool_calls=[
                ToolCallTrace(tool_name="Read", kind=ToolKind.READ),
            ])],
        )
        _save_review(project, "builder", "build output", 0, trace=trace)
        content = (project / ".factory" / "reviews" / "builder-latest.md").read_text()
        assert "## Builder Trace Summary" in content
        assert "Read 1 files" in content


class TestFormatTraceSummary:
    def test_with_sample_trace(self) -> None:
        trace = ExecutionTrace(
            files_read=["a.py", "b.py", "c.py"],
            files_written=["a.py"],
            commands_executed=["pytest", "ruff check ."],
            steps=[
                AgentStep(step_index=0, tool_calls=[
                    ToolCallTrace(tool_name="Read", kind=ToolKind.READ),
                    ToolCallTrace(tool_name="Edit", kind=ToolKind.EDIT),
                ]),
                AgentStep(step_index=1, tool_calls=[
                    ToolCallTrace(tool_name="Bash", kind=ToolKind.EXECUTE),
                ]),
            ],
            thinking_blocks=["thinking about it", "still thinking"],
        )
        result = _format_trace_summary(trace)
        assert "## Builder Trace Summary" in result
        assert "Read 3 files (a.py, b.py, c.py)" in result
        assert "Edited 1 files (a.py)" in result
        assert "Ran 2 commands: pytest, ruff check ." in result
        assert "3 tool calls across 2 steps" in result
        assert "2 thinking blocks" in result

    def test_with_empty_trace(self) -> None:
        trace = ExecutionTrace()
        result = _format_trace_summary(trace)
        assert result == "## Builder Trace Summary"

    def test_truncates_long_file_lists(self) -> None:
        trace = ExecutionTrace(
            files_read=[f"file{i}.py" for i in range(10)],
        )
        result = _format_trace_summary(trace)
        assert "Read 10 files" in result
        assert "..." in result
        # Only first 5 files should be listed
        assert "file5.py" not in result
