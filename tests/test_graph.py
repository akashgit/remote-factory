"""Tests for factory.graph — graphify integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from factory.graph import (
    extract_graph,
    graph_stats,
    is_graph_available,
    is_graph_stale,
    update_graph,
)


def _write_graph(tmp_path: Path, data: dict | None = None) -> Path:
    gdir = tmp_path / ".factory" / "graphify-out"
    gdir.mkdir(parents=True)
    gpath = gdir / "graph.json"
    gpath.write_text(json.dumps(data or {"nodes": [], "edges": []}))
    return gpath


class TestIsGraphAvailable:
    def test_true_when_graph_exists(self, tmp_path: Path) -> None:
        _write_graph(tmp_path)
        assert is_graph_available(tmp_path) is True

    def test_false_when_missing(self, tmp_path: Path) -> None:
        assert is_graph_available(tmp_path) is False


class TestGraphStats:
    def test_returns_counts(self, tmp_path: Path) -> None:
        data = {
            "nodes": [{"id": "a"}, {"id": "b"}],
            "edges": [{"source": "a", "target": "b"}],
        }
        _write_graph(tmp_path, data)
        stats = graph_stats(tmp_path)
        assert stats == {"nodes": 2, "edges": 1}

    def test_uses_links_fallback(self, tmp_path: Path) -> None:
        data = {"nodes": [{"id": "x"}], "links": [{"from": "x", "to": "y"}]}
        _write_graph(tmp_path, data)
        stats = graph_stats(tmp_path)
        assert stats == {"nodes": 1, "edges": 1}

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert graph_stats(tmp_path) is None

    def test_returns_none_on_malformed_json(self, tmp_path: Path) -> None:
        gdir = tmp_path / ".factory" / "graphify-out"
        gdir.mkdir(parents=True)
        (gdir / "graph.json").write_text("not json")
        assert graph_stats(tmp_path) is None


class TestIsGraphStale:
    def test_returns_none_when_no_graph(self, tmp_path: Path) -> None:
        assert is_graph_stale(tmp_path) is None

    @patch("factory.graph.subprocess.run")
    def test_stale_when_commit_newer(self, mock_run: MagicMock, tmp_path: Path) -> None:

        gpath = _write_graph(tmp_path)
        graph_mtime = gpath.stat().st_mtime
        mock_run.return_value = MagicMock(returncode=0, stdout=str(graph_mtime + 100))
        assert is_graph_stale(tmp_path) is True

    @patch("factory.graph.subprocess.run")
    def test_fresh_when_graph_newer(self, mock_run: MagicMock, tmp_path: Path) -> None:
        _write_graph(tmp_path)
        mock_run.return_value = MagicMock(returncode=0, stdout="0")
        assert is_graph_stale(tmp_path) is False

    @patch("factory.graph.subprocess.run")
    def test_returns_none_on_git_failure(self, mock_run: MagicMock, tmp_path: Path) -> None:
        _write_graph(tmp_path)
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        assert is_graph_stale(tmp_path) is None


class TestExtractGraph:
    @patch("factory.graph.is_graphify_installed", return_value=False)
    def test_returns_none_when_not_installed(self, _mock: MagicMock, tmp_path: Path) -> None:
        assert extract_graph(tmp_path) is None

    @patch("factory.graph.subprocess.run")
    @patch("factory.graph.is_graphify_installed", return_value=True)
    def test_success(self, _inst: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        gpath = _write_graph(tmp_path, {"nodes": [{"id": "a"}], "edges": []})
        mock_run.return_value = MagicMock(returncode=0)
        result = extract_graph(tmp_path)
        assert result == gpath

    @patch("factory.graph.subprocess.run")
    @patch("factory.graph.is_graphify_installed", return_value=True)
    def test_nonzero_exit_returns_none(
        self, _inst: MagicMock, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        assert extract_graph(tmp_path) is None

    @patch("factory.graph.subprocess.run", side_effect=FileNotFoundError("no graphify"))
    @patch("factory.graph.is_graphify_installed", return_value=True)
    def test_file_not_found_returns_none(
        self, _inst: MagicMock, _run: MagicMock, tmp_path: Path
    ) -> None:
        assert extract_graph(tmp_path) is None

    @patch("factory.graph.subprocess.run")
    @patch("factory.graph.is_graphify_installed", return_value=True)
    def test_no_output_file_returns_none(
        self, _inst: MagicMock, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        (tmp_path / ".factory").mkdir(parents=True)
        mock_run.return_value = MagicMock(returncode=0)
        assert extract_graph(tmp_path) is None


class TestUpdateGraph:
    @patch("factory.graph.subprocess.run")
    @patch("factory.graph.is_graphify_installed", return_value=True)
    def test_passes_update_flag(
        self, _inst: MagicMock, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        _write_graph(tmp_path, {"nodes": [], "edges": []})
        mock_run.return_value = MagicMock(returncode=0)
        update_graph(tmp_path)
        cmd = mock_run.call_args[0][0]
        assert "--update" in cmd
