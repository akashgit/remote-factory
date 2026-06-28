"""Tests for factory/run_index.py — run metadata CRUD operations."""

from pathlib import Path

import pytest

from factory.run_index import (
    RunMetadata,
    delete_run,
    list_runs,
    read_run,
    update_status,
    write_run,
)


@pytest.fixture
def project_with_factory(tmp_path: Path) -> Path:
    """Create a minimal project directory with .factory/."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".factory").mkdir()
    return project


def _make_meta(
    run_id: str = "abcd1234",
    branch: str = "factory/run-abcd1234",
    status: str = "active",
    mode: str = "improve",
) -> RunMetadata:
    return RunMetadata(
        run_id=run_id,
        branch=branch,
        worktree_path=f"/tmp/worktrees/run-{run_id}",
        created_at="2026-06-28T12:00:00",
        mode=mode,
        status=status,
    )


class TestRunMetadataModel:
    def test_valid_model(self) -> None:
        meta = _make_meta()
        assert meta.run_id == "abcd1234"
        assert meta.status == "active"

    def test_rejects_invalid_status(self) -> None:
        with pytest.raises(Exception):
            _make_meta(status="invalid")

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(Exception):
            RunMetadata(
                run_id="x",
                branch="b",
                worktree_path="/tmp",
                created_at="2026-01-01T00:00:00",
                mode="improve",
                status="active",
                extra_field="bad",
            )


class TestWriteAndRead:
    def test_write_creates_file(self, project_with_factory: Path) -> None:
        meta = _make_meta()
        write_run(project_with_factory, meta)

        run_file = project_with_factory / ".factory" / "runs" / "abcd1234.json"
        assert run_file.exists()

    def test_read_returns_written_data(self, project_with_factory: Path) -> None:
        meta = _make_meta()
        write_run(project_with_factory, meta)

        loaded = read_run(project_with_factory, "abcd1234")
        assert loaded is not None
        assert loaded.run_id == "abcd1234"
        assert loaded.branch == "factory/run-abcd1234"
        assert loaded.status == "active"
        assert loaded.mode == "improve"

    def test_read_nonexistent_returns_none(self, project_with_factory: Path) -> None:
        assert read_run(project_with_factory, "nonexistent") is None

    def test_write_is_idempotent(self, project_with_factory: Path) -> None:
        meta = _make_meta()
        write_run(project_with_factory, meta)
        write_run(project_with_factory, meta)

        loaded = read_run(project_with_factory, "abcd1234")
        assert loaded is not None

    def test_creates_runs_directory(self, project_with_factory: Path) -> None:
        runs_dir = project_with_factory / ".factory" / "runs"
        assert not runs_dir.exists()

        write_run(project_with_factory, _make_meta())
        assert runs_dir.is_dir()


class TestListRuns:
    def test_empty_when_no_runs(self, project_with_factory: Path) -> None:
        assert list_runs(project_with_factory) == []

    def test_lists_all_runs(self, project_with_factory: Path) -> None:
        write_run(project_with_factory, _make_meta(run_id="aaaa1111", branch="factory/run-aaaa1111"))
        write_run(project_with_factory, _make_meta(run_id="bbbb2222", branch="factory/run-bbbb2222"))

        runs = list_runs(project_with_factory)
        assert len(runs) == 2
        ids = {r.run_id for r in runs}
        assert ids == {"aaaa1111", "bbbb2222"}

    def test_sorted_by_created_at_desc(self, project_with_factory: Path) -> None:
        write_run(project_with_factory, RunMetadata(
            run_id="old",
            branch="factory/run-old",
            worktree_path="/tmp/old",
            created_at="2026-01-01T00:00:00",
            mode="improve",
            status="completed",
        ))
        write_run(project_with_factory, RunMetadata(
            run_id="new",
            branch="factory/run-new",
            worktree_path="/tmp/new",
            created_at="2026-06-28T12:00:00",
            mode="improve",
            status="active",
        ))

        runs = list_runs(project_with_factory)
        assert runs[0].run_id == "new"
        assert runs[1].run_id == "old"

    def test_skips_corrupt_files(self, project_with_factory: Path) -> None:
        write_run(project_with_factory, _make_meta())
        runs_dir = project_with_factory / ".factory" / "runs"
        (runs_dir / "corrupt.json").write_text("not json{{{")

        runs = list_runs(project_with_factory)
        assert len(runs) == 1


class TestUpdateStatus:
    def test_updates_status(self, project_with_factory: Path) -> None:
        write_run(project_with_factory, _make_meta(status="active"))

        result = update_status(project_with_factory, "abcd1234", "completed")
        assert result is True

        loaded = read_run(project_with_factory, "abcd1234")
        assert loaded is not None
        assert loaded.status == "completed"

    def test_returns_false_for_nonexistent(self, project_with_factory: Path) -> None:
        result = update_status(project_with_factory, "nonexistent", "crashed")
        assert result is False


class TestDeleteRun:
    def test_deletes_run(self, project_with_factory: Path) -> None:
        write_run(project_with_factory, _make_meta())
        assert delete_run(project_with_factory, "abcd1234") is True
        assert read_run(project_with_factory, "abcd1234") is None

    def test_returns_false_for_nonexistent(self, project_with_factory: Path) -> None:
        assert delete_run(project_with_factory, "nonexistent") is False
