"""Workspace materialization, provenance probes, and lifecycle over factory-created runtimes."""

from __future__ import annotations

import argparse
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.contained import lifecycle
from factory.contained.lifecycle import Runtime, render_table, resolve_runtime
from factory.contained.provenance import content_probe, provenance_probes
from factory.contained.workspace import (
    contained_home,
    git_common_dir,
    materialize,
    merge_hint,
    plan_workspace,
    release,
)


@pytest.fixture()
def git_project(tmp_path: Path) -> Path:
    project = tmp_path / "rta"
    project.mkdir()
    (project / "README.md").write_text("# rta\n")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=project, check=True,
    )
    return project


@pytest.fixture()
def contained_root(tmp_path: Path):
    root = tmp_path / "contained-home"
    with patch.dict(os.environ, {"FACTORY_CONTAINED_HOME": str(root)}, clear=False):
        yield root


# --------------------------------------------------------------------------------------------
# The workspace is a copy (§3.2), and it always starts from the local tree (§2.1a)
# --------------------------------------------------------------------------------------------


def test_plan_workspace_touches_nothing(git_project: Path, contained_root: Path) -> None:
    ws = plan_workspace(git_project, "rta-abc123")
    assert ws.kind == "worktree"
    assert ws.branch == "contained/rta-abc123"
    assert ws.path == contained_root / "rta-abc123" / "rta"
    assert not contained_root.exists()


def test_git_project_becomes_a_worktree_on_a_branch(
    git_project: Path, contained_root: Path
) -> None:
    ws = materialize(git_project, "rta-abc123")
    assert ws.path.is_dir()
    assert (ws.path / "README.md").read_text() == "# rta\n"
    branch = subprocess.run(
        ["git", "-C", str(ws.path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert branch == "contained/rta-abc123"
    release(ws)


def test_the_copy_carries_uncommitted_work(git_project: Path, contained_root: Path) -> None:
    """The whole point of a contained run is to exercise code that is not committed yet."""
    (git_project / "README.md").write_text("# rta, edited\n")
    (git_project / "untracked.txt").write_text("new\n")
    factory_dir = git_project / ".factory"
    factory_dir.mkdir()
    (factory_dir / "config.json").write_text("{}")

    ws = materialize(git_project, "rta-abc123")
    assert (ws.path / "README.md").read_text() == "# rta, edited\n"
    assert (ws.path / "untracked.txt").exists()
    # .factory/ is gitignored by convention, so a HEAD checkout alone would lose the whole
    # experiment history.
    assert (ws.path / ".factory" / "config.json").exists()
    release(ws)


def test_the_host_tree_is_untouched(git_project: Path, contained_root: Path) -> None:
    ws = materialize(git_project, "rta-abc123")
    (ws.path / "written-by-the-run.txt").write_text("x\n")
    status = subprocess.run(
        ["git", "-C", str(git_project), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert status == ""
    assert not (git_project / "written-by-the-run.txt").exists()
    release(ws)


def test_a_non_git_project_is_copied_not_worktreed(tmp_path: Path, contained_root: Path) -> None:
    project = tmp_path / "plain"
    project.mkdir()
    (project / "a.txt").write_text("a\n")
    ws = materialize(project, "plain-abc123")
    assert ws.kind == "copy"
    assert ws.branch is None
    assert (ws.path / "a.txt").exists()


def test_materialize_is_idempotent_and_keeps_in_progress_work(
    git_project: Path, contained_root: Path
) -> None:
    ws = materialize(git_project, "rta-abc123")
    (ws.path / "in-progress.txt").write_text("half done\n")
    again = materialize(git_project, "rta-abc123")
    assert again.path == ws.path
    assert (ws.path / "in-progress.txt").exists()
    release(ws)


def test_the_source_repository_git_dir_is_discoverable(git_project: Path) -> None:
    """A worktree's .git is a *file*; without the source's git dir mounted, git fails inside."""
    common = git_common_dir(git_project)
    assert common is not None
    assert common.is_dir()
    assert common.name == ".git"


def test_merge_hint_never_merges(git_project: Path, contained_root: Path) -> None:
    ws = materialize(git_project, "rta-abc123")
    hint = merge_hint(ws)
    assert "contained/rta-abc123" in hint
    assert str(ws.path) in hint
    assert "git -C" in hint and "merge" in hint
    release(ws)


def test_contained_home_is_not_nested_under_factory_home() -> None:
    """~/.factory is itself bind-mounted read-write; nesting would overlap two bind mounts."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("FACTORY_CONTAINED_HOME", None)
        home = contained_home()
    factory_home = Path("~/.factory").expanduser()
    assert home != factory_home
    assert factory_home not in home.parents


# --------------------------------------------------------------------------------------------
# Provenance (§2.1a)
# --------------------------------------------------------------------------------------------


def test_probes_are_conditional_on_what_the_host_actually_has() -> None:
    names = [p.name for p in provenance_probes(
        "/w", expect_factory_state=False, expect_git=False, content=None
    )]
    assert names == ["project_present", "writable"]

    names = [p.name for p in provenance_probes(
        "/w", expect_factory_state=True, expect_git=True, content=("a.txt", "deadbeef")
    )]
    assert names == ["project_present", "git_usable", "factory_state", "writable", "content_hash"]


def test_every_probe_carries_a_hint_naming_the_consequence() -> None:
    for probe in provenance_probes(
        "/w", expect_factory_state=True, expect_git=True, content=("a.txt", "deadbeef")
    ):
        assert probe.hint, f"{probe.name} has no hint"
        assert len(probe.hint) > 40


def test_writable_probe_writes_rather_than_reading_mode_bits() -> None:
    """Mode bits can say writable while the mount is read-only in practice."""
    probe = next(
        p for p in provenance_probes("/w", expect_factory_state=False, expect_git=False,
                                     content=None)
        if p.name == "writable"
    )
    assert "touch" in " ".join(probe.argv)


def test_content_probe_hashes_the_largest_file_outside_git(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_text("x")
    (tmp_path / "big.txt").write_text("y" * 5000)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "huge").write_text("z" * 100000)
    result = content_probe(tmp_path)
    assert result is not None
    assert result[0] == "big.txt"


def test_content_probe_skips_rather_than_fakes_an_empty_tree(tmp_path: Path) -> None:
    assert content_probe(tmp_path) is None


# --------------------------------------------------------------------------------------------
# Lifecycle acts only on factory-created runtimes (§2.3)
# --------------------------------------------------------------------------------------------


def _entry(name: str, *, ours: bool = True, state: str = "running") -> dict[str, object]:
    labels = {"factory.contained": "true", "factory.project": "deadbeef"} if ours else {"app": "x"}
    return {"Names": [name], "Labels": labels, "State": state, "Created": 1_700_000_000}


def test_only_labelled_containers_are_listed() -> None:
    with patch(
        "factory.contained.lifecycle._podman_entries",
        return_value=[_entry("ours"), _entry("theirs", ours=False)],
    ):
        runtimes = lifecycle.local_runtimes()
    assert [r.name for r in runtimes] == ["ours"]


def test_attach_refuses_a_container_the_factory_did_not_create(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("factory.contained.lifecycle._podman_entries", return_value=[]), \
         patch("factory.contained.lifecycle.subprocess.call") as call:
        code = lifecycle.attach("theirs", "local")
    call.assert_not_called()
    assert code == 1
    assert "not a runtime" in capsys.readouterr().err


def test_rm_refuses_a_container_the_factory_did_not_create() -> None:
    with patch("factory.contained.lifecycle._podman_entries", return_value=[]), \
         patch("factory.contained.lifecycle.subprocess.call") as call:
        code = lifecycle.remove("theirs", "local", assume_yes=True)
    call.assert_not_called()
    assert code == 1


def test_rm_prompts_before_deleting_an_active_run(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("factory.contained.lifecycle._podman_entries", return_value=[_entry("ours")]), \
         patch("factory.contained.lifecycle.subprocess.call") as call:
        code = lifecycle.remove("ours", "local", assume_yes=False, interactive=False)
    call.assert_not_called()
    assert code == 1
    assert "--yes" in capsys.readouterr().err


def test_rm_deletes_a_stopped_run_without_prompting() -> None:
    with patch(
        "factory.contained.lifecycle._podman_entries",
        return_value=[_entry("ours", state="exited")],
    ), patch("factory.contained.lifecycle.subprocess.run",
             return_value=subprocess.CompletedProcess([], 0, "ours\n", "")) as run:
        code = lifecycle.remove("ours", "local", assume_yes=False, interactive=False)
    assert code == 0
    assert run.call_args[0][0][:2] == ["podman", "rm"]


def test_rm_does_not_echo_podmans_own_output(capsys: pytest.CaptureFixture[str]) -> None:
    """podman prints the name it removed; our own report follows, and the pair reads as a stutter."""
    with patch(
        "factory.contained.lifecycle._podman_entries",
        return_value=[_entry("ours", state="exited")],
    ), patch("factory.contained.lifecycle.subprocess.run",
             return_value=subprocess.CompletedProcess([], 0, "ours\n", "")):
        lifecycle.remove("ours", "local", assume_yes=True, interactive=False)
    out = capsys.readouterr().out
    assert not out.startswith("ours\n")


def test_reap_stale_leaves_a_running_container_alone() -> None:
    with patch("factory.contained.lifecycle._podman_entries", return_value=[_entry("ours")]), \
         patch("factory.contained.lifecycle.subprocess.call") as call:
        reaped, detail = lifecycle.reap_stale("ours")
    call.assert_not_called()
    assert not reaped
    assert "still active" in detail


def test_reap_stale_removes_a_dead_one_of_ours() -> None:
    with patch(
        "factory.contained.lifecycle._podman_entries",
        return_value=[_entry("ours", state="exited")],
    ), patch("factory.contained.lifecycle.subprocess.call", return_value=0):
        reaped, detail = lifecycle.reap_stale("ours")
    assert reaped
    assert "removed stale" in detail


def test_sync_reports_a_merge_command_and_merges_nothing(
    git_project: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = materialize(git_project, "rta-abc123")
    with patch(
        "factory.contained.lifecycle._podman_entries",
        return_value=[_entry("rta-abc123", state="exited")],
    ):
        code = lifecycle.sync("rta-abc123", "local")
    out = capsys.readouterr().out
    assert code == 0
    assert "contained/rta-abc123" in out
    assert "merge" in out
    # Nothing moved.
    assert subprocess.run(
        ["git", "-C", str(git_project), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout == ""
    release(ws)


def test_render_table_reports_ages_and_states() -> None:
    created = datetime.now(timezone.utc) - timedelta(hours=3)
    table = render_table(
        [Runtime(name="rta-abc", target="local", project="deadbeef", state="running",
                 created=created)]
    )
    assert "rta-abc" in table and "local" in table and "3h" in table and "running" in table


def test_render_table_on_an_empty_fleet_points_at_how_to_start_one() -> None:
    assert "factory contained --" in render_table([])


def test_resolve_runtime_matches_by_name() -> None:
    runtimes = [Runtime(name="a", target="local", project="p", state="running")]
    assert resolve_runtime("a", runtimes) is not None
    assert resolve_runtime("b", runtimes) is None


def test_dispatch_requires_a_name_for_name_taking_subcommands() -> None:
    args = argparse.Namespace(subcommand="attach", name=None, target="local")
    assert lifecycle.dispatch_lifecycle(args) == 2
