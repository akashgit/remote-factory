"""Tests for factory.profile — evidence collection, synthesis, loading, and injection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from factory.profile import (
    _MAX_SECTION_CHARS,
    _build_synthesis_task,
    _read_ace_playbooks,
    _read_auto_memory,
    _truncate,
    collect_evidence,
    inject_profile,
    load_profile,
    save_profile,
)


class TestTruncate:
    def test_short_text_unchanged(self) -> None:
        assert _truncate("hello") == "hello"

    def test_long_text_truncated(self) -> None:
        text = "a" * (_MAX_SECTION_CHARS + 100)
        result = _truncate(text)
        assert len(result) < len(text)
        assert result.endswith("... (truncated)")


class TestCollectEvidence:
    def test_empty_projects(self) -> None:
        evidence = collect_evidence([])
        assert isinstance(evidence, dict)
        assert len(evidence) == 5
        assert evidence["experiment_history"] == ""
        assert evidence["ceo_verdicts"] == ""
        assert evidence["strategy_observations"] == ""

    def test_with_results_tsv(self, tmp_path: Path) -> None:
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "results.tsv").write_text("id\thypothesis\tverdict\n1\ttest\tkeep\n")
        evidence = collect_evidence([tmp_path])
        assert "test" in evidence["experiment_history"]
        assert tmp_path.name in evidence["experiment_history"]

    def test_with_events_jsonl(self, tmp_path: Path) -> None:
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "events.jsonl").write_text('{"type":"agent.started"}\n')
        evidence = collect_evidence([tmp_path])
        assert "agent.started" in evidence["ceo_verdicts"]

    def test_with_strategy_notes(self, tmp_path: Path) -> None:
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "observations.md").write_text("## Key Insight\nFeatures > hygiene")
        evidence = collect_evidence([tmp_path])
        assert "Key Insight" in evidence["strategy_observations"]

    def test_with_archive_notes(self, tmp_path: Path) -> None:
        archive_dir = tmp_path / ".factory" / "archive" / "experiments"
        archive_dir.mkdir(parents=True)
        (archive_dir / "001.md").write_text("Learned that X works")
        evidence = collect_evidence([tmp_path])
        assert "Learned that X works" in evidence["strategy_observations"]

    def test_multiple_projects(self, tmp_path: Path) -> None:
        p1 = tmp_path / "proj-a"
        p2 = tmp_path / "proj-b"
        for p in (p1, p2):
            factory_dir = p / ".factory"
            factory_dir.mkdir(parents=True)
            (factory_dir / "results.tsv").write_text(f"data for {p.name}\n")
        evidence = collect_evidence([p1, p2])
        assert "proj-a" in evidence["experiment_history"]
        assert "proj-b" in evidence["experiment_history"]

    def test_size_cap_applied(self, tmp_path: Path) -> None:
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        large_content = "x" * (_MAX_SECTION_CHARS + 500)
        (factory_dir / "results.tsv").write_text(large_content)
        evidence = collect_evidence([tmp_path])
        assert len(evidence["experiment_history"]) <= _MAX_SECTION_CHARS + 50

    def test_missing_factory_dir_is_fine(self, tmp_path: Path) -> None:
        evidence = collect_evidence([tmp_path])
        assert evidence["experiment_history"] == ""


class TestSaveProfile:
    def test_writes_file_with_frontmatter(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "profile.md"
        with patch("factory.profile._PROFILE_PATH", profile_path):
            result = save_profile("Test profile content", ["proj-a"], "claude")
        assert result == profile_path
        text = profile_path.read_text()
        assert text.startswith("---\n")
        assert "generated:" in text
        assert "proj-a" in text
        assert "runner: claude" in text
        assert "Test profile content" in text

    def test_source_projects_listed(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "profile.md"
        with patch("factory.profile._PROFILE_PATH", profile_path):
            save_profile("content", ["a", "b", "c"], "bob")
        text = profile_path.read_text()
        assert '  - "a"\n' in text
        assert '  - "b"\n' in text
        assert '  - "c"\n' in text


class TestLoadProfile:
    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert load_profile(tmp_path / "nonexistent.md") is None

    def test_returns_none_for_empty_file(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.md"
        empty.write_text("")
        assert load_profile(empty) is None

    def test_strips_frontmatter(self, tmp_path: Path) -> None:
        profile = tmp_path / "profile.md"
        profile.write_text("---\ngenerated: 2024-01-01\n---\n\nProfile body here")
        result = load_profile(profile)
        assert result is not None
        assert "---" not in result
        assert "generated:" not in result
        assert "Profile body here" in result

    def test_no_frontmatter(self, tmp_path: Path) -> None:
        profile = tmp_path / "profile.md"
        profile.write_text("Just plain profile text")
        result = load_profile(profile)
        assert result == "Just plain profile text"

    def test_roundtrip_save_load(self, tmp_path: Path) -> None:
        profile_path = tmp_path / "profile.md"
        with patch("factory.profile._PROFILE_PATH", profile_path):
            save_profile("## Technical Identity\nSenior engineer", ["proj"], "claude")
            result = load_profile(profile_path)
        assert result is not None
        assert "## Technical Identity" in result
        assert "Senior engineer" in result


class TestInjectProfile:
    def test_appends_profile_section(self) -> None:
        prompt = "You are the CEO agent."
        profile = "The user is a senior engineer who prefers small PRs."
        result = inject_profile(prompt, profile)
        assert "You are the CEO agent." in result
        assert "## User Profile" in result
        assert "senior engineer" in result

    def test_profile_appears_after_prompt(self) -> None:
        prompt = "Base prompt"
        profile = "Profile content"
        result = inject_profile(prompt, profile)
        prompt_idx = result.index("Base prompt")
        profile_idx = result.index("Profile content")
        assert profile_idx > prompt_idx

    def test_section_header_format(self) -> None:
        result = inject_profile("prompt", "profile text")
        assert "## User Profile (auto-generated from session history)" in result


class TestReadAutoMemory:
    def test_returns_empty_when_no_dir(self, tmp_path: Path) -> None:
        with patch("factory.profile.Path.home", return_value=tmp_path):
            result = _read_auto_memory()
        assert result == ""

    def test_reads_memory_files(self, tmp_path: Path) -> None:
        mem_dir = tmp_path / ".claude" / "projects" / "test-proj" / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "note.md").write_text("Remember this fact")
        with patch("factory.profile.Path.home", return_value=tmp_path):
            result = _read_auto_memory()
        assert "note.md" in result
        assert "Remember this fact" in result

    def test_skips_dirs_without_memory_subdir(self, tmp_path: Path) -> None:
        proj_dir = tmp_path / ".claude" / "projects" / "no-memory"
        proj_dir.mkdir(parents=True)
        (proj_dir / "other.txt").write_text("not a memory dir")
        with patch("factory.profile.Path.home", return_value=tmp_path):
            result = _read_auto_memory()
        assert result == ""

    def test_truncates_large_memory(self, tmp_path: Path) -> None:
        mem_dir = tmp_path / ".claude" / "projects" / "big-proj" / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "huge.md").write_text("x" * (_MAX_SECTION_CHARS + 500))
        with patch("factory.profile.Path.home", return_value=tmp_path):
            result = _read_auto_memory()
        assert result.endswith("... (truncated)")


class TestReadAcePlaybooks:
    def test_returns_empty_when_no_dir(self, tmp_path: Path) -> None:
        with patch("factory.profile.Path.home", return_value=tmp_path):
            result = _read_ace_playbooks()
        assert result == ""

    def test_reads_playbook_files(self, tmp_path: Path) -> None:
        pb_dir = tmp_path / ".factory" / "playbooks"
        pb_dir.mkdir(parents=True)
        (pb_dir / "ceo.md").write_text("CEO playbook rules")
        (pb_dir / "builder.md").write_text("Builder playbook rules")
        with patch("factory.profile.Path.home", return_value=tmp_path):
            result = _read_ace_playbooks()
        assert "### ceo" in result
        assert "CEO playbook rules" in result
        assert "### builder" in result
        assert "Builder playbook rules" in result

    def test_ignores_non_md_files(self, tmp_path: Path) -> None:
        pb_dir = tmp_path / ".factory" / "playbooks"
        pb_dir.mkdir(parents=True)
        (pb_dir / "notes.txt").write_text("not a playbook")
        with patch("factory.profile.Path.home", return_value=tmp_path):
            result = _read_ace_playbooks()
        assert result == ""


class TestBuildSynthesisTask:
    def test_formats_non_empty_sections(self) -> None:
        evidence = {
            "experiment_history": "exp data here",
            "ceo_verdicts": "verdict data",
        }
        result = _build_synthesis_task(evidence)
        assert "## experiment_history" in result
        assert "exp data here" in result
        assert "## ceo_verdicts" in result
        assert "verdict data" in result
        assert "(no data)" not in result

    def test_formats_empty_sections(self) -> None:
        evidence = {
            "experiment_history": "",
            "auto_memory": "  ",
        }
        result = _build_synthesis_task(evidence)
        assert "(no data)" in result

    def test_mixed_empty_and_non_empty(self) -> None:
        evidence = {
            "experiment_history": "some data",
            "ceo_verdicts": "",
            "auto_memory": "memory content",
        }
        result = _build_synthesis_task(evidence)
        assert "some data" in result
        assert "memory content" in result
        no_data_count = result.count("(no data)")
        assert no_data_count == 1

    def test_starts_with_synthesis_instruction(self) -> None:
        result = _build_synthesis_task({"a": "b"})
        assert result.startswith("Synthesize a user profile")


class TestSynthesizeProfile:
    async def test_invokes_runner(self, tmp_path: Path) -> None:
        from factory.profile import synthesize_profile

        mock_runner = AsyncMock()
        mock_runner.headless = AsyncMock(return_value=("Synthesized profile text", 0))

        with patch("factory.runners.get_runner", return_value=mock_runner), \
             patch("factory.agents.runner.resolve_prompt", return_value="profiler prompt"):
            result = await synthesize_profile({"section": "data"}, "claude")
        assert result == "Synthesized profile text"
        mock_runner.headless.assert_called_once()

    async def test_handles_failure(self, tmp_path: Path) -> None:
        from factory.profile import synthesize_profile

        mock_runner = AsyncMock()
        mock_runner.headless = AsyncMock(return_value=("Error output", 1))

        with patch("factory.runners.get_runner", return_value=mock_runner), \
             patch("factory.agents.runner.resolve_prompt", return_value="prompt"):
            result = await synthesize_profile({"section": "data"})
        assert "failed" in result.lower()
