"""Tests for factory/mempalace/ package — helpers, reader, writer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from factory.mempalace.helpers import (
    get_palace_path,
    get_project_name,
    is_mempalace_available,
)


class TestHelpers:
    def test_get_palace_path_returns_string(self) -> None:
        result = get_palace_path()
        assert isinstance(result, str)
        assert result.endswith("palace")

    def test_get_project_name(self, tmp_path: Path) -> None:
        result = get_project_name(tmp_path)
        resolved = tmp_path.resolve().as_posix().replace(" ", "_")
        assert result == resolved

    def test_get_project_name_spaces_replaced(self) -> None:
        p = Path("/Users/sbaig/Documents/AI Innovation/calculator")
        result = get_project_name(p)
        assert " " not in result
        assert "/Users/sbaig/Documents/AI_Innovation/calculator" in result

    def test_get_project_name_preserves_case(self) -> None:
        p = Path("/tmp/MyProject")
        result = get_project_name(p)
        assert "MyProject" in result

    def test_is_mempalace_available_returns_bool(self) -> None:
        result = is_mempalace_available()
        assert isinstance(result, bool)


class TestExtractTaskTerms:
    def test_filters_short_words(self) -> None:
        from factory.mempalace.reader import _extract_task_terms

        result = _extract_task_terms("add structured logging to the app")
        assert "add" not in result
        assert "the" not in result
        assert "structured" in result
        assert "logging" in result

    def test_max_terms_cap(self) -> None:
        from factory.mempalace.reader import _extract_task_terms

        result = _extract_task_terms("alpha beta gamma delta epsilon zeta theta iota", max_terms=3)
        assert len(result) == 3

    def test_lowercases(self) -> None:
        from factory.mempalace.reader import _extract_task_terms

        result = _extract_task_terms("Structured Logging")
        assert all(t == t.lower() for t in result)


class TestMpRead:
    def test_graceful_degradation(self, tmp_path: Path) -> None:
        from factory.mempalace.reader import mp_read

        result = mp_read(tmp_path)
        assert isinstance(result, str)

    def test_graceful_degradation_with_task_hint(self, tmp_path: Path) -> None:
        from factory.mempalace.reader import mp_read

        result = mp_read(tmp_path, task_hint="add structured logging")
        assert isinstance(result, str)

    def test_creates_memory_dir(self, tmp_path: Path) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.reader import mp_read

        mp_read(tmp_path)
        assert (tmp_path / ".factory/archive/memory").exists()

    def test_task_hint_used_as_query(self, tmp_path: Path) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.reader import mp_read

        mp_read(tmp_path, task_hint="add structured logging")
        memory_dir = tmp_path / ".factory/archive/memory"
        assert memory_dir.exists()
        assert (memory_dir / "episodes.md").exists()
        assert (memory_dir / "anti-patterns.md").exists()
        assert (memory_dir / "reviews.md").exists()
        assert (memory_dir / "decisions.md").exists()
        assert (memory_dir / "context.md").exists()
        ctx = (memory_dir / "context.md").read_text()
        assert "## Episodic Memory (Task-Relevant)" in ctx
        assert "## Past QA Findings" in ctx
        assert "## Design Rationale" in ctx
        assert "## Anti-Patterns & Past Failures" in ctx
        assert "## Knowledge Graph Facts" in ctx
        assert "## Experiment Outcomes" in ctx

    def test_no_task_hint_falls_back_to_observations(self, tmp_path: Path) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.reader import mp_read

        mp_read(tmp_path)
        memory_dir = tmp_path / ".factory/archive/memory"
        assert (memory_dir / "context.md").exists()
        ctx = (memory_dir / "context.md").read_text()
        assert "## Episodic Memory (Task-Relevant)" in ctx
        assert "## Past QA Findings" in ctx
        assert "## Design Rationale" in ctx
        assert "## Anti-Patterns & Past Failures" in ctx

    def test_new_output_files_created(self, tmp_path: Path) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.reader import mp_read

        mp_read(tmp_path, task_hint="auth flow tradeoffs")
        memory_dir = tmp_path / ".factory/archive/memory"
        assert (memory_dir / "reviews.md").exists()
        assert (memory_dir / "decisions.md").exists()
        assert (memory_dir / "outcomes.md").exists()


class TestMpWrite:
    def test_graceful_degradation(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        result = mp_write(tmp_path)
        assert isinstance(result, str)

    def test_noop_without_mempalace(self, tmp_path: Path, monkeypatch) -> None:
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("mempalace"):
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        from factory.mempalace.writer import mp_write

        result = mp_write(tmp_path)
        assert result == ""


class TestMempalaceBrowse:
    def test_browse_no_mempalace(self, tmp_path: Path, monkeypatch) -> None:
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("mempalace"):
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        from factory.cli.mempalace import _do_browse

        args = argparse.Namespace(
            project_path=str(tmp_path), wing=None, room=None, drawer=None,
        )
        result = _do_browse(tmp_path, args)
        assert result == 1

    def test_browse_empty_palace(self, tmp_path: Path) -> None:
        pytest.importorskip("mempalace")
        from factory.cli.mempalace import _do_browse

        args = argparse.Namespace(
            project_path=str(tmp_path), wing=None, room=None, drawer=None,
        )
        result = _do_browse(tmp_path, args)
        assert result in (0, 1)

    def test_browse_with_data(self, tmp_path: Path, capsys) -> None:
        pytest.importorskip("mempalace")
        from factory.cli.mempalace import _do_browse
        from factory.mempalace.helpers import get_palace_path, get_project_name, store_drawer

        palace = get_palace_path()
        pn = get_project_name(tmp_path)
        wing = "project:" + pn
        store_drawer(palace, wing=wing, room="experiments", content="test content", source_file="test.md")

        args = argparse.Namespace(
            project_path=str(tmp_path), wing=None, room=None, drawer=None,
        )
        result = _do_browse(tmp_path, args)
        assert result == 0
        captured = capsys.readouterr()
        assert "Wing:" in captured.out
        assert "experiments" in captured.out

    def test_browse_wing_filter(self, tmp_path: Path, capsys) -> None:
        pytest.importorskip("mempalace")
        from factory.cli.mempalace import _do_browse
        from factory.mempalace.helpers import get_palace_path, get_project_name, store_drawer

        palace = get_palace_path()
        pn = get_project_name(tmp_path)
        wing = "project:" + pn
        store_drawer(palace, wing=wing, room="reviews", content="review data", source_file="review.md")

        args = argparse.Namespace(
            project_path=str(tmp_path), wing=wing, room=None, drawer=None,
        )
        result = _do_browse(tmp_path, args)
        assert result == 0
        captured = capsys.readouterr()
        assert "Room:" in captured.out

    def test_browse_drawer_by_id(self, tmp_path: Path, capsys) -> None:
        pytest.importorskip("mempalace")
        from factory.cli.mempalace import _do_browse
        from factory.mempalace.helpers import get_palace_path, get_project_name, store_drawer

        from mempalace.palace import get_collection

        palace = get_palace_path()
        pn = get_project_name(tmp_path)
        wing = "project:" + pn
        content = "full drawer content for browse test"
        store_drawer(palace, wing=wing, room="decisions", content=content, source_file="verdict.json")

        collection = get_collection(palace)
        all_items = collection.get(
            where={"wing": wing, "room": "decisions"}, include=["documents"],
        )
        assert all_items["ids"], "Expected at least one drawer"
        drawer_id = all_items["ids"][0]

        args = argparse.Namespace(
            project_path=str(tmp_path), wing=None, room=None, drawer=drawer_id,
        )
        result = _do_browse(tmp_path, args)
        assert result == 0
        captured = capsys.readouterr()
        assert content in captured.out
        assert "Drawer:" in captured.out


class TestMpWriteRooms:
    def test_experiments_room(self, tmp_path: Path) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.writer import mp_write
        from factory.mempalace.helpers import get_palace_path, get_project_name

        from mempalace.palace import get_collection

        (tmp_path / ".factory/strategy").mkdir(parents=True)
        (tmp_path / ".factory/archive").mkdir(parents=True)
        (tmp_path / ".factory/strategy/current.md").write_text("## Strategy\nTest strategy content")
        (tmp_path / ".factory/archive/build.md").write_text("Build narrative content")

        mp_write(tmp_path)

        palace = get_palace_path()
        collection = get_collection(palace)
        pn = get_project_name(tmp_path)
        results = collection.get(
            where={"room": "experiments", "wing": "project:" + pn},
            include=["documents", "metadatas"],
        )
        assert len(results["ids"]) >= 1

    def test_failures_room(self, tmp_path: Path) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.writer import mp_write
        from factory.mempalace.helpers import get_palace_path, get_project_name

        from mempalace.palace import get_collection

        (tmp_path / ".factory/reviews").mkdir(parents=True)
        (tmp_path / ".factory/reviews/ceo-verdict-build.md").write_text(
            "## CEO Review: Builder\n- **Verdict:** REDIRECT\n- **Rationale:** Insufficient coverage"
        )

        mp_write(tmp_path)

        palace = get_palace_path()
        collection = get_collection(palace)
        pn = get_project_name(tmp_path)
        results = collection.get(
            where={"room": "failures", "wing": "project:" + pn},
            include=["documents", "metadatas"],
        )
        assert len(results["ids"]) >= 1

    def test_reviews_room(self, tmp_path: Path) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.writer import mp_write
        from factory.mempalace.helpers import get_palace_path, get_project_name

        from mempalace.palace import get_collection

        (tmp_path / ".factory/reviews").mkdir(parents=True)
        (tmp_path / ".factory/reviews/code-review.md").write_text(
            "## Code Review\n### Correctness: PASS\n### Security: PASS"
        )

        mp_write(tmp_path)

        palace = get_palace_path()
        collection = get_collection(palace)
        pn = get_project_name(tmp_path)
        results = collection.get(
            where={"room": "reviews", "wing": "project:" + pn},
            include=["documents", "metadatas"],
        )
        assert len(results["ids"]) >= 1

    def test_decisions_room(self, tmp_path: Path) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.writer import mp_write
        from factory.mempalace.helpers import get_palace_path, get_project_name

        from mempalace.palace import get_collection

        (tmp_path / ".factory/experiments/001").mkdir(parents=True)
        (tmp_path / ".factory/experiments/001/verdict.json").write_text(
            json.dumps({"verdict": "keep", "delta": 0.05, "notes": "Improved coverage"})
        )

        mp_write(tmp_path)

        palace = get_palace_path()
        collection = get_collection(palace)
        pn = get_project_name(tmp_path)
        results = collection.get(
            where={"room": "decisions", "wing": "project:" + pn},
            include=["documents", "metadatas"],
        )
        assert len(results["ids"]) >= 1


class TestContentAddressedDrawers:
    def test_same_content_deduplicates(self, tmp_path: Path) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.helpers import get_palace_path, get_project_name, store_drawer

        from mempalace.palace import get_collection

        palace = get_palace_path()
        pn = get_project_name(tmp_path)
        wing = "project:" + pn

        store_drawer(palace, wing=wing, room="experiments", content="identical content", source_file="a.md")
        store_drawer(palace, wing=wing, room="experiments", content="identical content", source_file="a.md")

        collection = get_collection(palace)
        results = collection.get(
            where={"wing": wing, "room": "experiments"},
            include=["documents"],
        )
        assert len(results["ids"]) == 1

    def test_different_content_accumulates(self, tmp_path: Path) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.helpers import get_palace_path, get_project_name, store_drawer

        from mempalace.palace import get_collection

        palace = get_palace_path()
        pn = get_project_name(tmp_path)
        wing = "project:" + pn

        store_drawer(palace, wing=wing, room="experiments", content="content alpha", source_file="a.md")
        store_drawer(palace, wing=wing, room="experiments", content="content beta", source_file="a.md")

        collection = get_collection(palace)
        results = collection.get(
            where={"wing": wing, "room": "experiments"},
            include=["documents"],
        )
        assert len(results["ids"]) == 2
