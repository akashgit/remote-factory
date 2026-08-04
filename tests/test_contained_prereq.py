"""Prerequisite checks and setup: three checks, every failure carrying its fix, nothing raising."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from factory.contained import prereq, setup
from factory.contained.prereq import Check, local_checks, render_checks


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_a_clean_machine_gets_a_list_not_a_traceback() -> None:
    """`shutil.which` returns None for everything and every subprocess raises FileNotFoundError."""
    with patch("factory.contained.prereq.shutil.which", return_value=None), \
         patch("factory.contained.prereq.subprocess.run", side_effect=FileNotFoundError):
        checks = local_checks()
    assert [c.name for c in checks] == ["container_engine", "runtime_image", "inference"]
    assert not checks[0].ok
    assert checks[0].fix


def test_the_engine_check_exercises_the_connection_not_just_the_binary() -> None:
    """On macOS the machine stops quietly, so finding `podman` proves nothing."""
    with patch("factory.contained.prereq.shutil.which", return_value="/usr/bin/podman"), \
         patch("factory.contained.prereq.subprocess.run", return_value=_completed(returncode=125)):
        check = prereq._engine_check()
    assert not check.ok
    assert check.fix == "podman machine start"


def test_a_reachable_engine_reports_its_mode() -> None:
    def fake_run(argv, **kwargs):
        if argv[:2] == ["podman", "info"] and "json" in " ".join(argv):
            return _completed('{"host": {"security": {"rootless": false}}}')
        if argv[:2] == ["podman", "version"]:
            return _completed("5.7.1")
        return _completed("false")

    with patch("factory.contained.prereq.shutil.which", return_value="/usr/bin/podman"), \
         patch("factory.contained.prereq.subprocess.run", side_effect=fake_run):
        check = prereq._engine_check()
    assert check.ok
    assert "rootful" in check.detail


def test_a_missing_image_points_at_setup() -> None:
    with patch("factory.contained.prereq.subprocess.run", return_value=_completed(returncode=1)):
        check = prereq._image_check()
    assert not check.ok
    assert "factory contained setup" in (check.fix or "")


def test_inference_is_reported_by_shape_never_by_material(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-supersecret")
    monkeypatch.delenv("CLAUDE_CODE_USE_VERTEX", raising=False)
    check = prereq._inference_check()
    assert check.ok
    assert "sk-ant-supersecret" not in check.detail
    assert "ANTHROPIC_API_KEY" in check.detail


def test_every_failing_check_carries_a_fix() -> None:
    with patch("factory.contained.prereq.shutil.which", return_value=None), \
         patch("factory.contained.prereq.subprocess.run", side_effect=FileNotFoundError):
        checks = local_checks()
    for check in checks:
        if not check.ok:
            assert check.fix, f"{check.name} failed without naming a fix"


def test_render_reports_each_check_and_ends_in_one_of_two_states() -> None:
    green = render_checks([Check("a", True, "fine"), Check("b", True, "fine")])
    assert "All checks passed" in green
    red = render_checks([Check("a", False, "broken", fix="do the thing")])
    assert "1 check(s) failed" in red
    assert "fix: do the thing" in red


def test_setup_pulls_a_missing_image_and_skips_a_present_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("factory.contained.setup.local_checks",
               return_value=[Check("container_engine", True, "ok")]), \
         patch("factory.contained.setup._image_present", return_value=True), \
         patch("factory.contained.setup.subprocess.run") as run:
        setup._setup_local()
    run.assert_not_called()
    assert "already present" in capsys.readouterr().out

    with patch("factory.contained.setup.local_checks",
               return_value=[Check("container_engine", True, "ok")]), \
         patch("factory.contained.setup._image_present", return_value=False), \
         patch("factory.contained.setup.subprocess.run",
               return_value=_completed()) as run:
        setup._setup_local()
    assert run.call_args[0][0][:2] == ["podman", "pull"]


def test_setup_announces_before_starting_a_stopped_machine(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("factory.contained.setup.local_checks",
               return_value=[Check("container_engine", False, "not reachable")]), \
         patch("factory.contained.setup._image_present", return_value=True), \
         patch("factory.contained.setup.subprocess.run",
               return_value=_completed("podman-machine-default\n")):
        setup._setup_local()
    assert "Starting the podman machine" in capsys.readouterr().out


def test_setup_is_idempotent_over_a_ready_machine(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("factory.contained.setup.local_checks",
               return_value=[Check("container_engine", True, "ok")]), \
         patch("factory.contained.setup._image_present", return_value=True), \
         patch("factory.contained.setup.subprocess.run") as run:
        setup._setup_local()
        setup._setup_local()
    run.assert_not_called()


def test_setup_always_reports_the_full_check_list(capsys: pytest.CaptureFixture[str]) -> None:
    """Ends in exactly one of two states, never in a single ad hoc line standing in for it."""
    checks = [Check("container_engine", False, "no podman", fix="brew install podman")]
    with patch("factory.contained.setup._setup_local"), \
         patch("factory.contained.setup.local_checks", return_value=checks):
        code = setup.run_setup("local", interactive=False)
    out = capsys.readouterr().out
    assert code == 1
    assert "container_engine" in out
    assert "brew install podman" in out
