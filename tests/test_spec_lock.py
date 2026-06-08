"""Tests for factory.spec_lock — create/read/clear/check_scope_deviation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.spec_lock import (
    check_scope_deviation,
    clear_spec_lock,
    create_spec_lock,
    read_spec_lock,
)
from factory.models import SpecLock


@pytest.fixture
def lock_project(tmp_path: Path) -> Path:
    """Create a minimal project directory with .factory/."""
    project = tmp_path / "lock-project"
    project.mkdir()
    (project / ".factory").mkdir()
    return project


SAMPLE_SPEC = """\
## Project Specification

Build a weather CLI tool.

## Scope Boundaries
- CLI argument parsing
- Weather API integration
- Output formatting
"""


# ── model tests ──────────────────────────────────────────────────


def test_spec_lock_strict() -> None:
    """SpecLock rejects extra fields."""
    with pytest.raises(Exception):
        SpecLock(
            spec_hash="abc123",
            scope_boundaries=["feature-a"],
            locked_at="2026-01-01T00:00:00",
            source="interactive",
            extra_field="bad",
        )


def test_spec_lock_source_validation() -> None:
    """SpecLock only accepts 'interactive' or 'research' as source."""
    lock = SpecLock(
        spec_hash="abc123",
        scope_boundaries=["feature-a"],
        locked_at="2026-01-01T00:00:00",
        source="research",
    )
    assert lock.source == "research"


# ── create / read / clear ────────────────────────────────────────


def test_create_and_read(lock_project: Path) -> None:
    """create_spec_lock writes JSON, read_spec_lock round-trips it."""
    lock = create_spec_lock(
        lock_project,
        SAMPLE_SPEC,
        ["CLI argument parsing", "Weather API integration", "Output formatting"],
        "interactive",
    )

    lock_path = lock_project / ".factory" / "spec_lock.json"
    assert lock_path.exists()

    loaded = read_spec_lock(lock_project)
    assert loaded is not None
    assert loaded.spec_hash == lock.spec_hash
    assert loaded.scope_boundaries == [
        "CLI argument parsing",
        "Weather API integration",
        "Output formatting",
    ]
    assert loaded.source == "interactive"
    assert loaded.locked_at == lock.locked_at


def test_create_computes_hash(lock_project: Path) -> None:
    """create_spec_lock produces a deterministic SHA-256 hash of the spec content."""
    import hashlib

    lock = create_spec_lock(lock_project, SAMPLE_SPEC, ["feature-a"], "interactive")
    expected = hashlib.sha256(SAMPLE_SPEC.encode()).hexdigest()
    assert lock.spec_hash == expected


def test_read_missing(lock_project: Path) -> None:
    """read_spec_lock returns None when no lock exists."""
    assert read_spec_lock(lock_project) is None


def test_clear(lock_project: Path) -> None:
    """clear_spec_lock removes the file."""
    create_spec_lock(lock_project, SAMPLE_SPEC, ["feature-a"], "interactive")
    lock_path = lock_project / ".factory" / "spec_lock.json"
    assert lock_path.exists()

    clear_spec_lock(lock_project)
    assert not lock_path.exists()


def test_clear_noop(lock_project: Path) -> None:
    """clear_spec_lock does not raise when no lock exists."""
    clear_spec_lock(lock_project)


def test_create_creates_factory_dir(tmp_path: Path) -> None:
    """create_spec_lock creates .factory/ if it doesn't exist."""
    project = tmp_path / "bare-project"
    project.mkdir()
    create_spec_lock(project, SAMPLE_SPEC, ["feature-a"], "interactive")
    assert (project / ".factory" / "spec_lock.json").exists()


def test_read_corrupt_json(lock_project: Path) -> None:
    """read_spec_lock returns None for corrupted JSON files."""
    lock_path = lock_project / ".factory" / "spec_lock.json"
    lock_path.write_text("{truncated")

    loaded = read_spec_lock(lock_project)
    assert loaded is None


def test_read_invalid_schema(lock_project: Path) -> None:
    """read_spec_lock returns None for valid JSON with invalid schema."""
    lock_path = lock_project / ".factory" / "spec_lock.json"
    lock_path.write_text(json.dumps({"wrong_field": "bad"}))

    loaded = read_spec_lock(lock_project)
    assert loaded is None


def test_create_research_source(lock_project: Path) -> None:
    """create_spec_lock accepts 'research' as source."""
    lock = create_spec_lock(lock_project, SAMPLE_SPEC, ["feature-a"], "research")
    assert lock.source == "research"

    loaded = read_spec_lock(lock_project)
    assert loaded is not None
    assert loaded.source == "research"


# ── scope deviation checks ───────────────────────────────────────


def test_check_scope_no_deviation() -> None:
    """check_scope_deviation returns empty list when all items are in scope."""
    lock = SpecLock(
        spec_hash="abc",
        scope_boundaries=["CLI argument", "Weather API", "Output"],
        locked_at="2026-01-01T00:00:00",
        source="interactive",
    )
    deviations = check_scope_deviation(lock, ["CLI argument parsing", "Weather API calls"])
    assert deviations == []


def test_check_scope_with_deviation() -> None:
    """check_scope_deviation returns items outside the locked scope."""
    lock = SpecLock(
        spec_hash="abc",
        scope_boundaries=["CLI argument", "Weather API"],
        locked_at="2026-01-01T00:00:00",
        source="interactive",
    )
    deviations = check_scope_deviation(
        lock, ["CLI argument parsing", "Database caching", "Weather API calls"]
    )
    assert deviations == ["Database caching"]


def test_check_scope_all_deviations() -> None:
    """check_scope_deviation returns all items when none match."""
    lock = SpecLock(
        spec_hash="abc",
        scope_boundaries=["CLI"],
        locked_at="2026-01-01T00:00:00",
        source="interactive",
    )
    deviations = check_scope_deviation(lock, ["Database layer", "Admin dashboard"])
    assert deviations == ["Database layer", "Admin dashboard"]


def test_check_scope_empty_proposed() -> None:
    """check_scope_deviation returns empty list for empty proposed scope."""
    lock = SpecLock(
        spec_hash="abc",
        scope_boundaries=["CLI"],
        locked_at="2026-01-01T00:00:00",
        source="interactive",
    )
    assert check_scope_deviation(lock, []) == []


def test_check_scope_prefix_matching() -> None:
    """check_scope_deviation uses prefix matching for boundaries."""
    lock = SpecLock(
        spec_hash="abc",
        scope_boundaries=["factory/"],
        locked_at="2026-01-01T00:00:00",
        source="interactive",
    )
    deviations = check_scope_deviation(
        lock, ["factory/models.py", "factory/cli.py", "tests/test_models.py"]
    )
    assert deviations == ["tests/test_models.py"]
