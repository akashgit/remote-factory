"""Tests for factory.skill_cache — checksum-based workflow skill caching."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from factory.skill_cache import _compute_checksum, _sort_recursive, ensure_skills
from factory.workflow.definitions import register_all
from factory.workflow.primitives import AgentNode, AgentRole, FnNode, Workflow


def _make_workflow(name: str = "test", cmd: str = "echo hi") -> Workflow:
    return Workflow(
        name=name,
        nodes={"a": FnNode(id="a", command=cmd)},
        edges=[],
        start_node="a",
    )


class TestComputeChecksum:
    def test_deterministic(self) -> None:
        workflows = register_all()
        assert _compute_checksum(workflows) == _compute_checksum(workflows)

    def test_deterministic_with_set_fields(self) -> None:
        """set[str] fields (reads/writes) must not cause hash variation."""
        def _make_wf_with_sets() -> dict[str, Workflow]:
            return {
                "w": Workflow(
                    name="w",
                    nodes={
                        "a": AgentNode(
                            id="a",
                            role=AgentRole.RESEARCHER,
                            reads={"z", "a", "m", "b"},
                            writes={"x", "c", "w"},
                        ),
                    },
                    edges=[],
                    start_node="a",
                ),
            }

        checksums = {_compute_checksum(_make_wf_with_sets()) for _ in range(20)}
        assert len(checksums) == 1

    def test_sort_recursive(self) -> None:
        obj = {"b": [3, 1, 2], "a": {"y": [2, 1], "x": 1}}
        result = _sort_recursive(obj)
        assert result == {"a": {"x": 1, "y": [1, 2]}, "b": [1, 2, 3]}

    def test_changes_on_modification(self) -> None:
        wf1 = {"x": _make_workflow("x", "echo 1")}
        wf2 = {"x": _make_workflow("x", "echo 2")}
        assert _compute_checksum(wf1) != _compute_checksum(wf2)


class TestEnsureSkills:
    async def test_cache_miss(self, tmp_path: Path, monkeypatch: object) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # type: ignore[arg-type]

        project = tmp_path / "proj"
        project.mkdir()

        paths = await ensure_skills(project, refine=False)
        assert len(paths) > 0
        assert all(p.name == "SKILL.md" for p in paths)

        cache_root = tmp_path / ".factory" / "cache" / "skills"
        assert cache_root.exists()

    async def test_cache_hit(self, tmp_path: Path, monkeypatch: object) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # type: ignore[arg-type]

        project = tmp_path / "proj"
        project.mkdir()

        await ensure_skills(project, refine=False)

        with patch(
            "factory.workflow.skill_export.export_all_skills",
            wraps=None,
        ) as mock_export:
            mock_export.return_value = []
            paths = await ensure_skills(project, refine=False)
            mock_export.assert_not_called()

        assert len(paths) > 0

    async def test_cache_miss_evicts_stale_checksums(
        self, tmp_path: Path, monkeypatch: object,
    ) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # type: ignore[arg-type]

        project = tmp_path / "proj"
        project.mkdir()

        await ensure_skills(project, refine=False)

        cache_root = tmp_path / ".factory" / "cache" / "skills"
        first_dirs = list(cache_root.iterdir())
        assert len(first_dirs) == 1
        old_checksum_dir = first_dirs[0]

        different_workflow = {
            "alt": _make_workflow("alt", "echo changed"),
        }
        monkeypatch.setattr(
            "factory.workflow.definitions.register_all",
            lambda: different_workflow,
        )

        await ensure_skills(project, refine=False)

        remaining = [d for d in cache_root.iterdir() if d.is_dir()]
        assert len(remaining) == 1
        assert remaining[0] != old_checksum_dir

    async def test_only_workflow_dirs_copied(
        self, tmp_path: Path, monkeypatch: object,
    ) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # type: ignore[arg-type]

        project = tmp_path / "proj"
        project.mkdir()

        hand_written = project / "skills" / "implement"
        hand_written.mkdir(parents=True)
        marker = hand_written / "SKILL.md"
        marker.write_text("hand-written")

        await ensure_skills(project, refine=False)

        assert marker.read_text() == "hand-written"
