"""Tests for scripts/conflict_detector.py — standalone PR conflict detector."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import conflict_detector


def _make_run_mock(pr_json: str = "[]", merge_results: dict[str, tuple[int, str]] | None = None):
    """Build a side_effect for subprocess.run that fakes gh + git merge-tree."""
    if merge_results is None:
        merge_results = {}

    def _side_effect(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=pr_json, stderr="")
        if cmd[:2] == ["git", "merge-tree"]:
            branch = cmd[-1]
            if branch in merge_results:
                rc, stdout = merge_results[branch]
                return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return _side_effect


class TestDetect:
    def test_no_open_prs(self, tmp_path: Path) -> None:
        data_file = tmp_path / "conflicts.jsonl"
        with patch.object(conflict_detector, "_run", side_effect=_make_run_mock("[]")):
            rc = conflict_detector.main(["detect", "--data-file", str(data_file)])
        assert rc == 0
        assert not data_file.exists()

    def test_clean_merge(self, tmp_path: Path) -> None:
        prs = json.dumps([
            {"number": 1, "headRefName": "feat/a", "isDraft": False},
            {"number": 2, "headRefName": "feat/b", "isDraft": False},
        ])
        data_file = tmp_path / "conflicts.jsonl"
        with patch.object(conflict_detector, "_run", side_effect=_make_run_mock(prs)):
            rc = conflict_detector.main(["detect", "--data-file", str(data_file)])
        assert rc == 0
        assert not data_file.exists()

    def test_conflict_detected(self, tmp_path: Path) -> None:
        prs = json.dumps([
            {"number": 42, "headRefName": "feat/x", "isDraft": False},
        ])
        merge_output = (
            "abc123\n"
            "CONFLICT (content): Merge conflict in src/config.py\n"
            "CONFLICT (content): Merge conflict in README.md\n"
        )
        data_file = tmp_path / "conflicts.jsonl"
        with patch.object(
            conflict_detector,
            "_run",
            side_effect=_make_run_mock(prs, {"origin/feat/x": (1, merge_output)}),
        ):
            rc = conflict_detector.main(["detect", "--data-file", str(data_file)])
        assert rc == 1
        assert data_file.exists()
        events = [json.loads(line) for line in data_file.read_text().splitlines()]
        assert len(events) == 1
        assert events[0]["pr_number"] == 42
        assert events[0]["conflict_files"] == ["src/config.py", "README.md"]
        assert events[0]["total_open_prs"] == 1

    def test_draft_prs_skipped(self, tmp_path: Path) -> None:
        prs = json.dumps([
            {"number": 10, "headRefName": "draft/wip", "isDraft": True},
            {"number": 11, "headRefName": "feat/ready", "isDraft": False},
        ])
        merge_output = "CONFLICT (content): Merge conflict in main.py\n"
        data_file = tmp_path / "conflicts.jsonl"
        with patch.object(
            conflict_detector,
            "_run",
            side_effect=_make_run_mock(prs, {"origin/draft/wip": (1, merge_output)}),
        ):
            rc = conflict_detector.main(["detect", "--data-file", str(data_file)])
        assert rc == 0
        assert not data_file.exists()

    def test_include_drafts(self, tmp_path: Path) -> None:
        prs = json.dumps([
            {"number": 10, "headRefName": "draft/wip", "isDraft": True},
        ])
        merge_output = "CONFLICT (content): Merge conflict in main.py\n"
        data_file = tmp_path / "conflicts.jsonl"
        with patch.object(
            conflict_detector,
            "_run",
            side_effect=_make_run_mock(prs, {"origin/draft/wip": (1, merge_output)}),
        ):
            rc = conflict_detector.main(["detect", "--include-drafts", "--data-file", str(data_file)])
        assert rc == 1
        events = [json.loads(line) for line in data_file.read_text().splitlines()]
        assert len(events) == 1
        assert events[0]["pr_number"] == 10

    def test_delete_modify_conflict(self, tmp_path: Path) -> None:
        prs = json.dumps([{"number": 5, "headRefName": "feat/del", "isDraft": False}])
        merge_output = "CONFLICT (modify/delete): old.py deleted in HEAD and modified in origin/feat/del\n"
        data_file = tmp_path / "conflicts.jsonl"
        with patch.object(
            conflict_detector,
            "_run",
            side_effect=_make_run_mock(prs, {"origin/feat/del": (1, merge_output)}),
        ):
            rc = conflict_detector.main(["detect", "--data-file", str(data_file)])
        assert rc == 1
        events = [json.loads(line) for line in data_file.read_text().splitlines()]
        assert events[0]["conflict_files"] == ["old.py"]


class TestReport:
    def test_jsonl_roundtrip(self, tmp_path: Path) -> None:
        data_file = tmp_path / "conflicts.jsonl"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        events = [
            {"timestamp": now, "pr_number": 1, "pr_branch": "feat/a", "conflict_files": ["x.py"], "total_open_prs": 5},
            {"timestamp": now, "pr_number": 2, "pr_branch": "feat/b", "conflict_files": ["x.py", "y.py"], "total_open_prs": 5},
        ]
        with open(data_file, "w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        readback = [json.loads(line) for line in data_file.read_text().splitlines()]
        assert len(readback) == 2
        assert readback[0]["pr_number"] == 1
        assert readback[1]["conflict_files"] == ["x.py", "y.py"]

    def test_report_date_filter(self, tmp_path: Path) -> None:
        data_file = tmp_path / "conflicts.jsonl"
        old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        events = [
            {"timestamp": old_ts, "pr_number": 1, "pr_branch": "old", "conflict_files": ["old.py"], "total_open_prs": 1},
            {"timestamp": recent_ts, "pr_number": 2, "pr_branch": "new", "conflict_files": ["new.py"], "total_open_prs": 1},
        ]
        with open(data_file, "w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        with patch.object(conflict_detector, "_run"):
            rc = conflict_detector.main(["report", "--days", "30", "--data-file", str(data_file)])
        assert rc == 0

    def test_hotspot_ranking(self, tmp_path: Path) -> None:
        data_file = tmp_path / "conflicts.jsonl"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        events = [
            {"timestamp": now, "pr_number": 1, "pr_branch": "a", "conflict_files": ["hot.py", "cold.py"], "total_open_prs": 3},
            {"timestamp": now, "pr_number": 2, "pr_branch": "b", "conflict_files": ["hot.py"], "total_open_prs": 3},
            {"timestamp": now, "pr_number": 3, "pr_branch": "c", "conflict_files": ["hot.py"], "total_open_prs": 3},
        ]
        with open(data_file, "w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        with patch.object(conflict_detector, "_run"):
            rc = conflict_detector.main(["report", "--data-file", str(data_file)])
        assert rc == 0

    def test_no_data_file(self, tmp_path: Path) -> None:
        data_file = tmp_path / "nonexistent.jsonl"
        rc = conflict_detector.main(["report", "--data-file", str(data_file)])
        assert rc == 0

    def test_empty_data_file(self, tmp_path: Path) -> None:
        data_file = tmp_path / "conflicts.jsonl"
        data_file.write_text("")
        rc = conflict_detector.main(["report", "--data-file", str(data_file)])
        assert rc == 0

    def test_issue_flag_success(self, tmp_path: Path) -> None:
        """Test --issue flag posts report to GitHub issue (success path)."""
        data_file = tmp_path / "conflicts.jsonl"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        events = [
            {"timestamp": now, "pr_number": 1, "pr_branch": "feat/a", "conflict_files": ["x.py"], "total_open_prs": 1},
        ]
        with open(data_file, "w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        # Mock _run to capture gh issue comment call
        def _mock_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[:3] == ["gh", "issue", "comment"]:
                assert cmd[3] == "42"
                assert cmd[4] == "--body"
                assert "Conflict Hotspots" in cmd[5]
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(conflict_detector, "_run", side_effect=_mock_run):
            rc = conflict_detector.main(["report", "--data-file", str(data_file), "--issue", "42"])
        assert rc == 0

    def test_issue_flag_failure(self, tmp_path: Path) -> None:
        """Test --issue flag handles gh CLI failure (error path)."""
        data_file = tmp_path / "conflicts.jsonl"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        events = [
            {"timestamp": now, "pr_number": 1, "pr_branch": "feat/a", "conflict_files": ["x.py"], "total_open_prs": 1},
        ]
        with open(data_file, "w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        # Mock _run to simulate gh CLI failure
        def _mock_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[:3] == ["gh", "issue", "comment"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="API error: issue not found")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(conflict_detector, "_run", side_effect=_mock_run):
            rc = conflict_detector.main(["report", "--data-file", str(data_file), "--issue", "999"])
        assert rc == 1


class TestSummary:
    def test_summary_no_conflicts(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Summary shows green checkmark when no conflicts exist."""
        prs = json.dumps([
            {"number": 1, "headRefName": "feat/a", "isDraft": False},
            {"number": 2, "headRefName": "feat/b", "isDraft": False},
        ])
        data_file = tmp_path / "conflicts.jsonl"
        with patch.object(conflict_detector, "_run", side_effect=_make_run_mock(prs)):
            rc = conflict_detector.main(["summary", "--data-file", str(data_file)])
        assert rc == 0
        captured = capsys.readouterr()
        assert "✅" in captured.out
        assert "No conflicts detected" in captured.out

    def test_summary_with_conflicts(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Summary contains Mermaid chart and hotspot table when conflicts exist."""
        prs = json.dumps([
            {"number": 42, "headRefName": "feat/x", "isDraft": False},
        ])
        merge_output = "CONFLICT (content): Merge conflict in src/config.py\n"
        data_file = tmp_path / "conflicts.jsonl"

        # Write historical data
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        events = [
            {"timestamp": now, "pr_number": 42, "pr_branch": "feat/x", "conflict_files": ["src/config.py", "README.md"], "total_open_prs": 1},
            {"timestamp": now, "pr_number": 43, "pr_branch": "feat/y", "conflict_files": ["src/config.py"], "total_open_prs": 2},
        ]
        with open(data_file, "w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        with patch.object(
            conflict_detector,
            "_run",
            side_effect=_make_run_mock(prs, {"origin/feat/x": (1, merge_output)}),
        ):
            rc = conflict_detector.main(["summary", "--data-file", str(data_file)])
        assert rc == 0
        captured = capsys.readouterr()
        assert "```mermaid" in captured.out
        assert "xychart-beta" in captured.out
        assert "Hotspot Files" in captured.out
        assert "Currently Conflicting PRs" in captured.out
        assert "#42" in captured.out
        assert "src/config.py" in captured.out

    def test_summary_no_data_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Summary handles missing data file gracefully."""
        prs = json.dumps([
            {"number": 1, "headRefName": "feat/a", "isDraft": False},
        ])
        data_file = tmp_path / "nonexistent.jsonl"
        with patch.object(conflict_detector, "_run", side_effect=_make_run_mock(prs)):
            rc = conflict_detector.main(["summary", "--data-file", str(data_file)])
        assert rc == 0
        captured = capsys.readouterr()
        assert "✅" in captured.out or "Checked:" in captured.out


class TestCLI:
    def test_no_command_shows_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = conflict_detector.main([])
        assert rc == 2
        captured = capsys.readouterr()
        assert "usage" in captured.out.lower() or "detect" in captured.out.lower()
