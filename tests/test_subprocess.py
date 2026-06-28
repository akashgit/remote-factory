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
