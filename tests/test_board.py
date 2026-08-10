"""Unit tests for the Board shared data plane."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.workflow.board import Board


@pytest.fixture
def board(tmp_path: Path) -> Board:
    return Board(
        path=tmp_path / "board.json",
        run_id="test-run-1",
        modes=["modeA", "modeB"],
    )


class TestWriteReadNamespace:
    def test_write_read_roundtrip(self, board: Board) -> None:
        board.write("modeA", "result", {"score": 0.95})
        assert board.read("modeA", "result") == {"score": 0.95}

    def test_read_full_namespace(self, board: Board) -> None:
        board.write("modeA", "x", 1)
        board.write("modeA", "y", 2)
        assert board.read("modeA") == {"x": 1, "y": 2}

    def test_read_missing_key_returns_none(self, board: Board) -> None:
        assert board.read("modeA", "nonexistent") is None

    def test_read_missing_mode_returns_empty(self, board: Board) -> None:
        assert board.read("modeA") == {}


class TestNamespaceEnforcement:
    def test_write_to_wrong_mode_raises(self, board: Board) -> None:
        with pytest.raises(ValueError, match="not in allowed modes"):
            board.write("modeC", "key", "val")


class TestGlobalReadWrite:
    def test_global_write_read(self, board: Board) -> None:
        board.write_global("shared_key", [1, 2, 3])
        assert board.read_global("shared_key") == [1, 2, 3]

    def test_global_read_all(self, board: Board) -> None:
        board.write_global("a", 1)
        board.write_global("b", 2)
        assert board.read_global() == {"a": 1, "b": 2}

    def test_global_read_missing_returns_none(self, board: Board) -> None:
        assert board.read_global("nope") is None


class TestMarkModeComplete:
    def test_mark_mode_complete(self, board: Board) -> None:
        board.mark_mode_complete("modeA")
        assert "modeA" in board.state.modes_completed

    def test_mark_mode_complete_idempotent(self, board: Board) -> None:
        board.mark_mode_complete("modeA")
        board.mark_mode_complete("modeA")
        assert board.state.modes_completed.count("modeA") == 1


class TestSaveLoadRoundtrip:
    def test_roundtrip(self, board: Board) -> None:
        board.write("modeA", "key1", "value1")
        board.write("modeB", "key2", 42)
        board.write_global("g", True)
        board.mark_mode_complete("modeA")
        board.save()

        board2 = Board(
            path=board._path,
            run_id="test-run-1",
            modes=["modeA", "modeB"],
        )
        board2.load()
        assert board2.read("modeA", "key1") == "value1"
        assert board2.read("modeB", "key2") == 42
        assert board2.read_global("g") is True
        assert "modeA" in board2.state.modes_completed


class TestReset:
    def test_reset_clears_data(self, board: Board) -> None:
        board.write("modeA", "k", "v")
        board.write_global("g", 1)
        board.mark_mode_complete("modeA")
        board.reset()

        assert board.read("modeA") == {}
        assert board.read_global() == {}
        assert board.state.modes_completed == []


class TestAtomicWrite:
    def test_os_replace_called(self, board: Board) -> None:
        board.write("modeA", "k", "v")
        with patch("factory.workflow.board.os.replace", wraps=lambda src, dst: Path(src).rename(dst)) as mock_replace:
            board.save()
            mock_replace.assert_called_once()

    def test_file_written_correctly(self, board: Board) -> None:
        board.write("modeA", "k", "v")
        board.save()
        raw = json.loads(board._path.read_text())
        assert raw["data"]["modeA"]["k"] == "v"


class TestSnapshot:
    def test_snapshot_single_mode(self, board: Board) -> None:
        board.write("modeA", "x", 10)
        assert board.snapshot("modeA") == {"x": 10}

    def test_snapshot_all(self, board: Board) -> None:
        board.write("modeA", "x", 1)
        board.write("modeB", "y", 2)
        snap = board.snapshot()
        assert snap == {"modeA": {"x": 1}, "modeB": {"y": 2}}

    def test_snapshot_empty_mode(self, board: Board) -> None:
        assert board.snapshot("modeA") == {}
