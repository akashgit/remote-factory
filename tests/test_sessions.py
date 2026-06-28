"""CLI smoke tests for sessions-list, sessions-prune, sessions-resume commands."""

from datetime import datetime, timezone
from pathlib import Path

from factory.cli import main
from factory.runs import RunMetadata, SessionRunStatus, save_run


def _save_test_run(project: Path, run_id: str = "ae5f0001") -> None:
    save_run(project, RunMetadata(
        run_id=run_id,
        branch=f"factory/run-{run_id}",
        worktree_path=str(project / ".factory-worktrees" / f"run-{run_id}"),
        base_branch="main",
        status=SessionRunStatus.completed,
        created_at=datetime.now(timezone.utc).isoformat(),
        mode="improve",
    ))


def test_sessions_list_empty(tmp_project: Path) -> None:
    (tmp_project / ".factory").mkdir(exist_ok=True)
    code = main(["sessions-list", str(tmp_project)])
    assert code == 0


def test_sessions_list_with_runs(tmp_project: Path) -> None:
    (tmp_project / ".factory").mkdir(exist_ok=True)
    _save_test_run(tmp_project, "aaaa1111")
    _save_test_run(tmp_project, "bbbb2222")

    code = main(["sessions-list", str(tmp_project)])
    assert code == 0


def test_sessions_list_json(tmp_project: Path, capsys: object) -> None:
    (tmp_project / ".factory").mkdir(exist_ok=True)
    _save_test_run(tmp_project)

    code = main(["sessions-list", str(tmp_project), "--json"])
    assert code == 0


def test_sessions_list_status_filter(tmp_project: Path) -> None:
    (tmp_project / ".factory").mkdir(exist_ok=True)
    _save_test_run(tmp_project)

    code = main(["sessions-list", str(tmp_project), "--status", "running"])
    assert code == 0


def test_sessions_prune_dry_run(tmp_project: Path) -> None:
    (tmp_project / ".factory").mkdir(exist_ok=True)
    _save_test_run(tmp_project)

    code = main(["sessions-prune", str(tmp_project), "--dry-run"])
    assert code == 0


def test_sessions_prune_nothing(tmp_project: Path) -> None:
    (tmp_project / ".factory").mkdir(exist_ok=True)
    code = main(["sessions-prune", str(tmp_project)])
    assert code == 0


def test_sessions_resume_missing_run(tmp_project: Path) -> None:
    (tmp_project / ".factory").mkdir(exist_ok=True)
    code = main(["sessions-resume", str(tmp_project), "nonexistent"])
    assert code == 1
