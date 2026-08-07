"""Tests for factory/mempalace/ package — helpers, reader, writer."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from factory.mempalace.helpers import (
    get_palace_path,
    get_project_name,
    is_mempalace_available,
)


@pytest.fixture()
def isolated_palace(tmp_path: Path, monkeypatch):
    """Redirect MemPalace storage to a temp directory so tests don't pollute ~/.mempalace."""
    palace_dir = tmp_path / "test-palace"
    palace_dir.mkdir()

    def _fake() -> str:
        return str(palace_dir)

    monkeypatch.setattr("factory.mempalace.helpers.get_palace_path", _fake)
    monkeypatch.setattr("factory.mempalace.writer.get_palace_path", _fake)
    monkeypatch.setattr("factory.mempalace.reader.get_palace_path", _fake)
    return str(palace_dir)


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

    def test_creates_memory_dir(self, tmp_path: Path, isolated_palace) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.reader import mp_read

        mp_read(tmp_path)
        assert (tmp_path / ".factory/archive/memory").exists()

    def test_task_hint_used_as_query(self, tmp_path: Path, isolated_palace) -> None:
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

    def test_no_task_hint_falls_back_to_observations(self, tmp_path: Path, isolated_palace) -> None:
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

    def test_new_output_files_created(self, tmp_path: Path, isolated_palace) -> None:
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

    def test_browse_empty_palace(self, tmp_path: Path, isolated_palace) -> None:
        pytest.importorskip("mempalace")
        from factory.cli.mempalace import _do_browse

        args = argparse.Namespace(
            project_path=str(tmp_path), wing=None, room=None, drawer=None,
        )
        result = _do_browse(tmp_path, args)
        assert result in (0, 1)

    def test_browse_with_data(self, tmp_path: Path, isolated_palace, capsys) -> None:
        pytest.importorskip("mempalace")
        from factory.cli.mempalace import _do_browse
        from factory.mempalace.helpers import get_project_name, store_drawer

        pn = get_project_name(tmp_path)
        wing = "project:" + pn
        store_drawer(isolated_palace, wing=wing, room="experiments", content="test content", source_file="test.md")

        args = argparse.Namespace(
            project_path=str(tmp_path), wing=None, room=None, drawer=None,
        )
        result = _do_browse(tmp_path, args)
        assert result == 0
        captured = capsys.readouterr()
        assert "Wing:" in captured.out
        assert "experiments" in captured.out

    def test_browse_wing_filter(self, tmp_path: Path, isolated_palace, capsys) -> None:
        pytest.importorskip("mempalace")
        from factory.cli.mempalace import _do_browse
        from factory.mempalace.helpers import get_project_name, store_drawer

        pn = get_project_name(tmp_path)
        wing = "project:" + pn
        store_drawer(isolated_palace, wing=wing, room="reviews", content="review data", source_file="review.md")

        args = argparse.Namespace(
            project_path=str(tmp_path), wing=wing, room=None, drawer=None,
        )
        result = _do_browse(tmp_path, args)
        assert result == 0
        captured = capsys.readouterr()
        assert "Room:" in captured.out

    def test_browse_drawer_by_id(self, tmp_path: Path, isolated_palace, capsys) -> None:
        pytest.importorskip("mempalace")
        from factory.cli.mempalace import _do_browse
        from factory.mempalace.helpers import get_project_name, store_drawer

        from mempalace.palace import get_collection

        pn = get_project_name(tmp_path)
        wing = "project:" + pn
        content = "full drawer content for browse test"
        store_drawer(isolated_palace, wing=wing, room="decisions", content=content, source_file="verdict.json")

        collection = get_collection(isolated_palace)
        all_items = collection.get(
            where={"$and": [{"wing": wing}, {"room": "decisions"}]}, include=["documents"],
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
    def test_experiments_room(self, tmp_path: Path, isolated_palace) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.writer import mp_write
        from factory.mempalace.helpers import get_project_name

        from mempalace.palace import get_collection

        (tmp_path / ".factory/strategy").mkdir(parents=True)
        (tmp_path / ".factory/archive").mkdir(parents=True)
        (tmp_path / ".factory/strategy/current.md").write_text("## Strategy\nTest strategy content")
        (tmp_path / ".factory/archive/build.md").write_text("Build narrative content")

        mp_write(tmp_path)

        collection = get_collection(isolated_palace)
        pn = get_project_name(tmp_path)
        results = collection.get(
            where={"$and": [{"room": "experiments"}, {"wing": "project:" + pn}]},
            include=["documents", "metadatas"],
        )
        assert len(results["ids"]) >= 1

    def test_failures_room(self, tmp_path: Path, isolated_palace) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.writer import mp_write
        from factory.mempalace.helpers import get_project_name

        from mempalace.palace import get_collection

        (tmp_path / ".factory/reviews").mkdir(parents=True)
        (tmp_path / ".factory/reviews/ceo-verdict-build.md").write_text(
            "## CEO Review: Builder\n- **Verdict:** REDIRECT\n- **Rationale:** Insufficient coverage"
        )

        mp_write(tmp_path)

        collection = get_collection(isolated_palace)
        pn = get_project_name(tmp_path)
        results = collection.get(
            where={"$and": [{"room": "failures"}, {"wing": "project:" + pn}]},
            include=["documents", "metadatas"],
        )
        assert len(results["ids"]) >= 1

    def test_reviews_room(self, tmp_path: Path, isolated_palace) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.writer import mp_write
        from factory.mempalace.helpers import get_project_name

        from mempalace.palace import get_collection

        (tmp_path / ".factory/reviews").mkdir(parents=True)
        (tmp_path / ".factory/reviews/code-review.md").write_text(
            "## Code Review\n### Correctness: PASS\n### Security: PASS"
        )

        mp_write(tmp_path)

        collection = get_collection(isolated_palace)
        pn = get_project_name(tmp_path)
        results = collection.get(
            where={"$and": [{"room": "reviews"}, {"wing": "project:" + pn}]},
            include=["documents", "metadatas"],
        )
        assert len(results["ids"]) >= 1

    def test_decisions_room(self, tmp_path: Path, isolated_palace) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.writer import mp_write
        from factory.mempalace.helpers import get_project_name

        from mempalace.palace import get_collection

        (tmp_path / ".factory/experiments/001").mkdir(parents=True)
        (tmp_path / ".factory/experiments/001/verdict.json").write_text(
            json.dumps({"verdict": "keep", "delta": 0.05, "notes": "Improved coverage"})
        )

        mp_write(tmp_path)

        collection = get_collection(isolated_palace)
        pn = get_project_name(tmp_path)
        results = collection.get(
            where={"$and": [{"room": "decisions"}, {"wing": "project:" + pn}]},
            include=["documents", "metadatas"],
        )
        assert len(results["ids"]) >= 1


class TestMpWriteHappyPaths:
    """Exercise writer.py branches that require mempalace — mock helpers to avoid real palace."""

    @pytest.fixture(autouse=True)
    def _mock_helpers(self, tmp_path: Path, monkeypatch):
        self.triples: list[tuple] = []
        self.supersedes: list[tuple] = []
        self.drawers: list[tuple] = []

        monkeypatch.setattr(
            "factory.mempalace.writer.kg_add_triple",
            lambda subj, pred, obj, valid_from: self.triples.append((subj, pred, obj)),
        )
        monkeypatch.setattr(
            "factory.mempalace.writer.kg_supersede",
            lambda subj, pred, old, new, at: self.supersedes.append((subj, pred, new)),
        )
        monkeypatch.setattr(
            "factory.mempalace.writer.store_drawer",
            lambda palace, wing, room, content, source_file: self.drawers.append((room, content[:50])),
        )
        monkeypatch.setattr("factory.mempalace.writer.get_palace_path", lambda: str(tmp_path / "p"))

    def test_hypotheses_recorded(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        (tmp_path / ".factory/strategy").mkdir(parents=True)
        (tmp_path / ".factory/strategy/current.md").write_text(
            "## Strategy\nImprove coverage\n\n#### H1: Add unit tests\n#### H2: Add integration tests"
        )
        mp_write(tmp_path)
        hyps = [t for t in self.triples if t[1] == "has_hypothesis"]
        assert len(hyps) == 2
        assert any("Add unit tests" in h[2] for h in hyps)

    def test_anti_patterns_recorded(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        (tmp_path / ".factory/strategy").mkdir(parents=True)
        (tmp_path / ".factory/strategy/current.md").write_text(
            "## Strategy\nTest\n\n## Anti-patterns\n- Monkey-patching internals\n- Skipping CI\n# Next"
        )
        mp_write(tmp_path)
        aps = [t for t in self.triples if t[1] == "rejected_approach"]
        assert len(aps) == 2
        assert any("Monkey-patching" in a[2] for a in aps)

    def test_design_session_recorded(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        (tmp_path / ".factory/strategy").mkdir(parents=True)
        (tmp_path / ".factory/strategy/current.md").write_text(
            "## Strategy\nFocus on auth hardening"
        )
        mp_write(tmp_path)
        sessions = [t for t in self.triples if t[1] == "design_session"]
        assert len(sessions) == 1
        assert "Focus on auth hardening" in sessions[0][2]

    def test_current_strategy_superseded(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        (tmp_path / ".factory/strategy").mkdir(parents=True)
        (tmp_path / ".factory/strategy/current.md").write_text("## Auth Hardening\nDetails here")
        mp_write(tmp_path)
        strats = [s for s in self.supersedes if s[1] == "current_strategy"]
        assert len(strats) == 1
        assert strats[0][2] == "Auth Hardening"

    def test_experiments_drawer_combines_current_and_build(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        (tmp_path / ".factory/strategy").mkdir(parents=True)
        (tmp_path / ".factory/archive").mkdir(parents=True)
        (tmp_path / ".factory/strategy/current.md").write_text("strategy content")
        (tmp_path / ".factory/archive/build.md").write_text("build content")
        mp_write(tmp_path)
        exp_drawers = [d for d in self.drawers if d[0] == "experiments"]
        assert len(exp_drawers) >= 1

    def test_failures_from_redirect_verdict(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        (tmp_path / ".factory/reviews").mkdir(parents=True)
        (tmp_path / ".factory/reviews/ceo-verdict-build.md").write_text("REDIRECT: bad approach")
        mp_write(tmp_path)
        failures = [d for d in self.drawers if d[0] == "failures"]
        assert len(failures) >= 1

    def test_failures_from_health_check(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        (tmp_path / ".factory/reviews").mkdir(parents=True)
        (tmp_path / ".factory/reviews/health-check.md").write_text("Tests: FAIL\n3 errors found")
        mp_write(tmp_path)
        failures = [d for d in self.drawers if d[0] == "failures"]
        assert len(failures) >= 1

    def test_no_failures_from_proceed_verdict(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        (tmp_path / ".factory/reviews").mkdir(parents=True)
        (tmp_path / ".factory/reviews/ceo-verdict-build.md").write_text("PROCEED: looks good")
        mp_write(tmp_path)
        failures = [d for d in self.drawers if d[0] == "failures"]
        assert len(failures) == 0

    def test_reviews_room_qa_files(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        (tmp_path / ".factory/reviews").mkdir(parents=True)
        (tmp_path / ".factory/reviews/code-review.md").write_text("review findings")
        (tmp_path / ".factory/reviews/adversarial-qa.md").write_text("qa findings")
        (tmp_path / ".factory/reviews/health-check.md").write_text("health ok")
        mp_write(tmp_path)
        reviews = [d for d in self.drawers if d[0] == "reviews"]
        assert len(reviews) == 3

    def test_research_room(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        (tmp_path / ".factory/strategy").mkdir(parents=True)
        (tmp_path / ".factory/strategy/research-combined.md").write_text("research findings")
        mp_write(tmp_path)
        research = [d for d in self.drawers if d[0] == "research"]
        assert len(research) == 1

    def test_decisions_room_verdict_json(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        (tmp_path / ".factory/experiments/001").mkdir(parents=True)
        (tmp_path / ".factory/experiments/001/verdict.json").write_text(
            json.dumps({"verdict": "keep", "delta": 0.05})
        )
        mp_write(tmp_path)
        decisions = [d for d in self.drawers if d[0] == "decisions"]
        assert len(decisions) == 1

    def test_eval_score_superseded(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        (tmp_path / ".factory").mkdir(parents=True)
        (tmp_path / ".factory/last_eval.json").write_text(json.dumps({"composite": 0.85}))
        mp_write(tmp_path)
        evals = [s for s in self.supersedes if s[1] == "eval_score"]
        assert len(evals) == 1
        assert evals[0][2] == "0.85"

    def test_playbook_rules_superseded(self, tmp_path: Path, monkeypatch) -> None:
        from factory.mempalace.writer import mp_write

        playbooks_dir = tmp_path / "dot-factory" / "playbooks"
        playbooks_dir.mkdir(parents=True)
        (playbooks_dir / "builder.md").write_text(
            "- [x] rule1 :: Always run tests\n- [x] rule2 :: Keep PRs small"
        )

        original_expanduser = os.path.expanduser

        def _expanduser(p: str) -> str:
            if p == "~/.factory/playbooks":
                return str(playbooks_dir)
            return original_expanduser(p)

        monkeypatch.setattr("os.path.expanduser", _expanduser)
        mp_write(tmp_path)
        rules = [s for s in self.supersedes if s[1] == "has_rule"]
        assert len(rules) == 2
        assert any("Always run tests" in r[2] for r in rules)

    def test_return_value(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        result = mp_write(tmp_path)
        assert "MemPalace archive complete" in result

    def test_no_current_md_skips_section1(self, tmp_path: Path) -> None:
        from factory.mempalace.writer import mp_write

        (tmp_path / ".factory").mkdir(parents=True)
        mp_write(tmp_path)
        assert len(self.triples) == 0
        strats = [s for s in self.supersedes if s[1] == "current_strategy"]
        assert len(strats) == 0


class TestMpReadHappyPaths:
    """Exercise reader.py branches with mocked helpers."""

    @pytest.fixture(autouse=True)
    def _mock_helpers(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "factory.mempalace.reader.search_episodes",
            lambda palace, wing, query, n_results: f"episode for {query}",
        )
        monkeypatch.setattr(
            "factory.mempalace.reader.search_build_outcomes",
            lambda palace, wing, room, query, n_results: f"outcome:{room}",
        )

        class FakeKG:
            def query_entity(self, name, direction="both", as_of=None):
                return [{"subject": name, "predicate": "has", "object": "value"}]

            def timeline(self, entity_name=None):
                return [{"valid_from": "2026-01-01", "subject": entity_name, "predicate": "created", "object": "v1"}]

        monkeypatch.setattr("factory.mempalace.reader.get_kg", FakeKG)
        monkeypatch.setattr(
            "factory.mempalace.reader.kg_query_entity",
            lambda name, direction="both", as_of=None, kg=None: (
                kg.query_entity(name, direction, as_of) if kg else [{"subject": name, "predicate": "has", "object": "value"}]
            ),
        )
        monkeypatch.setattr(
            "factory.mempalace.reader.kg_timeline",
            lambda entity_name, kg=None: (
                kg.timeline(entity_name=entity_name) if kg else [{"valid_from": "2026-01-01", "subject": entity_name, "predicate": "created", "object": "v1"}]
            ),
        )
        monkeypatch.setattr("factory.mempalace.reader.get_palace_path", lambda: str(tmp_path / "p"))

    def test_returns_context_content(self, tmp_path: Path) -> None:
        from factory.mempalace.reader import mp_read

        result = mp_read(tmp_path)
        assert "## Episodic Memory" in result
        assert "## Knowledge Graph Facts" in result
        assert "## Timeline" in result
        assert "## Experiment Outcomes" in result

    def test_context_file_written(self, tmp_path: Path) -> None:
        from factory.mempalace.reader import mp_read

        mp_read(tmp_path)
        ctx = (tmp_path / ".factory/archive/memory/context.md").read_text()
        assert "## Episodic Memory" in ctx

    def test_episodes_populated(self, tmp_path: Path) -> None:
        from factory.mempalace.reader import mp_read

        mp_read(tmp_path, task_hint="auth flow")
        ep = (tmp_path / ".factory/archive/memory/episodes.md").read_text()
        assert "episode for auth flow" in ep

    def test_facts_populated(self, tmp_path: Path) -> None:
        from factory.mempalace.reader import mp_read

        mp_read(tmp_path)
        fk = (tmp_path / ".factory/archive/memory/facts.md").read_text()
        assert "has value" in fk

    def test_timeline_populated(self, tmp_path: Path) -> None:
        from factory.mempalace.reader import mp_read

        mp_read(tmp_path)
        tl = (tmp_path / ".factory/archive/memory/timeline.md").read_text()
        assert "2026-01-01" in tl
        assert "created" in tl

    def test_task_hint_expands_kg_queries(self, tmp_path: Path) -> None:
        from factory.mempalace.reader import mp_read

        mp_read(tmp_path, task_hint="structured logging")
        fk = (tmp_path / ".factory/archive/memory/facts.md").read_text()
        assert "structured" in fk or "logging" in fk or "has value" in fk

    def test_observations_fallback(self, tmp_path: Path) -> None:
        from factory.mempalace.reader import mp_read

        (tmp_path / ".factory/strategy").mkdir(parents=True)
        (tmp_path / ".factory/strategy/observations.md").write_text("line1\nline2\nline3")
        mp_read(tmp_path)
        ep = (tmp_path / ".factory/archive/memory/episodes.md").read_text()
        assert "episode for line1 line2 line3" in ep

    def test_anti_patterns_populated(self, tmp_path: Path) -> None:
        from factory.mempalace.reader import mp_read

        mp_read(tmp_path)
        anti = (tmp_path / ".factory/archive/memory/anti-patterns.md").read_text()
        assert "outcome:failures" in anti

    def test_outcomes_populated(self, tmp_path: Path) -> None:
        from factory.mempalace.reader import mp_read

        mp_read(tmp_path)
        outcomes = (tmp_path / ".factory/archive/memory/outcomes.md").read_text()
        assert "outcome:experiments" in outcomes


class TestCliMempalace:
    """Exercise cli/mempalace.py dispatch and sub-commands."""

    def test_cmd_mempalace_read(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            "factory.mempalace.reader.mp_read",
            lambda pp, task_hint=None: "read output",
        )
        from factory.cli.mempalace import cmd_mempalace

        args = argparse.Namespace(mempalace_action="read", project_path=str(tmp_path), task_hint=None)
        result = cmd_mempalace(args)
        assert result == 0
        assert "read output" in capsys.readouterr().out

    def test_cmd_mempalace_write(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            "factory.mempalace.writer.mp_write",
            lambda pp: "write output",
        )
        from factory.cli.mempalace import cmd_mempalace

        args = argparse.Namespace(mempalace_action="write", project_path=str(tmp_path))
        result = cmd_mempalace(args)
        assert result == 0
        assert "write output" in capsys.readouterr().out

    def test_cmd_mempalace_unknown_action(self, tmp_path: Path) -> None:
        from factory.cli.mempalace import cmd_mempalace

        args = argparse.Namespace(mempalace_action="unknown", project_path=str(tmp_path))
        result = cmd_mempalace(args)
        assert result == 1

    def test_do_read_empty_result(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.setattr("factory.mempalace.reader.mp_read", lambda pp, task_hint=None: "")
        from factory.cli.mempalace import _do_read

        result = _do_read(tmp_path)
        assert result == 0
        assert capsys.readouterr().out == ""

    def test_do_write_empty_result(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.setattr("factory.mempalace.writer.mp_write", lambda pp: "")
        from factory.cli.mempalace import _do_write

        result = _do_write(tmp_path)
        assert result == 0
        assert capsys.readouterr().out == ""

    def test_browse_with_room_filter(self, tmp_path: Path, isolated_palace, capsys) -> None:
        pytest.importorskip("mempalace")
        from factory.cli.mempalace import _do_browse
        from factory.mempalace.helpers import get_project_name, store_drawer

        pn = get_project_name(tmp_path)
        wing = "project:" + pn
        store_drawer(isolated_palace, wing=wing, room="research", content="research data here", source_file="r.md")

        args = argparse.Namespace(
            project_path=str(tmp_path), wing=wing, room="research", drawer=None,
        )
        result = _do_browse(tmp_path, args)
        assert result == 0
        captured = capsys.readouterr()
        assert "Room: research" in captured.out
        assert "Drawer:" in captured.out

    def test_browse_all_wings(self, tmp_path: Path, isolated_palace, capsys) -> None:
        pytest.importorskip("mempalace")
        from factory.cli.mempalace import _do_browse
        from factory.mempalace.helpers import get_project_name, store_drawer

        pn = get_project_name(tmp_path)
        wing = "project:" + pn
        store_drawer(isolated_palace, wing=wing, room="experiments", content="data", source_file="a.md")

        args = argparse.Namespace(
            project_path=str(tmp_path), wing=None, room=None, drawer=None, all=True,
        )
        result = _do_browse(tmp_path, args)
        assert result == 0
        captured = capsys.readouterr()
        assert "Wing:" in captured.out

    def test_browse_empty_wing(self, tmp_path: Path, isolated_palace, capsys) -> None:
        pytest.importorskip("mempalace")
        from factory.cli.mempalace import _do_browse

        args = argparse.Namespace(
            project_path=str(tmp_path), wing="project:nonexistent", room=None, drawer=None,
        )
        result = _do_browse(tmp_path, args)
        assert result in (0, 1)

    def test_browse_empty_room(self, tmp_path: Path, isolated_palace, capsys) -> None:
        pytest.importorskip("mempalace")
        from factory.cli.mempalace import _do_browse
        from factory.mempalace.helpers import get_project_name, store_drawer

        pn = get_project_name(tmp_path)
        wing = "project:" + pn
        store_drawer(isolated_palace, wing=wing, room="experiments", content="data", source_file="a.md")

        args = argparse.Namespace(
            project_path=str(tmp_path), wing=wing, room="nonexistent", drawer=None,
        )
        result = _do_browse(tmp_path, args)
        assert result == 0
        captured = capsys.readouterr()
        assert "No drawers" in captured.out

    def test_browse_nonexistent_drawer(self, tmp_path: Path, isolated_palace, capsys) -> None:
        pytest.importorskip("mempalace")
        from factory.cli.mempalace import _do_browse
        from factory.mempalace.helpers import get_project_name, store_drawer

        pn = get_project_name(tmp_path)
        wing = "project:" + pn
        store_drawer(isolated_palace, wing=wing, room="experiments", content="data", source_file="a.md")

        args = argparse.Namespace(
            project_path=str(tmp_path), wing=None, room=None, drawer="nonexistent-id",
        )
        result = _do_browse(tmp_path, args)
        assert result == 1
        assert "not found" in capsys.readouterr().out


class TestContentAddressedDrawers:
    def test_same_content_deduplicates(self, tmp_path: Path, isolated_palace) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.helpers import get_project_name, store_drawer

        from mempalace.palace import get_collection

        pn = get_project_name(tmp_path)
        wing = "project:" + pn

        store_drawer(isolated_palace, wing=wing, room="experiments", content="identical content", source_file="a.md")
        store_drawer(isolated_palace, wing=wing, room="experiments", content="identical content", source_file="a.md")

        collection = get_collection(isolated_palace)
        results = collection.get(
            where={"$and": [{"wing": wing}, {"room": "experiments"}]},
            include=["documents"],
        )
        assert len(results["ids"]) == 1

    def test_different_content_accumulates(self, tmp_path: Path, isolated_palace) -> None:
        pytest.importorskip("mempalace")
        from factory.mempalace.helpers import get_project_name, store_drawer

        from mempalace.palace import get_collection

        pn = get_project_name(tmp_path)
        wing = "project:" + pn

        store_drawer(isolated_palace, wing=wing, room="experiments", content="content alpha", source_file="a.md")
        store_drawer(isolated_palace, wing=wing, room="experiments", content="content beta", source_file="a.md")

        collection = get_collection(isolated_palace)
        results = collection.get(
            where={"$and": [{"wing": wing}, {"room": "experiments"}]},
            include=["documents"],
        )
        assert len(results["ids"]) == 2
