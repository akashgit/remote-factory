"""Tests for factory digest — vault scanning and summary formatting."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from factory.cli import main
from factory.digest import (
    _parse_dashboard,
    _parse_experiment_note,
    _parse_frontmatter,
    format_digest,
    scan_vault,
)


# ── frontmatter parsing ─────────────────────────────────────


class TestParseFrontmatter:
    def test_basic(self):
        text = "---\ndate: 2026-04-11\nverdict: keep\n---\n# Hello"
        fm = _parse_frontmatter(text)
        assert fm["date"] == "2026-04-11"
        assert fm["verdict"] == "keep"

    def test_skips_list_items(self):
        text = "---\ntags:\n  - factory\n  - experiment\ndate: 2026-04-11\n---\n"
        fm = _parse_frontmatter(text)
        assert fm["date"] == "2026-04-11"
        assert fm.get("tags") == ""  # tags: line has empty value, list items skipped

    def test_no_frontmatter(self):
        assert _parse_frontmatter("# Just a heading") == {}

    def test_unclosed_frontmatter(self):
        assert _parse_frontmatter("---\ndate: 2026-04-11\n# Heading") == {}


# ── dashboard parsing ────────────────────────────────────────


class TestParseDashboard:
    def test_full_dashboard(self):
        text = (
            "## Status\n"
            "- **State**: has_factory\n"
            "- **Current Score**: 0.986\n"
            "- **Experiments Run**: 15\n"
            "- **Kept**: 14, **Reverted**: 0, **Error**: 1\n"
            "\n"
            "## Description\n"
            "A Telegram bot that bridges messages.\n"
            "\n"
            "## Architecture\n"
        )
        info = _parse_dashboard(text)
        assert info["state"] == "has_factory"
        assert info["score"] == "0.986"
        assert info["experiments"] == "15"
        assert info["kept"] == "14"
        assert info["description"] == "A Telegram bot that bridges messages."

    def test_missing_fields(self):
        info = _parse_dashboard("# Project\nSome text\n")
        assert info == {}


# ── experiment note parsing ──────────────────────────────────


class TestParseExperimentNote:
    def test_full_note(self):
        text = (
            "---\n"
            "tags:\n  - factory\n  - experiment\n"
            "date: 2026-04-11\n"
            "verdict: keep\n"
            "experiment_id: 5\n"
            "---\n\n"
            "# Experiment #5: Add test coverage\n\n"
            "## Hypothesis\nAdd test coverage\n\n"
            "## Result\n"
            "**KEEP** — score changed from 0.934 to 0.956 (+0.022)\n\n"
            "## What Changed\n"
            "12 new tests. Coverage 67% -> 78%.\n"
        )
        exp = _parse_experiment_note(text)
        assert exp["date"] == "2026-04-11"
        assert exp["verdict"] == "keep"
        assert exp["experiment_id"] == "5"
        assert exp["hypothesis"] == "Add test coverage"
        assert exp["score_before"] == "0.934"
        assert exp["score_after"] == "0.956"
        assert exp["delta"] == "+0.022"
        assert "12 new tests" in exp["summary"]

    def test_missing_fields(self):
        text = "---\ndate: 2026-04-11\n---\n# Experiment #1: Fix bug\n"
        exp = _parse_experiment_note(text)
        assert exp["date"] == "2026-04-11"
        assert exp["hypothesis"] == "Fix bug"


# ── vault scanning ───────────────────────────────────────────


def _make_experiment_note(
    project: str, exp_id: int, exp_date: str, verdict: str = "keep",
    hypothesis: str = "Test hypothesis",
) -> str:
    return (
        f"---\n"
        f"tags:\n  - factory\n  - experiment\n  - {project}\n"
        f"date: {exp_date}\n"
        f"verdict: {verdict}\n"
        f"experiment_id: {exp_id}\n"
        f"---\n\n"
        f"# Experiment #{exp_id}: {hypothesis}\n\n"
        f"## Hypothesis\n{hypothesis}\n\n"
        f"## Result\n"
        f"**{verdict.upper()}** — score changed from 0.8 to 0.9 (+0.1)\n\n"
        f"## What Changed\nSome changes were made.\n"
    )


def _make_dashboard(project: str, score: str = "0.9", experiments: int = 5) -> str:
    return (
        f"---\ntags:\n  - factory\n  - project\n  - {project}\n---\n\n"
        f"# Factory: {project}\n\n"
        f"## Status\n"
        f"- **State**: has_factory\n"
        f"- **Current Score**: {score}\n"
        f"- **Experiments Run**: {experiments}\n"
        f"- **Kept**: {experiments}, **Reverted**: 0\n\n"
        f"## Description\nA test project.\n"
    )


@pytest.fixture
def populated_vault(tmp_path: Path) -> Path:
    """Create a vault with two projects and experiments across dates."""
    vault = tmp_path / "vault"
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    old = (date.today() - timedelta(days=30)).isoformat()

    # Project A — has recent activity
    proj_a = vault / "10-Projects" / "project-a"
    (proj_a / "Experiments").mkdir(parents=True)
    (proj_a / "project-a.md").write_text(_make_dashboard("project-a", "0.95", 3))
    (proj_a / "Experiments" / "project-a-001.md").write_text(
        _make_experiment_note("project-a", 1, today, "keep", "Add auth module")
    )
    (proj_a / "Experiments" / "project-a-002.md").write_text(
        _make_experiment_note("project-a", 2, yesterday, "keep", "Fix rate limiter")
    )
    (proj_a / "Experiments" / "project-a-003.md").write_text(
        _make_experiment_note("project-a", 3, old, "revert", "Old change")
    )

    # Project B — only old activity
    proj_b = vault / "10-Projects" / "project-b"
    (proj_b / "Experiments").mkdir(parents=True)
    (proj_b / "project-b.md").write_text(_make_dashboard("project-b", "0.8", 1))
    (proj_b / "Experiments" / "project-b-001.md").write_text(
        _make_experiment_note("project-b", 1, old, "keep", "Ancient experiment")
    )

    return vault


class TestScanVault:
    def test_scan_recent(self, populated_vault: Path):
        projects = scan_vault(days=7, vault_path=populated_vault)
        assert "project-a" in projects
        # project-a should have 2 recent experiments (today + yesterday), not the old one
        exps = projects["project-a"]["experiments"]
        assert len(exps) == 2
        hypotheses = {e["hypothesis"] for e in exps}
        assert "Add auth module" in hypotheses
        assert "Fix rate limiter" in hypotheses
        assert "Old change" not in hypotheses

    def test_scan_specific_date(self, populated_vault: Path):
        today = date.today()
        projects = scan_vault(target_date=today, vault_path=populated_vault)
        exps = projects["project-a"]["experiments"]
        assert len(exps) == 1
        assert exps[0]["hypothesis"] == "Add auth module"

    def test_scan_old_date(self, populated_vault: Path):
        old = date.today() - timedelta(days=30)
        projects = scan_vault(target_date=old, vault_path=populated_vault)
        # Both projects had activity on the old date
        assert "project-a" in projects
        assert "project-b" in projects
        assert len(projects["project-a"]["experiments"]) == 1
        assert projects["project-a"]["experiments"][0]["hypothesis"] == "Old change"

    def test_scan_empty_vault(self, tmp_path: Path):
        vault = tmp_path / "empty-vault"
        vault.mkdir()
        projects = scan_vault(vault_path=vault)
        assert projects == {}

    def test_scan_no_experiments_dir(self, tmp_path: Path):
        vault = tmp_path / "vault"
        proj = vault / "10-Projects" / "bare-project"
        proj.mkdir(parents=True)
        (proj / "bare-project.md").write_text(_make_dashboard("bare-project"))
        projects = scan_vault(vault_path=vault)
        assert "bare-project" in projects
        assert projects["bare-project"]["experiments"] == []

    def test_includes_dashboard_for_inactive_projects(self, populated_vault: Path):
        """Projects with no recent experiments still appear when scanning all."""
        projects = scan_vault(days=7, vault_path=populated_vault)
        assert "project-b" in projects
        assert projects["project-b"]["experiments"] == []


# ── digest formatting ────────────────────────────────────────


class TestFormatDigest:
    def test_empty(self):
        output = format_digest({})
        assert "No projects found" in output

    def test_with_projects(self, populated_vault: Path):
        projects = scan_vault(days=7, vault_path=populated_vault)
        output = format_digest(projects)
        assert "project-a" in output
        assert "Add auth module" in output
        assert "Fix rate limiter" in output
        assert "2 experiments" in output

    def test_specific_date_title(self):
        output = format_digest({}, target_date=date(2026, 4, 11))
        assert "2026-04-11" in output

    def test_date_range_title(self):
        output = format_digest({}, days=7)
        assert " to " in output

    def test_truncates_long_summary(self):
        projects = {
            "test": {
                "dashboard": {},
                "experiments": [{
                    "experiment_id": "1",
                    "verdict": "keep",
                    "hypothesis": "Long one",
                    "delta": "+0.1",
                    "summary": "x" * 200,
                }],
            },
        }
        output = format_digest(projects)
        assert "..." in output


# ── CLI integration ──────────────────────────────────────────


class TestCmdDigest:
    def test_digest_default(self, populated_vault: Path, monkeypatch, capsys):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(populated_vault))
        result = main(["digest"])
        assert result == 0
        out = capsys.readouterr().out
        assert "Factory Digest" in out
        assert "project-a" in out

    def test_digest_specific_date(self, populated_vault: Path, monkeypatch, capsys):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(populated_vault))
        today = date.today().isoformat()
        result = main(["digest", "--date", today])
        assert result == 0
        out = capsys.readouterr().out
        assert today in out
        assert "Add auth module" in out

    def test_digest_no_vault(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "nonexistent"))
        result = main(["digest"])
        assert result == 0
        out = capsys.readouterr().out
        assert "No projects found" in out
