"""Tests for factory/runners/_subprocess.py."""

from __future__ import annotations

import ast
from pathlib import Path


def test_subprocess_readline_limit():
    """Verify subprocess uses 1MB readline limit, not default 64KB."""
    source = (Path(__file__).parent.parent / "factory" / "runners" / "_subprocess.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and "create_subprocess_exec" in ast.dump(node):
            for kw in node.keywords:
                if kw.arg == "limit":
                    assert isinstance(kw.value, ast.Constant)
                    assert kw.value.value >= 1_048_576
                    return
    raise AssertionError("No limit= kwarg found in create_subprocess_exec call")


def test_max_timeout_default_is_10_hours():
    """Verify the default max_timeout is 36000s (10 hours), not 1 hour."""
    source = (Path(__file__).parent.parent / "factory" / "runners" / "_subprocess.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_subprocess":
            for arg, default in zip(reversed(node.args.kwonlyargs), reversed(node.args.kw_defaults)):
                if arg.arg == "max_timeout":
                    assert isinstance(default, ast.Constant)
                    assert default.value == 36000.0, (
                        f"max_timeout default should be 36000.0 (10h), got {default.value}"
                    )
                    return
    raise AssertionError("No max_timeout kwarg found in run_subprocess signature")
