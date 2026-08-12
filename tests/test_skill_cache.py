"""Tests for factory.skill_cache — checksum-based workflow skill caching."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from factory.skill_cache import _compute_checksum, _sort_recursive, ensure_skills
from factory.workflow.definitions import register_all
from factory.workflow.primitives import AgentNode, AgentRole, FnNode, Workflow
from factory.workflow.registry import WorkflowRegistry


@pytest.fixture(autouse=True)
def _reset_workflow_registry():
    """Reset WorkflowRegistry state between tests."""
    WorkflowRegistry.reset()
    yield
    WorkflowRegistry.reset()


def _make_workflow(name: str = "test", cmd: str = "echo hi") -> Workflow:
    return Workflow(
        name=name,
        nodes={"a": FnNode(id="a", command=cmd)},
        edges=[],
        start_node="a",
    )


SAMPLE_WORKFLOW_PY = """\
from factory.workflow.primitives import FnNode, Workflow

meta = {"name": "test_mode", "description": "A test project-local workflow"}

def workflow():
    return Workflow(
        name="test_mode",
        nodes={"start": FnNode(id="start", command="echo hello")},
        edges=[],
        start_node="start",
    )
"""


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
    def test_cache_miss(self, tmp_path: Path, monkeypatch: object) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # type: ignore[arg-type]

        project = tmp_path / "proj"
        project.mkdir()

        paths = ensure_skills(project)
        assert len(paths) > 0
        assert all(p.name == "SKILL.md" for p in paths)

        cache_root = tmp_path / ".factory" / "cache" / "skills"
        assert cache_root.exists()

    def test_cache_hit(self, tmp_path: Path, monkeypatch: object) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # type: ignore[arg-type]

        project = tmp_path / "proj"
        project.mkdir()

        ensure_skills(project)

        with patch(
            "factory.workflow.skill_export.export_all_skills",
            wraps=None,
        ) as mock_export:
            mock_export.return_value = []
            paths = ensure_skills(project)
            mock_export.assert_not_called()

        assert len(paths) > 0

    def test_cache_miss_evicts_stale_checksums(self, tmp_path: Path, monkeypatch: object) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # type: ignore[arg-type]

        project = tmp_path / "proj"
        project.mkdir()

        ensure_skills(project)

        cache_root = tmp_path / ".factory" / "cache" / "skills"
        first_dirs = list(cache_root.iterdir())
        assert len(first_dirs) == 1
        old_checksum_dir = first_dirs[0]

        different_registry = {"alt": lambda: _make_workflow("alt", "echo changed")}
        monkeypatch.setattr(
            "factory.workflow.definitions._get_builtin_registry",
            lambda: different_registry,
        )

        ensure_skills(project)

        remaining = [d for d in cache_root.iterdir() if d.is_dir()]
        assert len(remaining) == 1
        assert remaining[0] != old_checksum_dir

    def test_only_workflow_dirs_copied(self, tmp_path: Path, monkeypatch: object) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # type: ignore[arg-type]

        project = tmp_path / "proj"
        project.mkdir()

        hand_written = project / "skills" / "implement"
        hand_written.mkdir(parents=True)
        marker = hand_written / "SKILL.md"
        marker.write_text("hand-written")

        ensure_skills(project)

        assert marker.read_text() == "hand-written"


class TestProjectLocalWorkflows:
    def test_discovers_project_local_workflow(self, tmp_path: Path, monkeypatch: object) -> None:
        """ensure_skills() discovers and generates skills for a project-local workflow."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # type: ignore[arg-type]

        project = tmp_path / "proj"
        project.mkdir()
        wf_dir = project / ".factory" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "test_mode.py").write_text(SAMPLE_WORKFLOW_PY)

        paths = ensure_skills(project)
        skill_names = [p.parent.name for p in paths]
        assert "workflow-test_mode" in skill_names

        skill_md = project / "skills" / "workflow-test_mode" / "SKILL.md"
        assert skill_md.exists()

    def test_project_local_always_regenerated(self, tmp_path: Path, monkeypatch: object) -> None:
        """Project-local workflows are always regenerated, not cached."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # type: ignore[arg-type]

        project = tmp_path / "proj"
        project.mkdir()
        wf_dir = project / ".factory" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "test_mode.py").write_text(SAMPLE_WORKFLOW_PY)

        ensure_skills(project)
        skill_md = project / "skills" / "workflow-test_mode" / "SKILL.md"
        first_content = skill_md.read_text()

        updated_py = SAMPLE_WORKFLOW_PY.replace("echo hello", "echo updated")
        (wf_dir / "test_mode.py").write_text(updated_py)

        ensure_skills(project)
        second_content = skill_md.read_text()
        assert second_content != first_content

    def test_builtins_still_use_cache(self, tmp_path: Path, monkeypatch: object) -> None:
        """Builtin workflows use the cache path, not direct regeneration."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # type: ignore[arg-type]

        project = tmp_path / "proj"
        project.mkdir()

        ensure_skills(project)

        cache_root = tmp_path / ".factory" / "cache" / "skills"
        cache_dirs = list(cache_root.iterdir())
        assert len(cache_dirs) == 1
        cached_skills = list(cache_dirs[0].glob("workflow-*"))
        assert len(cached_skills) > 0

    def test_project_local_not_in_cache_dir(self, tmp_path: Path, monkeypatch: object) -> None:
        """Project-local workflow skills go directly to project/skills/, not cache."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # type: ignore[arg-type]

        project = tmp_path / "proj"
        project.mkdir()
        wf_dir = project / ".factory" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "test_mode.py").write_text(SAMPLE_WORKFLOW_PY)

        ensure_skills(project)

        cache_root = tmp_path / ".factory" / "cache" / "skills"
        for cache_dir in cache_root.iterdir():
            cached_names = [d.name for d in cache_dir.iterdir() if d.is_dir()]
            assert "workflow-test_mode" not in cached_names
