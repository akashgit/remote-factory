"""Tests for factory.registry — global project registry."""

from pathlib import Path

from factory.registry import (
    _load_registry,
    get_project_paths,
    list_projects,
    populate_from_directory,
    register_project,
    update_project_stats,
)


def test_register_project(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    project = tmp_path / "my-project"
    project.mkdir()

    register_project(project, registry_path=registry_path)

    entries = list_projects(registry_path=registry_path)
    assert len(entries) == 1
    assert entries[0].name == "my-project"
    assert entries[0].path == str(project.resolve())


def test_register_project_idempotent(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    project = tmp_path / "my-project"
    project.mkdir()

    register_project(project, registry_path=registry_path)
    register_project(project, registry_path=registry_path)

    entries = list_projects(registry_path=registry_path)
    assert len(entries) == 1


def test_update_project_stats(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    project = tmp_path / "my-project"
    project.mkdir()

    register_project(project, registry_path=registry_path)
    update_project_stats(
        project, experiment_count=5, latest_score=0.85,
        registry_path=registry_path,
    )

    entries = list_projects(registry_path=registry_path)
    assert entries[0].experiment_count == 5
    assert entries[0].latest_score == 0.85
    assert entries[0].last_experiment_at is not None


def test_update_project_stats_not_found(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    project = tmp_path / "my-project"
    project.mkdir()

    # Should not raise — just logs a warning
    update_project_stats(
        project, experiment_count=5,
        registry_path=registry_path,
    )


def test_get_project_paths(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    project1 = tmp_path / "project1"
    project1.mkdir()
    project2 = tmp_path / "project2"
    project2.mkdir()

    register_project(project1, registry_path=registry_path)
    register_project(project2, registry_path=registry_path)

    paths = get_project_paths(registry_path=registry_path)
    assert len(paths) == 2


def test_get_project_paths_filters_missing(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    project = tmp_path / "exists"
    project.mkdir()
    missing = tmp_path / "gone"
    missing.mkdir()

    register_project(project, registry_path=registry_path)
    register_project(missing, registry_path=registry_path)

    # Remove "gone" after registration
    missing.rmdir()

    paths = get_project_paths(registry_path=registry_path)
    assert len(paths) == 1
    assert paths[0] == project.resolve()


def test_load_registry_missing(tmp_path: Path) -> None:
    registry_path = tmp_path / "nonexistent.json"
    registry = _load_registry(registry_path)
    assert registry.projects == []


def test_load_registry_corrupt(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text("not json")
    registry = _load_registry(registry_path)
    assert registry.projects == []


def test_populate_from_directory(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"

    # Create a project with .factory/results.tsv
    project = tmp_path / "projects" / "proj1"
    factory_dir = project / ".factory"
    factory_dir.mkdir(parents=True)
    (factory_dir / "results.tsv").write_text("id\ttimestamp\thypothesis\n")

    added = populate_from_directory(
        tmp_path / "projects", registry_path=registry_path,
    )
    assert added == 1

    entries = list_projects(registry_path=registry_path)
    assert len(entries) == 1
    assert entries[0].name == "proj1"


def test_populate_from_directory_idempotent(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"

    project = tmp_path / "projects" / "proj1"
    factory_dir = project / ".factory"
    factory_dir.mkdir(parents=True)
    (factory_dir / "results.tsv").write_text("id\ttimestamp\thypothesis\n")

    added1 = populate_from_directory(
        tmp_path / "projects", registry_path=registry_path,
    )
    added2 = populate_from_directory(
        tmp_path / "projects", registry_path=registry_path,
    )

    assert added1 == 1
    assert added2 == 0
