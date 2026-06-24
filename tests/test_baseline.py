"""Tests for factory.baseline — eval baseline retrieval from benchmark-data branch."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

from factory.baseline import (
    _entry_to_composite,
    _find_base_commit,
    _find_entry_for_commit,
    _find_latest_main_entry,
    _parse_jsonl,
    _read_jsonl_from_branch,
    get_baseline,
    get_latest_main_baseline,
)


# ── fixtures ──────────────────────────────────────────────────────


SAMPLE_ENTRY = {
    "type": "eval",
    "commit": "abc123def456",
    "ref": "refs/heads/main",
    "timestamp": "2026-06-24T00:00:00Z",
    "run_id": "12345",
    "run_url": "https://github.com/owner/repo/actions/runs/12345",
    "trigger": "push",
    "total": 0.638,
    "passed": True,
    "threshold": 0.6,
    "results": [
        {"name": "tests", "score": 1.0, "weight": 0.15, "passed": True, "details": "OK"},
        {"name": "lint", "score": 0.9, "weight": 0.075, "passed": False, "details": "1 error"},
    ],
    "guard_violations": [],
}

SAMPLE_ENTRY_2 = {
    "type": "eval",
    "commit": "deadbeef1234",
    "ref": "refs/heads/main",
    "timestamp": "2026-06-25T00:00:00Z",
    "run_id": "12346",
    "run_url": "https://github.com/owner/repo/actions/runs/12346",
    "trigger": "push",
    "total": 0.700,
    "passed": True,
    "threshold": 0.6,
    "results": [
        {"name": "tests", "score": 1.0, "weight": 0.15, "passed": True, "details": "OK"},
        {"name": "lint", "score": 1.0, "weight": 0.075, "passed": True, "details": "clean"},
    ],
    "guard_violations": [],
}

SAMPLE_PR_ENTRY = {
    "type": "eval",
    "commit": "featurebranch1",
    "ref": "refs/heads/feature-x",
    "timestamp": "2026-06-24T12:00:00Z",
    "run_id": "99999",
    "total": 0.5,
    "passed": False,
    "results": [],
}


def _make_jsonl(*entries: dict) -> str:
    return "\n".join(json.dumps(e) for e in entries) + "\n"


# ── _parse_jsonl ──────────────────────────────────────────────────


class TestParseJsonl:
    def test_valid_lines(self):
        content = _make_jsonl(SAMPLE_ENTRY, SAMPLE_ENTRY_2)
        entries = _parse_jsonl(content)
        assert len(entries) == 2
        assert entries[0]["commit"] == "abc123def456"
        assert entries[1]["commit"] == "deadbeef1234"

    def test_empty_string(self):
        assert _parse_jsonl("") == []

    def test_blank_lines_skipped(self):
        content = json.dumps(SAMPLE_ENTRY) + "\n\n\n" + json.dumps(SAMPLE_ENTRY_2)
        entries = _parse_jsonl(content)
        assert len(entries) == 2

    def test_malformed_lines_skipped(self):
        content = json.dumps(SAMPLE_ENTRY) + "\n{bad json}\n" + json.dumps(SAMPLE_ENTRY_2)
        entries = _parse_jsonl(content)
        assert len(entries) == 2

    def test_all_malformed(self):
        assert _parse_jsonl("not json\nalso bad\n") == []


# ── _find_entry_for_commit ────────────────────────────────────────


class TestFindEntryForCommit:
    def test_exact_match(self):
        entries = [SAMPLE_ENTRY, SAMPLE_ENTRY_2]
        result = _find_entry_for_commit(entries, "abc123def456")
        assert result is not None
        assert result["commit"] == "abc123def456"

    def test_prefix_match(self):
        entries = [SAMPLE_ENTRY, SAMPLE_ENTRY_2]
        result = _find_entry_for_commit(entries, "abc123")
        assert result is not None
        assert result["commit"] == "abc123def456"

    def test_no_match(self):
        entries = [SAMPLE_ENTRY, SAMPLE_ENTRY_2]
        assert _find_entry_for_commit(entries, "nonexistent") is None

    def test_returns_latest_on_multiple_matches(self):
        dup = {**SAMPLE_ENTRY, "total": 0.999}
        entries = [SAMPLE_ENTRY, dup]
        result = _find_entry_for_commit(entries, "abc123def456")
        assert result is not None
        assert result["total"] == 0.999

    def test_empty_entries(self):
        assert _find_entry_for_commit([], "abc123") is None

    def test_empty_commit_field_skipped(self):
        bad_entry = {**SAMPLE_ENTRY, "commit": ""}
        entries = [bad_entry, SAMPLE_ENTRY_2]
        result = _find_entry_for_commit(entries, "abc123")
        assert result is None

    def test_missing_commit_field_skipped(self):
        bad_entry = {k: v for k, v in SAMPLE_ENTRY.items() if k != "commit"}
        entries = [bad_entry]
        assert _find_entry_for_commit(entries, "anything") is None


# ── _find_latest_main_entry ──────────────────────────────────────


class TestFindLatestMainEntry:
    def test_finds_latest(self):
        entries = [SAMPLE_ENTRY, SAMPLE_PR_ENTRY, SAMPLE_ENTRY_2]
        result = _find_latest_main_entry(entries)
        assert result is not None
        assert result["commit"] == "deadbeef1234"

    def test_skips_non_main(self):
        entries = [SAMPLE_PR_ENTRY]
        assert _find_latest_main_entry(entries) is None

    def test_empty_entries(self):
        assert _find_latest_main_entry([]) is None

    def test_master_branch(self):
        master_entry = {**SAMPLE_ENTRY, "ref": "refs/heads/master"}
        entries = [master_entry]
        result = _find_latest_main_entry(entries)
        assert result is not None


# ── _entry_to_composite ──────────────────────────────────────────


class TestEntryToComposite:
    def test_converts_correctly(self):
        score = _entry_to_composite(SAMPLE_ENTRY)
        assert score.total == 0.638
        assert score.passed is True
        assert len(score.results) == 2
        assert score.results[0].name == "tests"
        assert score.results[0].score == 1.0
        assert score.guard_violations == []

    def test_missing_fields_use_defaults(self):
        minimal = {"total": 0.5}
        score = _entry_to_composite(minimal)
        assert score.total == 0.5
        assert score.passed is False
        assert score.results == []
        assert score.guard_violations == []

    def test_empty_entry(self):
        score = _entry_to_composite({})
        assert score.total == 0.0


# ── _read_jsonl_from_branch ──────────────────────────────────────


class TestReadJsonlFromBranch:
    def test_success(self, tmp_path):
        content = _make_jsonl(SAMPLE_ENTRY)
        with patch("factory.baseline.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=content, stderr=""
            )
            result = _read_jsonl_from_branch(tmp_path)
            assert result == content

    def test_branch_not_found(self, tmp_path):
        with patch("factory.baseline.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr="fatal: invalid object name"
            )
            assert _read_jsonl_from_branch(tmp_path) is None


# ── _find_base_commit ────────────────────────────────────────────


class TestFindBaseCommit:
    def test_success(self, tmp_path):
        with patch("factory.baseline.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="abc123\n", stderr=""
            )
            assert _find_base_commit(tmp_path) == "abc123"

    def test_failure_tries_origin(self, tmp_path):
        results = [
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="def456\n", stderr=""),
        ]
        with patch("factory.baseline.subprocess.run", side_effect=results):
            assert _find_base_commit(tmp_path) == "def456"

    def test_all_fail(self, tmp_path):
        with patch("factory.baseline.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr=""
            )
            assert _find_base_commit(tmp_path) is None


# ── get_baseline (integration) ───────────────────────────────────


class TestGetBaseline:
    def test_returns_score_for_known_commit(self, tmp_path):
        content = _make_jsonl(SAMPLE_ENTRY, SAMPLE_ENTRY_2)
        with patch("factory.baseline._find_base_commit", return_value="abc123def456"), \
             patch("factory.baseline._read_jsonl_from_branch", return_value=content):
            score = get_baseline(tmp_path)
            assert score is not None
            assert score.total == 0.638

    def test_returns_none_when_no_data(self, tmp_path):
        with patch("factory.baseline._find_base_commit", return_value="abc123"), \
             patch("factory.baseline._read_jsonl_from_branch", return_value=None), \
             patch("factory.baseline._detect_repo", return_value=None):
            assert get_baseline(tmp_path) is None

    def test_returns_none_when_commit_not_found(self, tmp_path):
        content = _make_jsonl(SAMPLE_ENTRY)
        with patch("factory.baseline._find_base_commit", return_value="nonexistent"), \
             patch("factory.baseline._read_jsonl_from_branch", return_value=content):
            assert get_baseline(tmp_path) is None

    def test_uses_explicit_commit(self, tmp_path):
        content = _make_jsonl(SAMPLE_ENTRY)
        with patch("factory.baseline._read_jsonl_from_branch", return_value=content):
            score = get_baseline(tmp_path, commit="abc123def456")
            assert score is not None
            assert score.total == 0.638

    def test_no_base_commit_returns_none(self, tmp_path):
        with patch("factory.baseline._find_base_commit", return_value=None):
            assert get_baseline(tmp_path) is None

    def test_falls_back_to_remote(self, tmp_path):
        content = _make_jsonl(SAMPLE_ENTRY)
        with patch("factory.baseline._find_base_commit", return_value="abc123def456"), \
             patch("factory.baseline._read_jsonl_from_branch", return_value=None), \
             patch("factory.baseline._detect_repo", return_value="owner/repo"), \
             patch("factory.baseline._read_jsonl_from_remote", return_value=content):
            score = get_baseline(tmp_path)
            assert score is not None
            assert score.total == 0.638


# ── get_latest_main_baseline ─────────────────────────────────────


class TestGetLatestMainBaseline:
    def test_returns_latest_main(self, tmp_path):
        content = _make_jsonl(SAMPLE_ENTRY, SAMPLE_PR_ENTRY, SAMPLE_ENTRY_2)
        with patch("factory.baseline._read_jsonl_from_branch", return_value=content):
            score = get_latest_main_baseline(tmp_path)
            assert score is not None
            assert score.total == 0.700

    def test_returns_none_when_no_main_entries(self, tmp_path):
        content = _make_jsonl(SAMPLE_PR_ENTRY)
        with patch("factory.baseline._read_jsonl_from_branch", return_value=content):
            assert get_latest_main_baseline(tmp_path) is None

    def test_returns_none_when_no_data(self, tmp_path):
        with patch("factory.baseline._read_jsonl_from_branch", return_value=None), \
             patch("factory.baseline._detect_repo", return_value=None):
            assert get_latest_main_baseline(tmp_path) is None
