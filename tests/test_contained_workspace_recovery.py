"""What the workspace helpers do when the filesystem or git says no.

The happy paths are covered elsewhere; these are the directions where a wrong answer is silent.
`merge_hint` and `cleanup_hint` are the only route a user has back to their work after a run, so a
hint that names the wrong mechanism loses it — an rsync merge printed for a git worktree sends them
at a tree whose branch they then never merge.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.contained.workspace import (
    Workspace,
    WorkspaceError,
    cleanup_hint,
    git_common_dir,
    merge_hint,
    release,
)


def _completed(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


# --------------------------------------------------------------------------------------------
# git_common_dir — the mount a worktree cannot work without
# --------------------------------------------------------------------------------------------


def test_a_non_repository_has_no_common_git_dir(tmp_path: Path) -> None:
    """The caller mounts what this returns; a fabricated path would mount a directory that does
    not exist and every git command inside would fail on it."""
    with patch("factory.contained.workspace.subprocess.run",
               return_value=_completed("", returncode=128)):
        assert git_common_dir(tmp_path) is None


def test_an_empty_answer_is_treated_as_no_common_git_dir(tmp_path: Path) -> None:
    with patch("factory.contained.workspace.subprocess.run", return_value=_completed("  \n")):
        assert git_common_dir(tmp_path) is None


# --------------------------------------------------------------------------------------------
# The two failure paths in copying
# --------------------------------------------------------------------------------------------


def test_a_missing_rsync_names_the_install_command(tmp_path: Path) -> None:
    """rsync is the copier for both kinds of workspace, so its absence stops everything — and
    "command not found" from inside a subprocess names nothing the user can act on."""
    from factory.contained.workspace import _rsync

    with patch("factory.contained.workspace.shutil.which", return_value=None):
        with pytest.raises(WorkspaceError, match="brew install rsync"):
            _rsync(tmp_path, tmp_path, exclude=(), delete=False)


def test_a_failed_copy_reports_rsyncs_own_error(tmp_path: Path) -> None:
    from factory.contained.workspace import _rsync

    with patch("factory.contained.workspace.shutil.which", return_value="/usr/bin/rsync"), \
         patch("factory.contained.workspace.subprocess.run",
               return_value=_completed("", returncode=23, stderr="permission denied")):
        with pytest.raises(WorkspaceError, match="permission denied"):
            _rsync(tmp_path, tmp_path, exclude=(), delete=False)


def test_a_failed_git_command_reports_gits_own_error(tmp_path: Path) -> None:
    from factory.contained.workspace import _git

    with patch("factory.contained.workspace.subprocess.run",
               return_value=_completed("", returncode=128, stderr="not a git repository")):
        with pytest.raises(WorkspaceError, match="not a git repository"):
            _git(tmp_path, ["worktree", "prune"])


# --------------------------------------------------------------------------------------------
# Getting the work back
# --------------------------------------------------------------------------------------------


def test_a_worktree_is_merged_with_git_not_rsync() -> None:
    ws = Workspace(source=Path("/src"), path=Path("/copy"), kind="worktree", branch="contained/x")
    hint = merge_hint(ws)
    assert "git -C /src merge contained/x" in hint
    assert "rsync" not in hint


def test_a_plain_copy_is_merged_with_rsync_and_keeps_git_out_of_it() -> None:
    """Rsyncing the copy's `.git` over the source's would overwrite the source repository."""
    ws = Workspace(source=Path("/src"), path=Path("/copy"), kind="copy")
    hint = merge_hint(ws)
    assert "rsync -a --exclude .git /copy/ /src/" in hint
    assert "git merge" not in hint


def test_a_worktree_with_no_branch_falls_back_to_the_copy_wording() -> None:
    """There is nothing to merge from, so naming a branch would be a lie."""
    ws = Workspace(source=Path("/src"), path=Path("/copy"), kind="worktree", branch=None)
    assert "rsync" in merge_hint(ws)


def test_cleanup_of_a_worktree_names_both_the_registration_and_the_branch() -> None:
    """Deleting the directory by hand leaves a stale registration that blocks the next run of the
    same name — the failure names a directory that no longer exists."""
    ws = Workspace(source=Path("/src"), path=Path("/copy"), kind="worktree", branch="contained/x")
    hint = cleanup_hint(ws)
    assert "worktree remove /copy" in hint and "branch -D contained/x" in hint


def test_cleanup_of_a_plain_copy_is_a_single_rm() -> None:
    ws = Workspace(source=Path("/src"), path=Path("/copy"), kind="copy")
    assert cleanup_hint(ws) == "Remove the copy with:  rm -rf /copy"


# --------------------------------------------------------------------------------------------
# release
# --------------------------------------------------------------------------------------------


def test_releasing_a_worktree_keeps_its_branch_by_default() -> None:
    """The branch is where the run's work is."""
    ws = Workspace(source=Path("/src"), path=Path("/copy"), kind="worktree", branch="contained/x")
    with patch("factory.contained.workspace.subprocess.run", return_value=_completed()) as run:
        release(ws)
    assert not any("branch" in c.args[0] for c in run.call_args_list)


def test_releasing_with_delete_branch_also_removes_the_branch() -> None:
    """Only for a launch that failed before the factory ever started — provably no work to lose."""
    ws = Workspace(source=Path("/src"), path=Path("/copy"), kind="worktree", branch="contained/x")
    with patch("factory.contained.workspace.subprocess.run", return_value=_completed()) as run:
        release(ws, delete_branch=True)
    assert any(c.args[0][-2:] == ["-D", "contained/x"] for c in run.call_args_list)


def test_a_branch_that_cannot_be_deleted_does_not_turn_cleanup_into_a_second_error() -> None:
    ws = Workspace(source=Path("/src"), path=Path("/copy"), kind="worktree", branch="contained/x")
    with patch("factory.contained.workspace.subprocess.run",
               side_effect=[_completed(), _completed("", returncode=1, stderr="not fully merged")]):
        release(ws, delete_branch=True)


def test_releasing_a_plain_copy_removes_the_directory(tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "f.txt").write_text("x")
    release(Workspace(source=tmp_path, path=copy, kind="copy"))
    assert not copy.exists()


def test_releasing_a_copy_that_is_already_gone_is_not_an_error(tmp_path: Path) -> None:
    """Cleanup runs on the failure path, where the thing may already have been removed."""
    release(Workspace(source=tmp_path, path=tmp_path / "never-existed", kind="copy"))
