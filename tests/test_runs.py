"""Tests for factory/runs.py — run metadata CRUD operations."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory.runs import (
    RunMetadata,
    SessionRunStatus,
    delete_run,
    list_runs,
    load_run,
    prune_runs,
    save_run,
    update_run,
)


def _make_run(run_id: str = "abcd1234", **overrides: object) -> RunMetadata:
    defaults: dict[str, object] = {
        "run_id": run_id,
        "branch": f"factory/run-{run_id}",
        "worktree_path": f"/tmp/wt/run-{run_id}",
        "base_branch": "main",
        "status": SessionRunStatus.running,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "improve",
    }
    defaults.update(overrides)
    return RunMetadata.model_validate(defaults)


class TestSaveAndLoad:
    def test_round_trip(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / ".factory").mkdir(parents=True)

        meta = _make_run()
        save_run(project, meta)

        loaded = load_run(project, meta.run_id)
        assert loaded is not None
        assert loaded.run_id == meta.run_id
        assert loaded.branch == meta.branch
        assert loaded.status == SessionRunStatus.running

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / ".factory").mkdir(parents=True)

        assert load_run(project, "nonexistent") is None


class TestListRuns:
    def test_list_empty(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / ".factory").mkdir(parents=True)

        assert list_runs(project) == []

    def test_list_multiple(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / ".factory").mkdir(parents=True)

        save_run(project, _make_run("aaaa1111"))
        save_run(project, _make_run("bbbb2222"))
        save_run(project, _make_run("cccc3333"))

        runs = list_runs(project)
        assert len(runs) == 3
        ids = {r.run_id for r in runs}
        assert ids == {"aaaa1111", "bbbb2222", "cccc3333"}

    def test_list_no_runs_dir(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        assert list_runs(project) == []


class TestUpdateRun:
    def test_update_status(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / ".factory").mkdir(parents=True)

        save_run(project, _make_run())
        updated = update_run(
            project, "abcd1234",
            status=SessionRunStatus.completed,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        assert updated is not None
        assert updated.status == SessionRunStatus.completed
        assert updated.completed_at is not None

        reloaded = load_run(project, "abcd1234")
        assert reloaded is not None
        assert reloaded.status == SessionRunStatus.completed

    def test_update_nonexistent(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / ".factory").mkdir(parents=True)

        assert update_run(project, "nope", status=SessionRunStatus.error) is None


class TestDeleteRun:
    def test_delete_existing(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / ".factory").mkdir(parents=True)

        save_run(project, _make_run())
        assert delete_run(project, "abcd1234") is True
        assert load_run(project, "abcd1234") is None
        assert list_runs(project) == []

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / ".factory").mkdir(parents=True)

        assert delete_run(project, "nope") is False


class TestPruneRuns:
    def test_prune_old_runs(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / ".factory").mkdir(parents=True)

        old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        recent_ts = datetime.now(timezone.utc).isoformat()

        save_run(project, _make_run("old00001", created_at=old_ts, status=SessionRunStatus.completed))
        save_run(project, _make_run("new00001", created_at=recent_ts, status=SessionRunStatus.completed))

        pruned = prune_runs(project, older_than_days=30)
        assert len(pruned) == 1
        assert "old00001" in pruned[0]

        remaining = list_runs(project)
        assert len(remaining) == 1
        assert remaining[0].run_id == "new00001"

    def test_prune_dry_run(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / ".factory").mkdir(parents=True)

        old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        save_run(project, _make_run("old00001", created_at=old_ts, status=SessionRunStatus.completed))

        pruned = prune_runs(project, older_than_days=30, dry_run=True)
        assert len(pruned) == 1
        assert "Would prune" in pruned[0]
        assert load_run(project, "old00001") is not None

    def test_prune_skips_running(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / ".factory").mkdir(parents=True)

        old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        save_run(project, _make_run("old00001", created_at=old_ts, status=SessionRunStatus.running))

        pruned = prune_runs(project, older_than_days=30)
        assert len(pruned) == 0
        assert load_run(project, "old00001") is not None

    def test_prune_all(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / ".factory").mkdir(parents=True)

        recent_ts = datetime.now(timezone.utc).isoformat()
        save_run(project, _make_run("run00001", created_at=recent_ts, status=SessionRunStatus.completed))
        save_run(project, _make_run("run00002", created_at=recent_ts, status=SessionRunStatus.error))

        pruned = prune_runs(project, prune_all=True)
        assert len(pruned) == 2
        assert list_runs(project) == []
