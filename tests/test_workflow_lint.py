"""Tests for the contributed workflow linter."""

from __future__ import annotations

import argparse
from pathlib import Path

from factory.workflow.cli import _cmd_lint_contributed
from factory.workflow.lint import LintIssue, _load_module, lint_contributed


def _make_valid_workflow(d: Path) -> None:
    """Create a minimal valid contributed workflow directory."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "__init__.py").write_text("from .workflow import meta, workflow\n")
    (d / "README.md").write_text("# Test\n")
    (d / "test_workflow.py").write_text("def test_placeholder(): pass\n")
    (d / "workflow.py").write_text(
        "from factory.workflow.primitives import Edge, FnNode, Workflow\n"
        "\n"
        'meta = {"name": "test", "description": "A test workflow"}\n'
        "\n"
        "def workflow() -> Workflow:\n"
        "    return Workflow(\n"
        '        name="test",\n'
        '        nodes={"start": FnNode(id="start", command="echo hi")},\n'
        "        edges=[],\n"
        '        start_node="start",\n'
        "    )\n"
    )


def _issue_checks(issues: list[LintIssue]) -> set[str]:
    return {i.check for i in issues}


class TestValidDirectory:
    def test_valid_passes(self, tmp_path: Path) -> None:
        _make_valid_workflow(tmp_path / "good")
        issues = lint_contributed(tmp_path)
        assert issues == []

    def test_skips_pycache(self, tmp_path: Path) -> None:
        (tmp_path / "__pycache__").mkdir()
        issues = lint_contributed(tmp_path)
        assert issues == []

    def test_skips_files(self, tmp_path: Path) -> None:
        (tmp_path / "some_file.py").write_text("")
        issues = lint_contributed(tmp_path)
        assert issues == []


class TestMissingFiles:
    def test_missing_init(self, tmp_path: Path) -> None:
        _make_valid_workflow(tmp_path / "bad")
        (tmp_path / "bad" / "__init__.py").unlink()
        issues = lint_contributed(tmp_path)
        assert "missing-__init__.py" in _issue_checks(issues)

    def test_missing_workflow(self, tmp_path: Path) -> None:
        _make_valid_workflow(tmp_path / "bad")
        (tmp_path / "bad" / "workflow.py").unlink()
        issues = lint_contributed(tmp_path)
        assert "missing-workflow.py" in _issue_checks(issues)

    def test_missing_readme(self, tmp_path: Path) -> None:
        _make_valid_workflow(tmp_path / "bad")
        (tmp_path / "bad" / "README.md").unlink()
        issues = lint_contributed(tmp_path)
        assert "missing-README.md" in _issue_checks(issues)

    def test_missing_test(self, tmp_path: Path) -> None:
        _make_valid_workflow(tmp_path / "bad")
        (tmp_path / "bad" / "test_workflow.py").unlink()
        issues = lint_contributed(tmp_path)
        assert "missing-test_workflow.py" in _issue_checks(issues)


class TestInvalidMeta:
    def test_missing_name(self, tmp_path: Path) -> None:
        _make_valid_workflow(tmp_path / "bad")
        (tmp_path / "bad" / "workflow.py").write_text(
            "from factory.workflow.primitives import Edge, FnNode, Workflow\n"
            "\n"
            'meta = {"description": "no name"}\n'
            "\n"
            "def workflow() -> Workflow:\n"
            "    return Workflow(\n"
            '        name="test",\n'
            '        nodes={"start": FnNode(id="start", command="echo hi")},\n'
            "        edges=[],\n"
            '        start_node="start",\n'
            "    )\n"
        )
        issues = lint_contributed(tmp_path)
        assert "meta-missing-name" in _issue_checks(issues)

    def test_no_meta_dict(self, tmp_path: Path) -> None:
        _make_valid_workflow(tmp_path / "bad")
        (tmp_path / "bad" / "workflow.py").write_text(
            "from factory.workflow.primitives import Edge, FnNode, Workflow\n"
            "\n"
            "def workflow() -> Workflow:\n"
            "    return Workflow(\n"
            '        name="test",\n'
            '        nodes={"start": FnNode(id="start", command="echo hi")},\n'
            "        edges=[],\n"
            '        start_node="start",\n'
            "    )\n"
        )
        issues = lint_contributed(tmp_path)
        assert "missing-meta" in _issue_checks(issues)


class TestMissingWorkflowFunction:
    def test_no_workflow_callable(self, tmp_path: Path) -> None:
        _make_valid_workflow(tmp_path / "bad")
        (tmp_path / "bad" / "workflow.py").write_text(
            'meta = {"name": "test", "description": "test"}\n'
            'workflow = "not a function"\n'
        )
        issues = lint_contributed(tmp_path)
        assert "missing-workflow-fn" in _issue_checks(issues)


class TestGraphValidation:
    def test_invalid_graph(self, tmp_path: Path) -> None:
        _make_valid_workflow(tmp_path / "bad")
        (tmp_path / "bad" / "workflow.py").write_text(
            "from factory.workflow.primitives import Edge, FnNode, Workflow\n"
            "\n"
            'meta = {"name": "test", "description": "bad graph"}\n'
            "\n"
            "def workflow() -> Workflow:\n"
            "    return Workflow(\n"
            '        name="test",\n'
            '        nodes={"a": FnNode(id="a", command="echo")},\n'
            '        edges=[Edge(source="a", target="nonexistent")],\n'
            '        start_node="a",\n'
            "    )\n"
        )
        issues = lint_contributed(tmp_path)
        assert "graph-invalid" in _issue_checks(issues)

    def test_workflow_call_error(self, tmp_path: Path) -> None:
        _make_valid_workflow(tmp_path / "bad")
        (tmp_path / "bad" / "workflow.py").write_text(
            'meta = {"name": "test", "description": "raises"}\n'
            "\n"
            "def workflow():\n"
            '    raise RuntimeError("boom")\n'
        )
        issues = lint_contributed(tmp_path)
        assert "workflow-call-error" in _issue_checks(issues)


class TestLintContributedEdgeCases:
    def test_nonexistent_base_dir(self, tmp_path: Path) -> None:
        issues = lint_contributed(tmp_path / "does_not_exist")
        assert issues == []

    def test_load_module_returns_none_for_bad_file(self, tmp_path: Path) -> None:
        bad_py = tmp_path / "bad.py"
        bad_py.write_text("raise SyntaxError\n")
        result = _load_module(bad_py)
        assert result is None

    def test_load_module_returns_none_for_nonexistent(self, tmp_path: Path) -> None:
        result = _load_module(tmp_path / "nonexistent.py")
        assert result is None

    def test_load_module_spec_none(self, tmp_path: Path, monkeypatch: object) -> None:
        """Cover the spec is None branch."""
        import importlib.util

        real_path = tmp_path / "mod.py"
        real_path.write_text("x = 1\n")
        monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *a, **kw: None)  # type: ignore[attr-defined]
        result = _load_module(real_path)
        assert result is None

    def test_load_error_produces_issue(self, tmp_path: Path) -> None:
        d = tmp_path / "broken"
        d.mkdir()
        (d / "__init__.py").write_text("")
        (d / "README.md").write_text("# Broken\n")
        (d / "test_workflow.py").write_text("")
        (d / "workflow.py").write_text("raise RuntimeError('import fail')\n")
        issues = lint_contributed(tmp_path)
        assert "load-error" in _issue_checks(issues)


class TestCmdLintContributed:
    def test_clean_exit_zero(self, tmp_path: Path, capsys: object) -> None:
        _make_valid_workflow(tmp_path / "good")
        args = argparse.Namespace(path=str(tmp_path))
        rc = _cmd_lint_contributed(args)
        assert rc == 0
        captured = capsys.readouterr()  # type: ignore[union-attr]
        assert "clean" in captured.out

    def test_issues_exit_one(self, tmp_path: Path, capsys: object) -> None:
        d = tmp_path / "incomplete"
        d.mkdir()
        args = argparse.Namespace(path=str(tmp_path))
        rc = _cmd_lint_contributed(args)
        assert rc == 1
        captured = capsys.readouterr()  # type: ignore[union-attr]
        assert "issue(s) found" in captured.out

    def test_default_path_when_none(self, capsys: object) -> None:
        args = argparse.Namespace(path=None)
        rc = _cmd_lint_contributed(args)
        assert rc in (0, 1)


class TestLintContributedReal:
    """Smoke test: lint the actual contributed workflows directory."""

    def test_real_contributed_clean(self) -> None:
        base = Path(__file__).resolve().parent.parent / "factory" / "workflow" / "contributed"
        if not base.is_dir():
            return
        issues = lint_contributed(base)
        assert issues == [], f"Real contributed workflows have lint issues: {issues}"
