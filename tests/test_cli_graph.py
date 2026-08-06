"""Tests for factory.cli.graph — extract, update, status subcommands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from factory.cli.graph import cmd_graph_extract, cmd_graph_status, cmd_graph_update


def _write_graph(tmp_path: Path, data: dict | None = None) -> Path:
    gdir = tmp_path / ".factory" / "graphify-out"
    gdir.mkdir(parents=True)
    gpath = gdir / "graph.json"
    gpath.write_text(json.dumps(data or {"nodes": [], "edges": []}))
    return gpath


class TestCmdGraphExtract:
    def test_not_a_directory(self) -> None:
        args = argparse.Namespace(path="/nonexistent")
        assert cmd_graph_extract(args) == 1

    @patch("factory.graph.is_graphify_installed", return_value=False)
    def test_graphify_not_installed(self, _mock: MagicMock, tmp_path: Path) -> None:
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_graph_extract(args) == 1

    @patch("factory.graph.extract_graph", return_value=None)
    @patch("factory.graph.is_graphify_installed", return_value=True)
    def test_extraction_failure(self, _inst: MagicMock, _ext: MagicMock, tmp_path: Path) -> None:
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_graph_extract(args) == 1

    @patch("factory.graph.extract_graph")
    @patch("factory.graph.is_graphify_installed", return_value=True)
    def test_success(self, _inst: MagicMock, mock_ext: MagicMock, tmp_path: Path) -> None:
        gpath = tmp_path / ".factory" / "graphify-out" / "graph.json"
        mock_ext.return_value = gpath
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_graph_extract(args) == 0


class TestCmdGraphUpdate:
    def test_not_a_directory(self) -> None:
        args = argparse.Namespace(path="/nonexistent")
        assert cmd_graph_update(args) == 1

    @patch("factory.graph.is_graphify_installed", return_value=False)
    def test_graphify_not_installed(self, _mock: MagicMock, tmp_path: Path) -> None:
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_graph_update(args) == 1

    @patch("factory.graph.update_graph")
    @patch("factory.graph.is_graph_available", return_value=True)
    @patch("factory.graph.is_graphify_installed", return_value=True)
    def test_incremental_update(
        self, _inst: MagicMock, _avail: MagicMock, mock_upd: MagicMock, tmp_path: Path
    ) -> None:
        mock_upd.return_value = tmp_path / "graph.json"
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_graph_update(args) == 0

    @patch("factory.graph.extract_graph")
    @patch("factory.graph.is_graph_available", return_value=False)
    @patch("factory.graph.is_graphify_installed", return_value=True)
    def test_fallback_to_full_extract(
        self, _inst: MagicMock, _avail: MagicMock, mock_ext: MagicMock, tmp_path: Path
    ) -> None:
        mock_ext.return_value = tmp_path / "graph.json"
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_graph_update(args) == 0

    @patch("factory.graph.update_graph", return_value=None)
    @patch("factory.graph.is_graph_available", return_value=True)
    @patch("factory.graph.is_graphify_installed", return_value=True)
    def test_update_failure(
        self, _inst: MagicMock, _avail: MagicMock, _upd: MagicMock, tmp_path: Path
    ) -> None:
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_graph_update(args) == 1


class TestCmdGraphStatus:
    def test_not_a_directory(self) -> None:
        args = argparse.Namespace(path="/nonexistent")
        assert cmd_graph_status(args) == 1

    @patch("factory.graph.is_graphify_installed", return_value=False)
    def test_no_graph(self, _mock: MagicMock, tmp_path: Path) -> None:
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_graph_status(args) == 0

    @patch("factory.graph.is_graph_stale", return_value=True)
    @patch("factory.graph.is_graphify_installed", return_value=True)
    def test_stale_graph(self, _inst: MagicMock, _stale: MagicMock, tmp_path: Path) -> None:
        _write_graph(tmp_path, {"nodes": [{"id": "a"}], "edges": []})
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_graph_status(args) == 0

    @patch("factory.graph.is_graph_stale", return_value=False)
    @patch("factory.graph.is_graphify_installed", return_value=True)
    def test_fresh_graph(self, _inst: MagicMock, _stale: MagicMock, tmp_path: Path) -> None:
        _write_graph(tmp_path, {"nodes": [{"id": "a"}], "edges": []})
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_graph_status(args) == 0

    @patch("factory.graph.is_graph_stale", return_value=None)
    @patch("factory.graph.is_graphify_installed", return_value=True)
    def test_unknown_staleness(self, _inst: MagicMock, _stale: MagicMock, tmp_path: Path) -> None:
        _write_graph(tmp_path, {"nodes": [{"id": "a"}], "edges": []})
        args = argparse.Namespace(path=str(tmp_path))
        assert cmd_graph_status(args) == 0
