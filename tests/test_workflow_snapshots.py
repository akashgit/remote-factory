"""Snapshot preservation tests for core workflows.

These tests lock the current graph structure (node ids, edge tuples,
conditions, reads/writes, post_checks) for the workflows that V2 Phase 1
will refactor (stage extraction). The refactor must be graph-identical:
after extraction, these snapshots must NOT change.

Regenerate deliberately (only when the graph is intentionally changed):

    FACTORY_REGEN_SNAPSHOTS=1 pytest tests/test_workflow_snapshots.py

Snapshots live in tests/snapshots/workflows/.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from factory.skill_cache import _sort_recursive
from factory.workflow.definitions import register_all
from factory.workflow.primitives import Workflow

SNAPSHOT_DIR = Path(__file__).parent / "snapshots" / "workflows"

# The workflows Phase 1 stage extraction will touch. Snapshots must stay
# byte-identical across the refactor.
SNAPSHOT_WORKFLOWS: list[str] = [
    "build",
    "design",
    "improve",
    "research",
    "parallel-improve",
    "create",
]


def _canonical(wf: Workflow) -> dict:
    """Deterministic canonical form, matching skill_cache checksum semantics.

    Edges are additionally sorted by (source, target, condition) because
    _sort_recursive cannot order lists of dicts — edge ORDER is not part
    of graph semantics, edge SET is.
    """
    payload = _sort_recursive(wf.model_dump(mode="json"))
    payload["edges"] = sorted(
        payload["edges"],
        key=lambda e: (e.get("source", ""), e.get("target", ""), str(e.get("condition"))),
    )
    return payload


def _snapshot_path(name: str) -> Path:
    return SNAPSHOT_DIR / f"{name}.json"


def _load_snapshot(name: str) -> dict:
    return json.loads(_snapshot_path(name).read_text())["graph"]


def test_snapshots_cover_expected_workflows() -> None:
    all_wf = register_all()
    missing = [name for name in SNAPSHOT_WORKFLOWS if name not in all_wf]
    assert missing == []


class TestWorkflowSnapshots:
    def test_snapshot_files_in_sync(self) -> None:
        """Current graphs must equal committed snapshots (graph-identical refactor guard)."""
        all_wf = register_all()
        for name in SNAPSHOT_WORKFLOWS:
            assert _canonical(all_wf[name]) == _load_snapshot(name), (
                f"workflow '{name}' drifted from its snapshot — "
                f"if the change is intentional, regenerate with "
                f"FACTORY_REGEN_SNAPSHOTS=1"
            )

    def test_snapshot_graphs_validate(self) -> None:
        all_wf = register_all()
        for name in SNAPSHOT_WORKFLOWS:
            assert all_wf[name].validate_graph() == [], (
                f"workflow '{name}' no longer validates"
            )

    def test_no_dangling_snapshot_files(self) -> None:
        expected = {_snapshot_path(name) for name in SNAPSHOT_WORKFLOWS}
        actual = set(SNAPSHOT_DIR.glob("*.json"))
        assert actual == expected


def test_regen_snapshots() -> None:
    """Hidden regen helper: FACTORY_REGEN_SNAPSHOTS=1 pytest -k regen_snapshots."""
    if not os.environ.get("FACTORY_REGEN_SNAPSHOTS"):
        import pytest

        pytest.skip("set FACTORY_REGEN_SNAPSHOTS=1 to regenerate snapshots")
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    all_wf = register_all()
    for name in SNAPSHOT_WORKFLOWS:
        payload = {"name": name, "graph": _canonical(all_wf[name])}
        _snapshot_path(name).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n"
        )
