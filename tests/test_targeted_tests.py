"""Tests for graph-based targeted test selection."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from factory.graph import find_dependent_tests


def _make_graph(
    tmp_path: Path,
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> Path:
    gpath = tmp_path / "graph.json"
    gpath.write_text(json.dumps({
        "nodes": nodes or [],
        "edges": edges or [],
    }))
    return gpath


def _simple_graph(tmp_path: Path) -> Path:
    """A -> B -> test_B (via imports edges), plus unrelated tests to stay under fan-out."""
    nodes = [
        {"id": "mod_a", "source_file": "src/a.py"},
        {"id": "mod_b", "source_file": "src/b.py"},
        {"id": "mod_c", "source_file": "src/c.py"},
        {"id": "test_b", "source_file": "tests/test_b.py"},
        {"id": "test_a", "source_file": "tests/test_a.py"},
        {"id": "test_c1", "source_file": "tests/test_c1.py"},
        {"id": "test_c2", "source_file": "tests/test_c2.py"},
        {"id": "test_c3", "source_file": "tests/test_c3.py"},
        {"id": "test_c4", "source_file": "tests/test_c4.py"},
        {"id": "test_c5", "source_file": "tests/test_c5.py"},
        {"id": "test_c6", "source_file": "tests/test_c6.py"},
        {"id": "test_c7", "source_file": "tests/test_c7.py"},
        {"id": "test_c8", "source_file": "tests/test_c8.py"},
    ]
    edges = [
        {"source": "mod_b", "target": "mod_a", "relation": "imports"},
        {"source": "test_b", "target": "mod_b", "relation": "imports"},
        {"source": "test_a", "target": "mod_a", "relation": "imports"},
        {"source": "test_c1", "target": "mod_c", "relation": "imports"},
        {"source": "test_c2", "target": "mod_c", "relation": "imports"},
        {"source": "test_c3", "target": "mod_c", "relation": "imports"},
        {"source": "test_c4", "target": "mod_c", "relation": "imports"},
        {"source": "test_c5", "target": "mod_c", "relation": "imports"},
        {"source": "test_c6", "target": "mod_c", "relation": "imports"},
        {"source": "test_c7", "target": "mod_c", "relation": "imports"},
        {"source": "test_c8", "target": "mod_c", "relation": "imports"},
    ]
    return _make_graph(tmp_path, nodes, edges)


class TestFindDependentTests:
    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_basic_reverse_bfs(self, _stale: MagicMock, tmp_path: Path) -> None:
        _simple_graph(tmp_path)
        result = find_dependent_tests(tmp_path, ["src/a.py"])
        assert result is not None
        assert "tests/test_a.py" in result
        assert "tests/test_b.py" in result

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_direct_import_only(self, _stale: MagicMock, tmp_path: Path) -> None:
        _simple_graph(tmp_path)
        result = find_dependent_tests(tmp_path, ["src/b.py"])
        assert result is not None
        assert "tests/test_b.py" in result
        assert "tests/test_a.py" not in result

    @patch("factory.graph.is_graph_stale", return_value=True)
    def test_returns_none_when_stale(self, _stale: MagicMock, tmp_path: Path) -> None:
        _simple_graph(tmp_path)
        assert find_dependent_tests(tmp_path, ["src/a.py"]) is None

    @patch("factory.graph.is_graph_stale", return_value=None)
    def test_returns_none_when_staleness_unknown(self, _stale: MagicMock, tmp_path: Path) -> None:
        _simple_graph(tmp_path)
        assert find_dependent_tests(tmp_path, ["src/a.py"]) is None

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_returns_none_on_conftest(self, _stale: MagicMock, tmp_path: Path) -> None:
        _simple_graph(tmp_path)
        assert find_dependent_tests(tmp_path, ["tests/conftest.py"]) is None

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_returns_none_on_init(self, _stale: MagicMock, tmp_path: Path) -> None:
        _simple_graph(tmp_path)
        assert find_dependent_tests(tmp_path, ["src/__init__.py"]) is None

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_returns_none_on_pyproject(self, _stale: MagicMock, tmp_path: Path) -> None:
        _simple_graph(tmp_path)
        assert find_dependent_tests(tmp_path, ["pyproject.toml"]) is None

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_returns_none_on_pytest_ini(self, _stale: MagicMock, tmp_path: Path) -> None:
        _simple_graph(tmp_path)
        assert find_dependent_tests(tmp_path, ["pytest.ini"]) is None

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_returns_none_on_workflow_file(self, _stale: MagicMock, tmp_path: Path) -> None:
        _simple_graph(tmp_path)
        assert find_dependent_tests(tmp_path, [".github/workflows/ci.yml"]) is None

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_returns_none_on_non_python_no_node(self, _stale: MagicMock, tmp_path: Path) -> None:
        _simple_graph(tmp_path)
        assert find_dependent_tests(tmp_path, ["README.md"]) is None

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_returns_none_on_unknown_py_file(self, _stale: MagicMock, tmp_path: Path) -> None:
        _simple_graph(tmp_path)
        assert find_dependent_tests(tmp_path, ["src/unknown.py"]) is None

    def test_returns_none_on_empty_changed_files(self) -> None:
        assert find_dependent_tests(Path("/tmp"), []) is None

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_fan_out_returns_none(self, _stale: MagicMock, tmp_path: Path) -> None:
        """When >80% of test files are dependent, return None."""
        nodes = [
            {"id": "mod_a", "source_file": "src/a.py"},
        ]
        edges = []
        for i in range(10):
            nid = f"test_{i}"
            nodes.append({"id": nid, "source_file": f"tests/test_{i}.py"})
            edges.append({"source": nid, "target": "mod_a", "relation": "imports"})
        _make_graph(tmp_path, nodes, edges)
        result = find_dependent_tests(tmp_path, ["src/a.py"])
        assert result is None

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_fan_out_under_threshold(self, _stale: MagicMock, tmp_path: Path) -> None:
        """When <80% of test files are dependent, return the set."""
        nodes = [
            {"id": "mod_a", "source_file": "src/a.py"},
            {"id": "mod_b", "source_file": "src/b.py"},
        ]
        edges = []
        for i in range(3):
            nid = f"test_a_{i}"
            nodes.append({"id": nid, "source_file": f"tests/test_a_{i}.py"})
            edges.append({"source": nid, "target": "mod_a", "relation": "imports"})
        for i in range(10):
            nid = f"test_b_{i}"
            nodes.append({"id": nid, "source_file": f"tests/test_b_{i}.py"})
        _make_graph(tmp_path, nodes, edges)
        result = find_dependent_tests(tmp_path, ["src/a.py"])
        assert result is not None
        assert len(result) == 3

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_transitive_multi_hop(self, _stale: MagicMock, tmp_path: Path) -> None:
        """C imports B imports A; changing A reaches test_C."""
        nodes = [
            {"id": "mod_a", "source_file": "src/a.py"},
            {"id": "mod_b", "source_file": "src/b.py"},
            {"id": "mod_c", "source_file": "src/c.py"},
            {"id": "test_c", "source_file": "tests/test_c.py"},
        ]
        # Add unrelated tests to stay under fan-out threshold
        for i in range(8):
            nodes.append({"id": f"test_u{i}", "source_file": f"tests/test_u{i}.py"})
        edges = [
            {"source": "mod_b", "target": "mod_a", "relation": "imports"},
            {"source": "mod_c", "target": "mod_b", "relation": "imports_from"},
            {"source": "test_c", "target": "mod_c", "relation": "imports"},
        ]
        _make_graph(tmp_path, nodes, edges)
        result = find_dependent_tests(tmp_path, ["src/a.py"])
        assert result is not None
        assert "tests/test_c.py" in result

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_changed_test_file_included(self, _stale: MagicMock, tmp_path: Path) -> None:
        _simple_graph(tmp_path)
        result = find_dependent_tests(tmp_path, ["tests/test_a.py"])
        assert result is not None
        assert "tests/test_a.py" in result

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_returns_empty_set_when_no_tests_depend(
        self, _stale: MagicMock, tmp_path: Path,
    ) -> None:
        nodes = [
            {"id": "mod_a", "source_file": "src/a.py"},
            {"id": "mod_b", "source_file": "src/b.py"},
            {"id": "test_x", "source_file": "tests/test_x.py"},
        ]
        edges = [
            {"source": "test_x", "target": "mod_b", "relation": "imports"},
        ]
        _make_graph(tmp_path, nodes, edges)
        result = find_dependent_tests(tmp_path, ["src/a.py"])
        assert result is not None
        assert len(result) == 0

    @patch("factory.graph.is_graph_stale", return_value=False)
    def test_non_import_edges_ignored(self, _stale: MagicMock, tmp_path: Path) -> None:
        nodes = [
            {"id": "mod_a", "source_file": "src/a.py"},
            {"id": "test_a", "source_file": "tests/test_a.py"},
        ]
        edges = [
            {"source": "test_a", "target": "mod_a", "relation": "calls"},
        ]
        _make_graph(tmp_path, nodes, edges)
        result = find_dependent_tests(tmp_path, ["src/a.py"])
        assert result is not None
        assert len(result) == 0


class TestPythonEvaluatorTestPaths:
    @patch("factory.eval.languages.python.PythonEvaluator._detect_cov_target", return_value="src")
    def test_test_paths_appended_to_cmd(self, _cov: MagicMock) -> None:
        from factory.eval.languages.python import PythonEvaluator

        ev = PythonEvaluator()
        with patch("factory.eval.languages.python._run_cmd") as mock_run:
            mock_run.return_value = (0, "1 passed", "TOTAL 100 10 90%")
            ev.run_tests_with_coverage(
                Path("/fake"),
                timeout=60,
                test_paths=["tests/test_a.py", "tests/test_b.py"],
            )
            cmd = mock_run.call_args[0][0]
            assert "tests/test_a.py" in cmd
            assert "tests/test_b.py" in cmd
            assert cmd.index("tests/test_a.py") > cmd.index("-q")

    @patch("factory.eval.languages.python.PythonEvaluator._detect_cov_target", return_value="src")
    def test_no_test_paths_unchanged(self, _cov: MagicMock) -> None:
        from factory.eval.languages.python import PythonEvaluator

        ev = PythonEvaluator()
        with patch("factory.eval.languages.python._run_cmd") as mock_run:
            mock_run.return_value = (0, "1 passed", "TOTAL 100 10 90%")
            ev.run_tests_with_coverage(Path("/fake"), timeout=60)
            cmd = mock_run.call_args[0][0]
            assert not any(c.startswith("tests/") for c in cmd)

    @patch("factory.eval.languages.python.PythonEvaluator._detect_cov_target", return_value="src")
    def test_run_tests_forwards_test_paths(self, _cov: MagicMock) -> None:
        from factory.eval.languages.python import PythonEvaluator

        ev = PythonEvaluator()
        with patch("factory.eval.languages.python._run_cmd") as mock_run:
            mock_run.return_value = (0, "1 passed", "TOTAL 100 10 90%")
            ev.run_tests(Path("/fake"), timeout=60, test_paths=["tests/test_x.py"])
            cmd = mock_run.call_args[0][0]
            assert "tests/test_x.py" in cmd
