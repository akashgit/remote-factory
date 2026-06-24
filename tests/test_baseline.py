"""Tests for factory.baseline — JSONL parsing, commit lookup, and CLI integration."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from factory.baseline import _parse_scores_jsonl, fetch_baseline


# ── JSONL parsing ─────────────────────────────────────────────


class TestParseScoresJsonl:
    def test_valid_records(self) -> None:
        raw = (
            '{"commit": "aaa111", "composite_score": 0.85, "passed": true}\n'
            '{"commit": "bbb222", "composite_score": 0.90, "passed": true}\n'
        )
        lookup = _parse_scores_jsonl(raw)
        assert len(lookup) == 2
        assert lookup["aaa111"]["composite_score"] == 0.85
        assert lookup["bbb222"]["composite_score"] == 0.90

    def test_malformed_lines_skipped(self) -> None:
        raw = (
            '{"commit": "aaa111", "composite_score": 0.85}\n'
            'NOT VALID JSON\n'
            '{"commit": "bbb222", "composite_score": 0.90}\n'
        )
        lookup = _parse_scores_jsonl(raw)
        assert len(lookup) == 2
        assert "aaa111" in lookup
        assert "bbb222" in lookup

    def test_empty_input(self) -> None:
        assert _parse_scores_jsonl("") == {}

    def test_blank_lines_skipped(self) -> None:
        raw = '\n\n{"commit": "aaa111", "composite_score": 0.85}\n\n'
        lookup = _parse_scores_jsonl(raw)
        assert len(lookup) == 1

    def test_missing_commit_key(self) -> None:
        raw = '{"no_commit": "x", "composite_score": 0.5}\n'
        lookup = _parse_scores_jsonl(raw)
        assert len(lookup) == 0


# ── Commit lookup ─────────────────────────────────────────────


def _make_completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


SCORES_JSONL = (
    '{"commit": "abc123", "composite_score": 0.85, "passed": true}\n'
    '{"commit": "def456", "composite_score": 0.90, "passed": true}\n'
)


class TestFetchBaseline:
    def test_exact_match(self, tmp_path: Path) -> None:
        calls: list[list[str]] = []

        def mock_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            if args[:2] == ["fetch", "origin"]:
                return _make_completed()
            if args[0] == "show":
                return _make_completed(stdout=SCORES_JSONL)
            return _make_completed(returncode=1)

        with patch("factory.baseline._git", side_effect=mock_git):
            result = fetch_baseline(tmp_path, commit_sha="abc123")

        assert result is not None
        assert result["composite_score"] == 0.85

    def test_ancestor_walk(self, tmp_path: Path) -> None:
        def mock_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if args[:2] == ["fetch", "origin"]:
                return _make_completed()
            if args[0] == "show":
                return _make_completed(stdout=SCORES_JSONL)
            if args[0] == "rev-list":
                return _make_completed(stdout="unknown1\ndef456\nold789\n")
            return _make_completed(returncode=1)

        with patch("factory.baseline._git", side_effect=mock_git):
            result = fetch_baseline(tmp_path, commit_sha="newer_commit")

        assert result is not None
        assert result["commit"] == "def456"
        assert result["composite_score"] == 0.90

    def test_no_match(self, tmp_path: Path) -> None:
        def mock_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if args[:2] == ["fetch", "origin"]:
                return _make_completed()
            if args[0] == "show":
                return _make_completed(stdout=SCORES_JSONL)
            if args[0] == "rev-list":
                return _make_completed(stdout="no_match_1\nno_match_2\n")
            return _make_completed(returncode=1)

        with patch("factory.baseline._git", side_effect=mock_git):
            result = fetch_baseline(tmp_path, commit_sha="nonexistent")

        assert result is None

    def test_fetch_uses_explicit_refspec(self, tmp_path: Path) -> None:
        calls: list[list[str]] = []

        def mock_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            if args[0] == "fetch":
                return _make_completed()
            if args[0] == "show":
                return _make_completed(stdout=SCORES_JSONL)
            return _make_completed(returncode=1)

        with patch("factory.baseline._git", side_effect=mock_git):
            fetch_baseline(tmp_path, commit_sha="abc123")

        fetch_call = calls[0]
        assert fetch_call[0] == "fetch"
        assert fetch_call[1] == "origin"
        assert fetch_call[2] == "eval-data:refs/remotes/origin/eval-data"

    def test_fetch_failure(self, tmp_path: Path) -> None:
        def mock_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if args[:2] == ["fetch", "origin"]:
                return _make_completed(returncode=1)
            return _make_completed(returncode=1)

        with patch("factory.baseline._git", side_effect=mock_git):
            result = fetch_baseline(tmp_path, commit_sha="abc123")

        assert result is None

    def test_empty_scores(self, tmp_path: Path) -> None:
        def mock_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if args[:2] == ["fetch", "origin"]:
                return _make_completed()
            if args[0] == "show":
                return _make_completed(stdout="")
            return _make_completed(returncode=1)

        with patch("factory.baseline._git", side_effect=mock_git):
            result = fetch_baseline(tmp_path, commit_sha="abc123")

        assert result is None


# ── CLI argument parsing ──────────────────────────────────────


class TestBaselineCLI:
    def test_parser_accepts_path(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["baseline", "/tmp/project"])
        assert args.command == "baseline"
        assert args.path == "/tmp/project"
        assert args.commit is None

    def test_parser_accepts_commit_flag(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["baseline", "/tmp/project", "--commit", "abc123"])
        assert args.commit == "abc123"

    def test_handler_registered(self) -> None:
        from factory.cli import cmd_baseline

        assert callable(cmd_baseline)

    def test_cmd_baseline_success(self, tmp_path: Path, capsys) -> None:
        from factory.cli import cmd_baseline

        baseline_data = {"commit": "abc123", "composite_score": 0.85, "passed": True}
        args = argparse.Namespace(path=str(tmp_path), commit="abc123")

        with patch("factory.baseline.fetch_baseline", return_value=baseline_data):
            rc = cmd_baseline(args)

        assert rc == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["composite_score"] == 0.85
        assert parsed["commit"] == "abc123"

    def test_cmd_baseline_no_match(self, tmp_path: Path, capsys) -> None:
        from factory.cli import cmd_baseline

        args = argparse.Namespace(path=str(tmp_path), commit="deadbeef1234")

        with patch("factory.baseline.fetch_baseline", return_value=None):
            rc = cmd_baseline(args)

        assert rc == 1
        captured = capsys.readouterr()
        assert "No baseline found" in captured.err

    def test_cmd_baseline_default_commit(self, tmp_path: Path, capsys) -> None:
        from factory.cli import cmd_baseline

        baseline_data = {"commit": "resolved_sha", "composite_score": 0.90}
        args = argparse.Namespace(path=str(tmp_path))

        merge_base_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="resolved_sha\n", stderr=""
        )

        with (
            patch("factory.baseline.fetch_baseline", return_value=baseline_data) as mock_fetch,
            patch("subprocess.run", return_value=merge_base_result) as mock_run,
            patch("factory.cli._read_target_branch", return_value="main"),
        ):
            rc = cmd_baseline(args)

        assert rc == 0
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "merge-base" in call_args[0][0]
        mock_fetch.assert_called_once_with(tmp_path, commit_sha="resolved_sha")
