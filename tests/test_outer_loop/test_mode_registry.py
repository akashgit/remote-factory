"""Tests for EphemeralModeRegistry."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.outer_loop.mode_registry import EphemeralModeRegistry
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    GateNode,
    Workflow,
)


def _make_workflow(name: str = "test_wf") -> Workflow:
    return Workflow(
        name=name,
        nodes={
            "builder": AgentNode(
                id="builder",
                role=AgentRole.BUILDER,
                writes={".factory/reviews/builder-latest.md"},
            ),
            "gate": GateNode(
                id="gate",
                evaluator_type="agent",
                evaluator_role=AgentRole.HEALTH_CHECKER,
            ),
        },
        edges=[Edge(source="builder", target="gate")],
        start_node="builder",
    )


class TestEphemeralModeRegistry:
    def test_register_creates_file(self, tmp_path: Path) -> None:
        registry = EphemeralModeRegistry(tmp_path)
        wf = _make_workflow()
        mode_name = registry.register("abc12345", 0, wf)

        assert mode_name == "evolve-gen0-abc12345"
        mode_file = tmp_path / ".factory" / "outer_loop" / "modes" / f"{mode_name}.json"
        assert mode_file.exists()

    def test_register_naming_convention(self, tmp_path: Path) -> None:
        registry = EphemeralModeRegistry(tmp_path)
        wf = _make_workflow()

        name0 = registry.register("individual1", 0, wf)
        name1 = registry.register("individual2", 3, wf)

        assert name0 == "evolve-gen0-individu"
        assert name1 == "evolve-gen3-individu"

    def test_load_round_trip(self, tmp_path: Path) -> None:
        registry = EphemeralModeRegistry(tmp_path)
        wf = _make_workflow()
        mode_name = registry.register("test1234", 0, wf)

        loaded = registry.load(mode_name)
        assert loaded is not None
        assert set(loaded.nodes.keys()) == {"builder", "gate"}
        assert loaded.start_node == "builder"

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        registry = EphemeralModeRegistry(tmp_path)
        assert registry.load("nonexistent-mode") is None

    def test_cleanup_generation(self, tmp_path: Path) -> None:
        registry = EphemeralModeRegistry(tmp_path)
        wf = _make_workflow()

        registry.register("aaa", 0, wf)
        registry.register("bbb", 0, wf)
        registry.register("ccc", 0, wf)

        assert registry.count == 3
        removed = registry.cleanup_generation({"evolve-gen0-aaa"})
        assert removed == 2
        assert registry.count == 1
        modes = registry.list_modes()
        assert "evolve-gen0-aaa" in modes

    def test_cleanup_all(self, tmp_path: Path) -> None:
        registry = EphemeralModeRegistry(tmp_path)
        wf = _make_workflow()

        registry.register("aaa", 0, wf)
        registry.register("bbb", 1, wf)

        removed = registry.cleanup_all()
        assert removed == 2
        assert registry.count == 0

    def test_cleanup_all_keep_best(self, tmp_path: Path) -> None:
        registry = EphemeralModeRegistry(tmp_path)
        wf = _make_workflow()

        registry.register("aaa", 0, wf)
        registry.register("bbb", 0, wf)

        removed = registry.cleanup_all(keep_best="evolve-gen0-bbb")
        assert removed == 1
        assert registry.count == 1

    def test_context_manager_cleanup(self, tmp_path: Path) -> None:
        with EphemeralModeRegistry(tmp_path) as registry:
            wf = _make_workflow()
            registry.register("test", 0, wf)
            assert registry.count == 1

        # After context exit, modes should be cleaned up
        fresh = EphemeralModeRegistry(tmp_path)
        assert fresh.count == 0

    def test_promote(self, tmp_path: Path) -> None:
        registry = EphemeralModeRegistry(tmp_path)
        wf = _make_workflow()
        mode_name = registry.register("winner", 5, wf)

        dest = registry.promote(mode_name, "best-evolved")
        assert dest is not None
        assert dest.exists()
        assert "best-evolved" in str(dest)

    def test_promote_nonexistent(self, tmp_path: Path) -> None:
        registry = EphemeralModeRegistry(tmp_path)
        assert registry.promote("nonexistent", "test") is None

    def test_list_modes(self, tmp_path: Path) -> None:
        registry = EphemeralModeRegistry(tmp_path)
        wf = _make_workflow()

        registry.register("aaa", 0, wf)
        registry.register("bbb", 1, wf)

        modes = registry.list_modes()
        assert len(modes) == 2
        assert "evolve-gen0-aaa" in modes
        assert "evolve-gen1-bbb" in modes
