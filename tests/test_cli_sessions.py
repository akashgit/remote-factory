"""Tests for factory sessions CLI commands (list, prune, resume)."""

import subprocess
from pathlib import Path

import pytest

from factory.cli import main
from factory.run_index import RunMetadata, list_runs, read_run, write_run

pytestmark = pytest.mark.real_worktree


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    """Create a minimal git project with .factory/ directory."""
    project = tmp_path / "project"
    project.mkdir()

    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }

    subprocess.run(["git", "init", "-b", "main"], cwd=project, capture_output=True, check=True)
    (project / ".gitignore").write_text(".factory/\n")
    (project / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=project, capture_output=True, check=True, env=env,
    )

    factory_dir = project / ".factory"
    factory_dir.mkdir()
    (factory_dir / "config.json").write_text("{}")

    return project


def _seed_run(project: Path, run_id: str, status: str = "completed", created_at: str = "2026-06-28T12:00:00") -> None:
    """Seed a run metadata entry and create the corresponding git branch."""
    branch = f"factory/run-{run_id}"
    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
        "HOME": str(project.parent),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(
        ["git", "branch", branch],
        cwd=project, capture_output=True, check=True, env=env,
    )
    write_run(project, RunMetadata(
        run_id=run_id,
        branch=branch,
        worktree_path=str(project / ".factory-worktrees" / f"run-{run_id}"),
        created_at=created_at,
        mode="improve",
        status=status,
    ))


class TestSessionsList:
    def test_no_sessions(self, git_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        ret = main(["sessions", "list", str(git_project)])
        assert ret == 0
        assert "No sessions found" in capsys.readouterr().out

    def test_lists_sessions(self, git_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _seed_run(git_project, "aabb1122", status="completed")
        _seed_run(git_project, "ccdd3344", status="active")

        ret = main(["sessions", "list", str(git_project)])
        assert ret == 0
        output = capsys.readouterr().out
        assert "aabb1122" in output
        assert "ccdd3344" in output

    def test_filter_by_status(self, git_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _seed_run(git_project, "aabb1122", status="completed")
        _seed_run(git_project, "ccdd3344", status="active")

        ret = main(["sessions", "list", str(git_project), "--status", "active"])
        assert ret == 0
        output = capsys.readouterr().out
        assert "ccdd3344" in output
        assert "aabb1122" not in output


class TestSessionsPrune:
    def test_prune_completed(self, git_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _seed_run(git_project, "aabb1122", status="completed")

        ret = main(["sessions", "prune", str(git_project)])
        assert ret == 0
        output = capsys.readouterr().out
        assert "Pruned" in output

        assert read_run(git_project, "aabb1122") is None

        result = subprocess.run(
            ["git", "branch", "--list", "factory/run-aabb1122"],
            cwd=git_project, capture_output=True, text=True,
        )
        assert "factory/run-aabb1122" not in result.stdout

    def test_prune_dry_run(self, git_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _seed_run(git_project, "aabb1122", status="completed")

        ret = main(["sessions", "prune", str(git_project), "--dry-run"])
        assert ret == 0
        output = capsys.readouterr().out
        assert "Would prune" in output

        assert read_run(git_project, "aabb1122") is not None

    def test_prune_nothing(self, git_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        ret = main(["sessions", "prune", str(git_project)])
        assert ret == 0
        assert "Nothing to prune" in capsys.readouterr().out

    def test_prune_does_not_touch_active(self, git_project: Path) -> None:
        _seed_run(git_project, "active01", status="active")
        _seed_run(git_project, "done0001", status="completed")

        main(["sessions", "prune", str(git_project)])

        assert read_run(git_project, "active01") is not None
        assert read_run(git_project, "done0001") is None


class TestSessionsResume:
    def test_resume_reconstructs_worktree(self, git_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from factory.worktree import create_worktree, remove_worktree

        wt_path, branch = create_worktree(git_project)
        runs = list_runs(git_project)
        run_id = runs[0].run_id

        remove_worktree(git_project, wt_path, branch)
        assert not wt_path.exists()

        ret = main(["sessions", "resume", str(git_project), run_id])
        assert ret == 0
        output = capsys.readouterr().out
        assert "Reconstructed worktree" in output

        assert wt_path.exists()

        meta = read_run(git_project, run_id)
        assert meta is not None
        assert meta.status == "active"

    def test_resume_nonexistent_session(self, git_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        ret = main(["sessions", "resume", str(git_project), "nonexistent"])
        assert ret == 1
        assert "No session found" in capsys.readouterr().err
