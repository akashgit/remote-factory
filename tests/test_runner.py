"""Tests for agent runner — output capture, review file saving, and profile injection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from factory.agents.runner import _save_review, resolve_prompt


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
