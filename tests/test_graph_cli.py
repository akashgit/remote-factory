"""Tests for graph CLI wrapper commands (query, explain, path)."""

from __future__ import annotations

import argparse
import subprocess
from unittest.mock import patch

import pytest


@pytest.fixture()
def _mock_graphify_installed():
    with patch("factory.graph.is_graphify_installed", return_value=True):
        yield


@pytest.fixture()
def _mock_graphify_not_installed():
    with patch("factory.graph.is_graphify_installed", return_value=False):
        yield


@pytest.fixture()
def _mock_graph_available():
    with patch("factory.graph.is_graph_available", return_value=True):
        yield


@pytest.fixture()
def _mock_graph_not_available():
    with patch("factory.graph.is_graph_available", return_value=False):
        yield


class TestCmdGraphQuery:
    @pytest.mark.usefixtures("_mock_graphify_installed", "_mock_graph_available")
    def test_success(self, tmp_path):
        from factory.cli.graph import cmd_graph_query

        args = argparse.Namespace(path=str(tmp_path), question="auth flow", depth=2)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="Found 3 nodes\n", stderr=""
            )
            result = cmd_graph_query(args)
        assert result == 0
        mock_run.assert_called_once()
        call_cmd = mock_run.call_args[0][0]
        assert call_cmd[0] == "graphify"
        assert call_cmd[1] == "query"
        assert "auth flow" in call_cmd

    @pytest.mark.usefixtures("_mock_graphify_not_installed")
    def test_not_installed(self, tmp_path):
        from factory.cli.graph import cmd_graph_query

        args = argparse.Namespace(path=str(tmp_path), question="test", depth=2)
        result = cmd_graph_query(args)
        assert result == 1

    @pytest.mark.usefixtures("_mock_graphify_installed", "_mock_graph_not_available")
    def test_no_graph(self, tmp_path):
        from factory.cli.graph import cmd_graph_query

        args = argparse.Namespace(path=str(tmp_path), question="test", depth=2)
        result = cmd_graph_query(args)
        assert result == 1


class TestCmdGraphExplain:
    @pytest.mark.usefixtures("_mock_graphify_installed", "_mock_graph_available")
    def test_success(self, tmp_path):
        from factory.cli.graph import cmd_graph_explain

        args = argparse.Namespace(path=str(tmp_path), node="Study")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="Study node: ...\n", stderr=""
            )
            result = cmd_graph_explain(args)
        assert result == 0
        call_cmd = mock_run.call_args[0][0]
        assert call_cmd[1] == "explain"

    @pytest.mark.usefixtures("_mock_graphify_not_installed")
    def test_not_installed(self, tmp_path):
        from factory.cli.graph import cmd_graph_explain

        args = argparse.Namespace(path=str(tmp_path), node="Study")
        result = cmd_graph_explain(args)
        assert result == 1


class TestCmdGraphPath:
    @pytest.mark.usefixtures("_mock_graphify_installed", "_mock_graph_available")
    def test_success(self, tmp_path):
        from factory.cli.graph import cmd_graph_path

        args = argparse.Namespace(path=str(tmp_path), source="Study", target="invoke_agent")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="Path: Study -> invoke_agent\n", stderr=""
            )
            result = cmd_graph_path(args)
        assert result == 0
        call_cmd = mock_run.call_args[0][0]
        assert call_cmd[1] == "path"
        assert "Study" in call_cmd
        assert "invoke_agent" in call_cmd

    @pytest.mark.usefixtures("_mock_graphify_not_installed")
    def test_not_installed(self, tmp_path):
        from factory.cli.graph import cmd_graph_path

        args = argparse.Namespace(path=str(tmp_path), source="A", target="B")
        result = cmd_graph_path(args)
        assert result == 1

    @pytest.mark.usefixtures("_mock_graphify_installed", "_mock_graph_available")
    def test_timeout(self, tmp_path):
        from factory.cli.graph import cmd_graph_path

        args = argparse.Namespace(path=str(tmp_path), source="A", target="B")
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("graphify", 60)):
            result = cmd_graph_path(args)
        assert result == 1
