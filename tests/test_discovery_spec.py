"""Tests for factory.discovery.spec — GRAPH-SPEC resolution and generation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from factory.discovery.spec import (
    generate_spec,
    resolve_spec,
)


def test_resolve_spec_committed(tmp_path: Path):
    (tmp_path / "GRAPH-SPEC.md").write_text("# Spec")
    path, source = resolve_spec(tmp_path)
    assert source == "committed"
    assert path == tmp_path / "GRAPH-SPEC.md"


def test_resolve_spec_generated(tmp_path: Path):
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "GRAPH-SPEC.md").write_text("# Generated Spec")
    path, source = resolve_spec(tmp_path)
    assert source == "generated"
    assert path == factory_dir / "GRAPH-SPEC.md"


def test_resolve_spec_committed_takes_priority(tmp_path: Path):
    (tmp_path / "GRAPH-SPEC.md").write_text("# Committed")
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "GRAPH-SPEC.md").write_text("# Generated")
    path, source = resolve_spec(tmp_path)
    assert source == "committed"
    assert path == tmp_path / "GRAPH-SPEC.md"


def test_resolve_spec_absent(tmp_path: Path):
    path, source = resolve_spec(tmp_path)
    assert source == "absent"
    assert path is None


def test_generate_spec_delegates_to_spec_module(tmp_path: Path):
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    spec_path = factory_dir / "GRAPH-SPEC.md"
    spec_path.write_text("# GRAPH-SPEC\n\nGenerated content.")

    mock_generate = AsyncMock(return_value=spec_path)
    with patch("factory.spec.generate.generate_spec", mock_generate):
        result = generate_spec(tmp_path)

    mock_generate.assert_awaited_once_with(tmp_path)
    assert result == "# GRAPH-SPEC\n\nGenerated content."
