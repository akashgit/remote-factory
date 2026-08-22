"""Coverage-completing tests for the `factory contained` CLI front door and the local runtime.

The three modules under test are pure orchestration: `contained.py` reads one interpreted namespace
and hands it to a peer, `contained_args.py` reads the command line, and `contained_local.py` composes
and runs one podman container. None of them should ever launch podman during a test, so every seam —
podman helpers, workspace materialization, credential/identity probes, the division server — is
mocked. What is asserted is the routing and the composition, not the side effects.

The existing `tests/test_contained.py` and `tests/test_contained_lifecycle.py` already cover the happy
paths reachable through dry-run; this file fills in the branches those cannot reach without either
launching podman or standing up a cluster.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from factory.cli import contained as cli
from factory.cli import contained_args
from factory.cli import contained_local
from factory.contained.credentials import CredentialShape
from factory.contained.errors import ContainedError
from factory.contained.provenance import Probe
from factory.contained.workspace import Workspace, WorkspaceError
from factory.podman import CONTAINER_HOME, LABEL_CONTAINED, LABEL_PROJECT, ContainerPlan, Mount, Step


# --------------------------------------------------------------------------------------------
# Shared helpers — a parser built the way the real CLI builds it, plus a minimal plan.
# --------------------------------------------------------------------------------------------


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="factory")
    sub = parser.add_subparsers(dest="command")
    cli.build_contained_parser(sub)
    return parser.parse_args(["contained", *argv])


def interpret(argv: list[str]) -> argparse.Namespace:
    args = parse(argv)
    cli.interpret(cli._PARSER, args)
    return args


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _plan(tmp_path: Path) -> ContainerPlan:
    workspace = tmp_path / "rta"
    workspace.mkdir(exist_ok=True)
    return ContainerPlan(
        name="rta-abc123",
        image="example/runtime:latest",
        workdir=str(workspace),
        env={"FACTORY_CONTAINED": "1", "HOME": CONTAINER_HOME},
        labels={LABEL_CONTAINED: "true", LABEL_PROJECT: "deadbeef"},
        mounts=(Mount(workspace, str(workspace)),),
        run_command=f"cd {workspace} && factory study {workspace}",
        user="501:0",
    )


class _FakeDivision:
    """Stand-in for `division.Division` — records whether the run kept or stopped the endpoint."""

    def __init__(self, plan: ContainerPlan) -> None:
        self.plan = plan
        self.kept = False
        self.stopped = False

    def keep(self) -> None:
        self.kept = True

    def stop(self) -> None:
        self.stopped = True


# ============================================================================================
# contained.py — the front door: dispatch routing, the Ctrl-C handler, and `_verify`.
# ============================================================================================


def test_keyboard_interrupt_is_caught_and_reported_as_130(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Backing out of a wizard is ordinary, so an interrupt is a message and 130, not a traceback."""
    with patch.object(cli, "_dispatch", side_effect=KeyboardInterrupt):
        code = cli.cmd_contained(argparse.Namespace())
    assert code == 130
    assert "Stopped." in capsys.readouterr().err


def test_context_is_pinned_once_when_given() -> None:
    """`--context` is applied globally so no downstream cluster command has to remember it."""
    args = interpret(["--target", "k8s", "--context", "ctx", "verify"])
    with patch("factory.contained.k8s.set_active_context") as pin, \
         patch("factory.contained.k8s_setup.verify_k8s", return_value=[SimpleNamespace(ok=True)]), \
         patch("factory.contained.prereq.format_check", return_value=""), \
         patch("factory.contained.prereq.summary_line", return_value="ok"):
        assert cli.cmd_contained(args) == 0
    pin.assert_called_once_with("ctx")


def test_verify_k8s_streams_and_returns_zero_when_all_pass(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = interpret(["--target", "k8s", "--namespace", "ns", "verify"])
    with patch(
        "factory.contained.k8s_setup.verify_k8s", return_value=[SimpleNamespace(ok=True)]
    ) as vk, \
         patch("factory.contained.prereq.format_check", return_value="line"), \
         patch("factory.contained.prereq.summary_line", return_value="summary"):
        assert cli.cmd_contained(args) == 0
    assert vk.call_args.kwargs["namespace"] == "ns"
    assert "summary" in capsys.readouterr().out


def test_verify_k8s_returns_one_when_a_check_fails() -> None:
    args = interpret(["--target", "k8s", "verify"])
    with patch("factory.contained.k8s_setup.verify_k8s", return_value=[SimpleNamespace(ok=False)]), \
         patch("factory.contained.prereq.format_check", return_value=""), \
         patch("factory.contained.prereq.summary_line", return_value=""):
        assert cli.cmd_contained(args) == 1


def test_verify_local_returns_zero_when_all_pass(capsys: pytest.CaptureFixture[str]) -> None:
    # `local_checks`/`render_checks` are imported into `contained.py` at module load, so they must
    # be patched there rather than at their source module.
    args = interpret(["verify"])
    with patch("factory.cli.contained.local_checks", return_value=[SimpleNamespace(ok=True)]), \
         patch("factory.cli.contained.render_checks", return_value="rendered"):
        assert cli.cmd_contained(args) == 0
    assert "rendered" in capsys.readouterr().out


def test_verify_local_returns_one_when_a_check_fails() -> None:
    args = interpret(["verify"])
    with patch("factory.cli.contained.local_checks", return_value=[SimpleNamespace(ok=False)]), \
         patch("factory.cli.contained.render_checks", return_value=""):
        assert cli.cmd_contained(args) == 1


def test_setup_is_dispatched_with_the_interactive_and_target_context() -> None:
    args = interpret(["setup"])
    with patch("factory.cli.contained.run_setup", return_value=0) as run_setup, \
         patch("factory.cli.contained.sys.stdin.isatty", return_value=False):
        assert cli.cmd_contained(args) == 0
    # `--target` was never typed, so setup is asked to decide the target itself (None).
    assert run_setup.call_args.args[0] is None
    assert run_setup.call_args.kwargs["interactive"] is False


def test_bundle_is_dispatched_and_implies_the_cluster_target(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = interpret(["bundle"])
    assert args.target == "k8s"          # `interpret` promotes bundle to the cluster target
    with patch("factory.contained.bundle.render_bundle", return_value="MANIFEST") as render, \
         patch("factory.podman.resolve_image", return_value="img:latest"):
        assert cli.cmd_contained(args) == 0
    assert render.call_args.kwargs["image"] == "img:latest"
    assert "MANIFEST" in capsys.readouterr().out


def test_bundle_uses_an_explicit_image_when_given() -> None:
    # Runtime flags go before the subcommand on this parser.
    args = interpret(["--image", "custom:tag", "bundle"])
    with patch("factory.contained.bundle.render_bundle", return_value="M") as render, \
         patch("factory.podman.resolve_image") as resolve:
        assert cli.cmd_contained(args) == 0
    resolve.assert_not_called()
    assert render.call_args.kwargs["image"] == "custom:tag"


def test_a_lifecycle_subcommand_is_handed_to_dispatch_lifecycle() -> None:
    args = interpret(["ls"])
    with patch("factory.cli.contained.dispatch_lifecycle", return_value=0) as dispatch:
        assert cli.cmd_contained(args) == 0
    dispatch.assert_called_once()


def test_a_payload_run_against_k8s_routes_to_run_k8s() -> None:
    args = interpret(["--target", "k8s", "--", "ceo", "/tmp"])
    with patch("factory.cli.contained_k8s.run_k8s", return_value=0) as run_k8s:
        assert cli.cmd_contained(args) == 0
    run_k8s.assert_called_once_with(args)


def test_a_payload_run_against_local_routes_to_run_local() -> None:
    # `run_local` is imported into `contained.py` at module load, so patch it there.
    args = interpret(["--", "study", "/tmp"])
    with patch("factory.cli.contained.run_local", return_value=0) as run_local:
        assert cli.cmd_contained(args) == 0
    run_local.assert_called_once_with(args)


def test_help_subcommand_prints_the_help_and_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = interpret(["help"])
    assert cli.cmd_contained(args) == 0
    assert "Targets:" in capsys.readouterr().out


# ============================================================================================
# contained_args.py — the remaining command-line reading branches.
# ============================================================================================


def test_within_one_edit_covers_every_shape() -> None:
    we = contained_args._within_one_edit
    assert we("ls", "ls") is True                 # identical
    assert we("abcd", "xy") is False              # length gap > 1
    assert we("ls", "lx") is True                 # one substitution
    assert we("ls", "xy") is False                # two substitutions
    assert we("ls", "lst") is True                # one insertion/deletion
    assert we("ab", "cde") is False               # same length gap of 1, but nothing matches


def test_a_close_typo_of_a_subcommand_is_named() -> None:
    """`lst` is one edit from `ls`, so it is caught before the passthrough path."""
    parser = argparse.ArgumentParser()
    with pytest.raises(SystemExit):
        contained_args._reject_subcommand_typo(parser, ["lst"])


def test_a_leading_flag_is_left_for_the_real_parser() -> None:
    """A token beginning with `-` is not a subcommand typo; the check returns without complaint."""
    parser = argparse.ArgumentParser()
    contained_args._reject_subcommand_typo(parser, ["--something"])  # no raise


def test_an_existing_directory_is_not_treated_as_a_typo(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    contained_args._reject_subcommand_typo(parser, [str(tmp_path)])  # no raise


def test_a_word_far_from_any_subcommand_is_left_alone() -> None:
    """A free-text first token that resembles no subcommand falls through to the passthrough."""
    parser = argparse.ArgumentParser()
    contained_args._reject_subcommand_typo(parser, ["totallyunrelated"])  # no raise


def test_an_empty_remainder_is_a_no_op() -> None:
    parser = argparse.ArgumentParser()
    contained_args._reject_subcommand_typo(parser, [])  # no raise


def test_target_given_recognizes_both_the_space_and_equals_forms() -> None:
    with patch.object(contained_args.sys, "argv", ["factory", "contained", "--target", "k8s"]):
        assert contained_args.target_given(argparse.Namespace()) is True
    with patch.object(contained_args.sys, "argv", ["factory", "contained", "--target=local"]):
        assert contained_args.target_given(argparse.Namespace()) is True
    with patch.object(contained_args.sys, "argv", ["factory", "contained", "setup"]):
        assert contained_args.target_given(argparse.Namespace()) is False


def test_bundle_interpret_promotes_the_target_to_k8s() -> None:
    """Directly exercises the `subcommand == "bundle"` promotion line inside `interpret`."""
    args = interpret(["--namespace", "ns", "bundle"])
    assert (args.subcommand, args.target, args.namespace) == ("bundle", "k8s", "ns")


def test_forwarding_an_unset_variable_raises() -> None:
    args = argparse.Namespace(extra_env=[], forward=["DEFINITELY_NOT_SET_XYZ"])
    with patch.dict(contained_args.os.environ, {}, clear=True):
        with pytest.raises(ContainedError, match="not set in this environment"):
            contained_args.validate_env_args(args)


def test_forwarding_a_set_variable_returns_its_value() -> None:
    args = argparse.Namespace(extra_env=["A=1"], forward=["PRESENT_XYZ"])
    with patch.dict(contained_args.os.environ, {"PRESENT_XYZ": "here"}, clear=False):
        extra, forwarded = contained_args.validate_env_args(args)
    assert extra == {"A": "1"}
    assert forwarded == {"PRESENT_XYZ": "here"}


def test_parse_extra_env_rejects_a_pair_without_an_equals() -> None:
    with pytest.raises(ContainedError, match="not KEY=VALUE"):
        contained_args.parse_extra_env(["NOEQUALS"])


def test_parse_extra_env_rejects_a_blank_key() -> None:
    with pytest.raises(ContainedError, match="not KEY=VALUE"):
        contained_args.parse_extra_env(["=value"])


def test_resolve_project_returns_the_first_existing_directory(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    assert contained_args.resolve_project(["ceo", str(project), "--loop"]) == project.resolve()


def test_resolve_project_raises_when_no_directory_is_named() -> None:
    with pytest.raises(ContainedError, match="no existing directory"):
        contained_args.resolve_project(["ceo", "--focus", "x"])


# ============================================================================================
# contained_local.py — helpers.
# ============================================================================================


def test_macos_share_warning_is_silent_off_darwin() -> None:
    with patch.object(contained_local.platform, "system", return_value="Linux"):
        assert contained_local._macos_share_warning([Mount(Path("/x"), "/x")]) is None


def test_macos_share_warning_is_silent_when_no_shared_paths_are_known() -> None:
    with patch.object(contained_local.platform, "system", return_value="Darwin"), \
         patch.object(contained_local, "_machine_shared_paths", return_value=[]):
        assert contained_local._macos_share_warning([Mount(Path("/x"), "/x")]) is None


def test_macos_share_warning_is_silent_when_every_mount_is_inside_a_shared_path() -> None:
    with patch.object(contained_local.platform, "system", return_value="Darwin"), \
         patch.object(contained_local, "_machine_shared_paths", return_value=[Path("/shared")]):
        assert contained_local._macos_share_warning([Mount(Path("/shared/proj"), "/x")]) is None


def test_macos_share_warning_names_a_mount_outside_the_shared_paths() -> None:
    with patch.object(contained_local.platform, "system", return_value="Darwin"), \
         patch.object(contained_local, "_machine_shared_paths", return_value=[Path("/shared")]):
        warning = contained_local._macos_share_warning([Mount(Path("/elsewhere/proj"), "/x")])
    assert warning is not None and "/elsewhere/proj" in warning and "/shared" in warning


def test_machine_shared_paths_returns_empty_when_podman_is_absent() -> None:
    with patch.object(contained_local.subprocess, "run", side_effect=FileNotFoundError):
        assert contained_local._machine_shared_paths() == []


def test_machine_shared_paths_returns_empty_on_a_nonzero_exit() -> None:
    with patch.object(contained_local.subprocess, "run", return_value=_completed(returncode=1)):
        assert contained_local._machine_shared_paths() == []


def test_machine_shared_paths_keeps_only_absolute_paths() -> None:
    out = "/abs/one\nrelative\n   \n/abs/two\n"
    with patch.object(contained_local.subprocess, "run", return_value=_completed(stdout=out)):
        assert contained_local._machine_shared_paths() == [Path("/abs/one"), Path("/abs/two")]


def test_handle_create_failure_ignores_a_non_create_step(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    result = _completed(returncode=1, stderr="already in use")
    out, hint = contained_local._handle_create_failure(Step("run", ["x"]), result, plan)
    assert out is result and hint is None


def test_handle_create_failure_ignores_an_unrelated_create_error(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    result = _completed(returncode=1, stderr="disk full")
    out, hint = contained_local._handle_create_failure(Step("create", ["x"]), result, plan)
    assert out is result and hint is None


def test_handle_create_failure_reaps_and_retries_successfully(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    first = _completed(returncode=1, stderr="name already in use")
    retry = _completed(returncode=0)
    with patch.object(contained_local, "reap_stale", return_value=(True, "was exited")), \
         patch.object(contained_local, "_run_step", return_value=retry):
        out, hint = contained_local._handle_create_failure(Step("create", ["x"]), first, plan)
    assert out is retry and hint is None


def test_handle_create_failure_reaps_but_the_retry_still_fails(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    first = _completed(returncode=1, stderr="already exists")
    retry = _completed(returncode=1, stderr="still broken")
    with patch.object(contained_local, "reap_stale", return_value=(True, "was exited")), \
         patch.object(contained_local, "_run_step", return_value=retry):
        out, hint = contained_local._handle_create_failure(Step("create", ["x"]), first, plan)
    assert out is retry and hint is not None and "already exists" in hint


def test_handle_create_failure_declines_to_reap_a_live_container(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    first = _completed(returncode=1, stderr="name already in use")
    with patch.object(contained_local, "reap_stale", return_value=(False, "still active")), \
         patch.object(contained_local, "_run_step") as run_step:
        out, hint = contained_local._handle_create_failure(Step("create", ["x"]), first, plan)
    run_step.assert_not_called()
    assert out is first and hint is not None and "still active" in hint


def test_run_step_uses_a_longer_timeout_for_create() -> None:
    with patch.object(contained_local.subprocess, "run", return_value=_completed()) as run:
        contained_local._run_step(Step("create", ["podman", "run"]))
    assert run.call_args.kwargs["timeout"] == 300
    with patch.object(contained_local.subprocess, "run", return_value=_completed()) as run:
        contained_local._run_step(Step("run", ["podman", "exec"]))
    assert run.call_args.kwargs["timeout"] == 120


def test_roll_back_is_a_no_op_without_a_workspace() -> None:
    contained_local._roll_back(None)  # no raise


def test_roll_back_is_a_no_op_when_the_copy_is_already_gone(tmp_path: Path) -> None:
    ws = Workspace(source=tmp_path, path=tmp_path / "gone", kind="copy")
    contained_local._roll_back(ws)  # no raise


def test_roll_back_releases_the_copy_and_removes_an_empty_run_dir(tmp_path: Path) -> None:
    import shutil

    run_dir = tmp_path / "run"
    copy = run_dir / "rta"
    copy.mkdir(parents=True)
    ws = Workspace(source=tmp_path / "src", path=copy, kind="worktree", branch="b")

    def _release(workspace: Workspace, *, delete_branch: bool) -> None:
        shutil.rmtree(workspace.path)

    with patch("factory.contained.workspace.release", side_effect=_release):
        contained_local._roll_back(ws)
    assert not run_dir.exists(), "the emptied run directory should be removed too"


def test_roll_back_leaves_a_run_dir_that_still_holds_other_work(tmp_path: Path) -> None:
    """When the run directory is not empty after release, it is left in place, not removed."""
    import shutil

    run_dir = tmp_path / "run"
    copy = run_dir / "rta"
    copy.mkdir(parents=True)
    (run_dir / "sibling").mkdir()          # keeps the run dir non-empty after the copy is released
    ws = Workspace(source=tmp_path / "src", path=copy, kind="worktree", branch="b")

    def _release(workspace: Workspace, *, delete_branch: bool) -> None:
        shutil.rmtree(workspace.path)

    with patch("factory.contained.workspace.release", side_effect=_release):
        contained_local._roll_back(ws)
    assert run_dir.exists() and (run_dir / "sibling").exists()


def test_roll_back_reports_rather_than_masks_a_cleanup_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copy = tmp_path / "run" / "rta"
    copy.mkdir(parents=True)
    ws = Workspace(source=tmp_path / "src", path=copy, kind="worktree", branch="b")
    with patch("factory.contained.workspace.release", side_effect=WorkspaceError("locked")), \
         patch("factory.contained.workspace.cleanup_hint", return_value="do this by hand"):
        contained_local._roll_back(ws)
    err = capsys.readouterr().err
    assert "could not clean up" in err and "do this by hand" in err


def test_settle_workspace_keeps_the_copy_on_success(tmp_path: Path) -> None:
    ws = Workspace(source=tmp_path, path=tmp_path, kind="copy")
    with patch.object(contained_local, "_roll_back") as roll_back:
        contained_local._settle_workspace(ws, code=0, created=True)
    roll_back.assert_not_called()


def test_settle_workspace_rolls_back_when_nothing_was_created(tmp_path: Path) -> None:
    ws = Workspace(source=tmp_path, path=tmp_path, kind="copy")
    with patch.object(contained_local, "_roll_back") as roll_back:
        contained_local._settle_workspace(ws, code=1, created=False)
    roll_back.assert_called_once_with(ws)


def test_settle_workspace_keeps_a_created_runtime_for_inspection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = Workspace(source=tmp_path, path=tmp_path, kind="copy")
    with patch("factory.contained.workspace.cleanup_hint", return_value="inspect me"):
        contained_local._settle_workspace(ws, code=1, created=True)
    assert "inspect me" in capsys.readouterr().err


def test_announce_prints_the_lifecycle_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contained_local._announce(_plan(tmp_path))
    out = capsys.readouterr().out
    assert "Starting rta-abc123" in out
    assert "attach:" in out and "sync" in out and "rm" in out


def test_execute_runs_every_step_and_reports_a_created_container(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    steps = [Step("create", ["c"]), Step("run", ["r"])]
    with patch.object(contained_local, "_run_step", return_value=_completed()):
        code, created = contained_local._execute(plan, steps, [])
    assert code == 0 and created is True


def test_execute_returns_130_on_interrupt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _plan(tmp_path)
    with patch.object(contained_local, "_run_step", side_effect=KeyboardInterrupt):
        code, created = contained_local._execute(plan, [Step("create", ["c"])], [])
    assert code == 130 and created is False
    assert "may still be running" in capsys.readouterr().err


def test_execute_reports_a_failure_before_the_container_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _plan(tmp_path)
    failed = _completed(returncode=1, stderr="boom")
    with patch.object(contained_local, "_run_step", return_value=failed), \
         patch.object(contained_local, "_handle_create_failure", return_value=(failed, "a hint")):
        code, created = contained_local._execute(plan, [Step("create", ["c"])], [])
    assert code == 1 and created is False
    err = capsys.readouterr().err
    assert "step 'create' failed" in err and "a hint" in err


def test_execute_keeps_a_created_container_after_a_later_step_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _plan(tmp_path)
    steps = [Step("create", ["c"]), Step("assert:git", ["g"])]
    probes = [Probe(name="git", argv=["g"], hint="mount hint")]
    with patch.object(
        contained_local, "_run_step",
        side_effect=[_completed(), _completed(returncode=1, stderr="assertion failed")],
    ):
        code, created = contained_local._execute(plan, steps, probes)
    assert code == 1 and created is True
    err = capsys.readouterr().err
    assert "still there for inspection" in err and "mount hint" in err


def test_probes_for_projects_from_the_source_in_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    ws = Workspace(source=project, path=tmp_path / "copy", kind="copy")
    with patch.object(contained_local, "content_probe", return_value=("a.txt", "deadbeef")):
        probes = contained_local._probes_for(ws, project, dry_run=True)
    assert probes  # a real probe list is still produced
    assert "projection from the source tree" in capsys.readouterr().err


def test_probes_for_is_silent_when_the_source_has_no_content_probe(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    ws = Workspace(source=project, path=tmp_path / "copy", kind="copy")
    with patch.object(contained_local, "content_probe", return_value=None):
        probes = contained_local._probes_for(ws, project, dry_run=True)
    assert probes


def test_probes_for_measures_the_copy_when_not_a_dry_run(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    copy = tmp_path / "copy"
    copy.mkdir()
    ws = Workspace(source=project, path=copy, kind="copy")
    with patch.object(contained_local, "content_probe", return_value=None) as content:
        contained_local._probes_for(ws, project, dry_run=False)
    content.assert_called_once_with(copy)


# --------------------------------------------------------------------------------------------
# contained_local.py — `_build_plan` composition branches.
# --------------------------------------------------------------------------------------------


def _plan_args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    project = tmp_path / "src"
    project.mkdir(exist_ok=True)
    fields: dict[str, object] = dict(
        image="img:pinned", extra_env=[], forward=[], mount=[],
        factory_args=["study", str(project)], name=None,
    )
    fields.update(overrides)
    return argparse.Namespace(**fields)


def test_build_plan_warns_when_no_credentials_and_home_is_absent(tmp_path: Path) -> None:
    """A non-worktree copy, no `~/.factory`, no credentials: the missing-inference warning fires."""
    home = tmp_path / "empty-home"
    home.mkdir()
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "copy").mkdir(exist_ok=True)
    ws = Workspace(source=tmp_path / "src", path=tmp_path / "copy", kind="copy")
    args = _plan_args(tmp_path)
    with patch.dict(contained_local.os.environ, {"HOME": str(home)}, clear=False), \
         patch.object(
             contained_local, "resolve_credentials",
             return_value=CredentialShape(backend="none", ok=False, detail=""),
         ):
        plan = contained_local._build_plan(args, ws, dry_run=True)
    assert any("no inference credentials" in w for w in plan.warnings)
    # No `~/.factory` mount was added, and a plain copy adds no git-common mount.
    assert not any(m.target.endswith(".factory") for m in plan.mounts)
    assert plan.image == "img:pinned"


def test_build_plan_appends_a_macos_share_warning(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "copy").mkdir(exist_ok=True)
    ws = Workspace(source=tmp_path / "src", path=tmp_path / "copy", kind="copy")
    args = _plan_args(tmp_path)
    with patch.object(
             contained_local, "resolve_credentials",
             return_value=CredentialShape(backend="none", ok=False, detail=""),
         ), \
         patch.object(contained_local, "_macos_share_warning", return_value="share warning"):
        plan = contained_local._build_plan(args, ws, dry_run=True)
    assert "share warning" in plan.warnings


def test_build_plan_rejects_an_extra_mount_that_does_not_exist(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "copy").mkdir(exist_ok=True)
    ws = Workspace(source=tmp_path / "src", path=tmp_path / "copy", kind="copy")
    args = _plan_args(tmp_path, mount=[str(tmp_path / "nope")])
    with patch.object(
        contained_local, "resolve_credentials",
        return_value=CredentialShape(backend="none", ok=False, detail=""),
    ):
        with pytest.raises(ContainedError, match="no such path"):
            contained_local._build_plan(args, ws, dry_run=True)


def test_build_plan_mounts_an_existing_extra_path(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "copy").mkdir(exist_ok=True)
    ws = Workspace(source=tmp_path / "src", path=tmp_path / "copy", kind="copy")
    extra = tmp_path / "extra"
    extra.mkdir()
    args = _plan_args(tmp_path, mount=[str(extra)])
    with patch.object(
        contained_local, "resolve_credentials",
        return_value=CredentialShape(backend="none", ok=False, detail=""),
    ):
        plan = contained_local._build_plan(args, ws, dry_run=True)
    assert any(m.source == extra.resolve() for m in plan.mounts)


def test_build_plan_skips_the_git_common_mount_when_there_is_none(tmp_path: Path) -> None:
    """A worktree whose common dir cannot be resolved simply adds no git mount."""
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "copy").mkdir(exist_ok=True)
    ws = Workspace(source=tmp_path / "src", path=tmp_path / "copy", kind="worktree", branch="b")
    args = _plan_args(tmp_path)
    with patch.object(
             contained_local, "resolve_credentials",
             return_value=CredentialShape(backend="none", ok=False, detail=""),
         ), \
         patch.object(contained_local, "git_common_dir", return_value=None):
        plan = contained_local._build_plan(args, ws, dry_run=True)
    assert not any(m.target.endswith(".git") for m in plan.mounts)


def test_build_plan_mounts_the_worktree_common_dir_when_present(tmp_path: Path) -> None:
    common = tmp_path / "src" / ".git"
    common.mkdir(parents=True)
    (tmp_path / "copy").mkdir(exist_ok=True)
    ws = Workspace(source=tmp_path / "src", path=tmp_path / "copy", kind="worktree", branch="b")
    args = _plan_args(tmp_path)
    with patch.object(
             contained_local, "resolve_credentials",
             return_value=CredentialShape(backend="none", ok=False, detail=""),
         ), \
         patch.object(contained_local, "git_common_dir", return_value=common):
        plan = contained_local._build_plan(args, ws, dry_run=True)
    assert any(m.source == common for m in plan.mounts)


# --------------------------------------------------------------------------------------------
# contained_local.py — `run_local`, the non-dry-run execution path.
# --------------------------------------------------------------------------------------------


def _run_local_args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    project = tmp_path / "src"
    project.mkdir(exist_ok=True)
    fields: dict[str, object] = dict(
        image=None, extra_env=[], forward=[], mount=[], division=False,
        factory_args=["study", str(project)], name="rta-run",
    )
    fields.update(overrides)
    return argparse.Namespace(**fields)


def test_run_local_executes_and_settles_on_success(tmp_path: Path) -> None:
    args = _run_local_args(tmp_path)
    ws = Workspace(source=tmp_path / "src", path=tmp_path / "copy", kind="copy")
    plan = _plan(tmp_path)
    with patch.object(contained_local, "dry_run_enabled", return_value=False), \
         patch.object(contained_local, "materialize", return_value=ws), \
         patch.object(contained_local, "_build_plan", return_value=plan), \
         patch.object(contained_local, "_probes_for", return_value=[]), \
         patch.object(contained_local, "plan_steps", return_value=[Step("run", ["r"])]), \
         patch.object(contained_local, "growth_context_warning", return_value=None), \
         patch.object(contained_local.shutil, "which", return_value="/usr/bin/podman"), \
         patch("factory.contained.usage.record_target") as record, \
         patch.object(contained_local, "_execute", return_value=(0, True)), \
         patch.object(contained_local, "_settle_workspace") as settle:
        assert contained_local.run_local(args) == 0
    record.assert_called_once_with("local")
    settle.assert_called_once()


def test_run_local_errors_when_podman_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _run_local_args(tmp_path)
    ws = Workspace(source=tmp_path / "src", path=tmp_path / "gone", kind="copy")
    plan = _plan(tmp_path)
    with patch.object(contained_local, "dry_run_enabled", return_value=False), \
         patch.object(contained_local, "materialize", return_value=ws), \
         patch.object(contained_local, "_build_plan", return_value=plan), \
         patch.object(contained_local, "_probes_for", return_value=[]), \
         patch.object(contained_local, "plan_steps", return_value=[Step("run", ["r"])]), \
         patch.object(contained_local, "growth_context_warning", return_value=None), \
         patch.object(contained_local.shutil, "which", return_value=None):
        assert contained_local.run_local(args) == 1
    assert "`podman` is not installed" in capsys.readouterr().err


def test_run_local_reports_a_provisioning_error_and_returns_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _run_local_args(tmp_path)
    with patch.object(contained_local, "dry_run_enabled", return_value=False), \
         patch.object(contained_local, "materialize", side_effect=WorkspaceError("no copy")):
        assert contained_local.run_local(args) == 2
    assert "Error: no copy" in capsys.readouterr().err


def test_run_local_prints_warnings_but_keeps_the_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _run_local_args(tmp_path)
    ws = Workspace(source=tmp_path / "src", path=tmp_path / "copy", kind="copy")
    plan = ContainerPlan(
        name="rta-warn", image="i", workdir=str(tmp_path), env={}, labels={},
        mounts=(), run_command="x", warnings=("credentials missing",),
    )
    with patch.object(contained_local, "dry_run_enabled", return_value=False), \
         patch.object(contained_local, "materialize", return_value=ws), \
         patch.object(contained_local, "_build_plan", return_value=plan), \
         patch.object(contained_local, "_probes_for", return_value=[]), \
         patch.object(contained_local, "plan_steps", return_value=[Step("run", ["r"])]), \
         patch.object(contained_local, "growth_context_warning", return_value="a growth warning"), \
         patch.object(contained_local.shutil, "which", return_value="/usr/bin/podman"), \
         patch("factory.contained.usage.record_target"), \
         patch.object(contained_local, "_execute", return_value=(0, True)), \
         patch.object(contained_local, "_settle_workspace"):
        assert contained_local.run_local(args) == 0
    err = capsys.readouterr().err
    assert "a growth warning" in err and "credentials missing" in err


def test_run_local_starts_and_keeps_the_division_on_success(tmp_path: Path) -> None:
    args = _run_local_args(tmp_path, division=True)
    ws = Workspace(source=tmp_path / "src", path=tmp_path / "copy", kind="copy")
    plan = _plan(tmp_path)
    division = _FakeDivision(plan)
    with patch.object(contained_local, "dry_run_enabled", return_value=False), \
         patch.object(contained_local, "materialize", return_value=ws), \
         patch.object(contained_local, "_build_plan", return_value=plan), \
         patch.object(contained_local, "_probes_for", return_value=[]), \
         patch.object(contained_local, "plan_steps", return_value=[Step("run", ["r"])]), \
         patch.object(contained_local, "growth_context_warning", return_value=None), \
         patch.object(contained_local.shutil, "which", return_value="/usr/bin/podman"), \
         patch("factory.contained.division.start_local_division", return_value=division), \
         patch("factory.contained.usage.record_target"), \
         patch.object(contained_local, "_execute", return_value=(0, True)), \
         patch.object(contained_local, "_settle_workspace"):
        assert contained_local.run_local(args) == 0
    assert division.kept is True and division.stopped is False


def test_run_local_stops_the_division_when_the_run_fails(tmp_path: Path) -> None:
    args = _run_local_args(tmp_path, division=True)
    ws = Workspace(source=tmp_path / "src", path=tmp_path / "copy", kind="copy")
    plan = _plan(tmp_path)
    division = _FakeDivision(plan)
    with patch.object(contained_local, "dry_run_enabled", return_value=False), \
         patch.object(contained_local, "materialize", return_value=ws), \
         patch.object(contained_local, "_build_plan", return_value=plan), \
         patch.object(contained_local, "_probes_for", return_value=[]), \
         patch.object(contained_local, "plan_steps", return_value=[Step("run", ["r"])]), \
         patch.object(contained_local, "growth_context_warning", return_value=None), \
         patch.object(contained_local.shutil, "which", return_value="/usr/bin/podman"), \
         patch("factory.contained.division.start_local_division", return_value=division), \
         patch("factory.contained.usage.record_target"), \
         patch.object(contained_local, "_execute", return_value=(1, True)), \
         patch.object(contained_local, "_settle_workspace"):
        assert contained_local.run_local(args) == 1
    assert division.kept is False and division.stopped is True
