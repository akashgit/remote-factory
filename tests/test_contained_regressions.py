"""Four defects the coverage pass surfaced, each pinned so it cannot return quietly.

All four shared a shape: the code reported a state that was not true. A stale container that could
never be reaped, an errored scan that read as clean, a dry run that contacted the cluster, and a
credential lookup that answered from a file it was not given. None of them raised; each just said
something reassuring and wrong, which is why they survived a green suite.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.cli import contained as cli
from factory.contained import lifecycle, secrets
from factory.contained.credentials import resolve_credentials
from factory.contained.runtimes import Runtime


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


# ---------------------------------------------------------------------------------------------
# 1. A finished run is not an active one
# ---------------------------------------------------------------------------------------------


def test_a_finished_run_is_inactive() -> None:
    """The container outlives its run by design, so "finished" is what a completed run looks like.

    Treating it as active made `reap_stale` refuse the containers it exists to reap.
    """
    assert not Runtime(name="x", target="local", project="p", state="finished").active
    assert Runtime(name="x", target="local", project="p", state="running").active


def test_reap_stale_removes_a_finished_container() -> None:
    with patch("factory.contained.lifecycle.local_runtimes",
               return_value=[Runtime(name="x", target="local", project="p", state="finished")]), \
         patch("factory.contained.lifecycle.subprocess.run", return_value=_completed()) as run:
        reaped, detail = lifecycle.reap_stale("x")
    assert reaped, detail
    assert run.call_args[0][0][:2] == ["podman", "rm"]


def test_rm_does_not_interrogate_the_user_about_a_finished_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-interactive `rm` used to refuse the one state where deleting is unambiguously safe."""
    with patch("factory.contained.lifecycle.local_runtimes",
               return_value=[Runtime(name="x", target="local", project="p", state="finished")]), \
         patch("factory.contained.lifecycle.workspace_for", return_value=None), \
         patch("factory.contained.division.stop_recorded", return_value=False), \
         patch("factory.contained.lifecycle.subprocess.run", return_value=_completed()):
        assert lifecycle.remove("x", "local", assume_yes=False, interactive=False) == 0
    assert "--yes" not in capsys.readouterr().err


def test_attach_explains_a_finished_run_rather_than_calling_it_stopped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A finished run's container IS running — only its session ended.

    The generic inactive message says "the container is not running", which is false here and hides
    that `podman exec` still works. Ordering the specific branch first is what keeps it reachable.
    """
    with patch("factory.contained.lifecycle.list_runtimes",
               return_value=([Runtime(name="x", target="local", project="p", state="finished")],
                             [], [])):
        assert lifecycle.attach("x", "local") == 1
    err = capsys.readouterr().err
    assert "podman exec -it x bash" in err
    assert "the container is not running" not in err


# ---------------------------------------------------------------------------------------------
# 2. A scan that failed is not a scan that passed
# ---------------------------------------------------------------------------------------------


def test_a_failed_gitleaks_run_is_reported_as_unscanned(tmp_path: Path) -> None:
    """gitleaks writes a report only when it finds something, so an error left no report and was
    read as "no secrets found" — and the workspace uploaded claiming it had been checked."""
    with patch("factory.contained.secrets.gitleaks_available", return_value=True), \
         patch("factory.contained.secrets.subprocess.run",
               return_value=_completed("", 1, "error: unknown flag --nonsense")):
        result = secrets.scan(tmp_path)
    assert not result.scanned
    assert "UNSCANNED" in result.detail
    assert "no secrets found" not in result.detail


def test_a_clean_gitleaks_run_is_still_clean(tmp_path: Path) -> None:
    with patch("factory.contained.secrets.gitleaks_available", return_value=True), \
         patch("factory.contained.secrets.subprocess.run", return_value=_completed("", 0)):
        result = secrets.scan(tmp_path)
    assert result.scanned
    assert result.detail == "no secrets found"


def test_the_leak_exit_code_is_the_one_gitleaks_is_told_to_use() -> None:
    """`scan` distinguishes findings from failure by this code, so it must match the flag."""
    argv = secrets.build_scan_argv(Path("/tmp/x"), Path("/tmp/r.json"))
    assert str(secrets.LEAK_EXIT_CODE) == argv[argv.index("--exit-code") + 1]


def test_an_unscanned_workspace_warns_visibly_before_uploading(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """It proceeds, and that is deliberate — but it must say so.

    `confirm_upload` warns and continues for an unscanned tree on purpose: "the absence of a
    scanner is not evidence of a secret, and refusing to run without an optional tool would make it
    mandatory by the back door." The defect was never that it proceeded; it was that a *failed*
    scan reported "no secrets found" and so produced no warning at all. The fix is that the
    warning now exists to be printed.
    """
    failed = secrets.ScanResult(scanned=False, detail="gitleaks failed, uploading UNSCANNED")
    with patch("builtins.input", side_effect=AssertionError("must not prompt")):
        assert secrets.confirm_upload(failed, assume_yes=False, interactive=True) is True
    assert "UNSCANNED" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------
# 3. Dry run provisions nothing — and contacts nothing
# ---------------------------------------------------------------------------------------------


def test_k8s_dry_run_never_reaches_the_cluster(tmp_path: Path) -> None:
    """`FACTORY_CONTAINED_DRY_RUN=1` is documented as composing commands and provisioning nothing.

    Two values in the pod plan are live cluster reads — the namespace's fsGroup range and whether
    the credentials Secret carries a Google credential file. Asking for them made dry-run a
    30-second round trip against an unreachable cluster, for a command that should be instant.
    """
    from factory.cli.contained_k8s import _build_pod_plan
    from factory.contained.workspace import plan_workspace

    project = tmp_path / "proj"
    project.mkdir()
    parser = argparse.ArgumentParser(prog="factory")
    sub = parser.add_subparsers(dest="command")
    cli.build_contained_parser(sub)
    args = parser.parse_args(
        ["contained", "--target", "k8s", "--namespace", "ns", "--", "ceo", str(project)]
    )
    cli.interpret(cli._PARSER, args)

    boom = AssertionError("dry run must not contact the cluster")
    with patch.dict("os.environ", {"FACTORY_CONTAINED_HOME": str(tmp_path / "home")}, clear=False), \
         patch("factory.cli.contained_k8s.namespace_fs_group", side_effect=boom), \
         patch("factory.cli.contained_k8s.secret_keys", side_effect=boom):
        ws = plan_workspace(project, "run-1", self_contained=True)
        plan = _build_pod_plan(args, ws, "ns", "run-1", dry_run=True)

    # Both cluster-derived fields fall back to their unknown value rather than a guess.
    assert plan.fs_group is None
    assert plan.adc is False


def test_a_real_k8s_launch_still_reads_both_from_the_cluster(tmp_path: Path) -> None:
    """The fix must not turn the real path into a dry run."""
    from factory.cli.contained_k8s import _build_pod_plan
    from factory.contained.k8s import ADC_SECRET_KEY
    from factory.contained.workspace import plan_workspace

    project = tmp_path / "proj"
    project.mkdir()
    parser = argparse.ArgumentParser(prog="factory")
    sub = parser.add_subparsers(dest="command")
    cli.build_contained_parser(sub)
    args = parser.parse_args(
        ["contained", "--target", "k8s", "--namespace", "ns", "--", "ceo", str(project)]
    )
    cli.interpret(cli._PARSER, args)

    with patch.dict("os.environ", {"FACTORY_CONTAINED_HOME": str(tmp_path / "home")}, clear=False), \
         patch("factory.cli.contained_k8s.namespace_fs_group", return_value=1001000000), \
         patch("factory.cli.contained_k8s.secret_keys", return_value={ADC_SECRET_KEY}):
        ws = plan_workspace(project, "run-1", self_contained=True)
        plan = _build_pod_plan(args, ws, "ns", "run-1")

    assert plan.fs_group == 1001000000
    assert plan.adc is True


# ---------------------------------------------------------------------------------------------
# 4. A credential lookup answers from the file it was given
# ---------------------------------------------------------------------------------------------


def test_the_model_is_read_from_the_caller_s_config(tmp_path: Path) -> None:
    """`config_path` used to apply to profiles but not to the model, so injection half-worked —
    under test that meant reaching into the developer's real ~/.factory/config.toml."""
    config = tmp_path / "config.toml"
    config.write_text('[defaults]\nmodel = "injected-model"\n\n[credentials.x]\nA = "b"\n')
    shape = resolve_credentials({"ANTHROPIC_API_KEY": "sk-ant-x"}, config_path=config)
    assert "injected-model" in shape.detail
    assert str(config) in shape.detail


def test_an_absent_config_reports_no_model_rather_than_the_real_one(tmp_path: Path) -> None:
    shape = resolve_credentials({"ANTHROPIC_API_KEY": "sk-ant-x"},
                                config_path=tmp_path / "absent.toml")
    assert "<unset" in shape.detail


def test_an_environment_model_still_wins_over_the_config(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[defaults]\nmodel = "from-config"\n')
    shape = resolve_credentials(
        {"ANTHROPIC_API_KEY": "sk-ant-x", "FACTORY_MODEL": "from-env"}, config_path=config
    )
    assert "from-env" in shape.detail and "from-config" not in shape.detail
