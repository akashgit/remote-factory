"""The local container-manufacturing plane: opt-in, reachable, briefed, and shut down."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory.contained import division
from factory.contained.division import (
    DIVISION_BRIEF_PATH,
    DIVISION_PORT,
    HOST_CANDIDATES,
    Division,
    mcp_config,
    probe_argv,
    probe_host_alias,
    server_argv,
    start_local_division,
)
from factory.contained.errors import ContainedError
from factory.podman import ContainerPlan, Mount, build_run_command


@pytest.fixture()
def contained_root(tmp_path: Path):
    """Keep the division's PID file and log out of the developer's real ~/.factory-contained."""
    import os

    root = tmp_path / "contained-home"
    with patch.dict(os.environ, {"FACTORY_CONTAINED_HOME": str(root)}, clear=False):
        yield root


def _plan(tmp_path: Path) -> ContainerPlan:
    workspace = tmp_path / "rta"
    workspace.mkdir(exist_ok=True)
    inner = f"factory study {workspace}"
    return ContainerPlan(
        name="rta-abc123",
        image="example/runtime:latest",
        workdir=str(workspace),
        env={},
        labels={},
        mounts=(Mount(workspace, str(workspace)),),
        run_command=build_run_command(str(workspace), inner),
        factory_command=inner,
    )


def _completed(returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, "", "")


# --------------------------------------------------------------------------------------------
# The server (§5.1)
# --------------------------------------------------------------------------------------------


def test_the_server_is_started_on_the_division_port() -> None:
    command = " ".join(server_argv())
    assert "podman-mcp-server" in command
    assert f"--port {DIVISION_PORT}" in command


def test_stdin_is_held_open_because_the_server_exits_on_eof() -> None:
    """A naive background spawn leaves nothing listening and writes no error at all.

    The writer has to be something *other* than the launching process, because the server outlives
    it — hence a pipeline whose head never writes and never exits.
    """
    command = " ".join(server_argv())
    assert command.startswith("sh -c tail -f /dev/null |") or "tail -f /dev/null |" in command


def test_the_server_is_detached_into_its_own_process_group(
    tmp_path: Path, contained_root: Path
) -> None:
    """It must survive this command and still be stoppable as a unit later."""
    process = MagicMock()
    process.poll.return_value = None
    with patch("factory.contained.division.shutil.which", return_value="/usr/bin/npx"), \
         patch("factory.contained.division.subprocess.Popen", return_value=process) as popen, \
         patch("factory.contained.division.wait_for_listening", return_value=True), \
         patch("factory.contained.division.probe_host_alias", return_value="host.containers.internal"):
        start_local_division(_plan(tmp_path))
    assert popen.call_args.kwargs["start_new_session"] is True


def test_missing_npx_fails_before_anything_is_spawned(tmp_path: Path) -> None:
    with patch("factory.contained.division.shutil.which", return_value=None), \
         patch("factory.contained.division.subprocess.Popen") as popen:
        with pytest.raises(ContainedError, match="npx"):
            start_local_division(_plan(tmp_path))
    popen.assert_not_called()


# --------------------------------------------------------------------------------------------
# Reachability is probed, never assumed (§5.1, open item F6)
# --------------------------------------------------------------------------------------------


def test_the_probe_runs_from_inside_a_container_not_from_the_host() -> None:
    """The host can reach a port the container cannot: on macOS they are different machines."""
    argv = probe_argv("img", "host.containers.internal")
    assert argv[:3] == ["podman", "run", "--rm"]
    assert f"http://host.containers.internal:{DIVISION_PORT}/mcp" in argv


def test_candidates_are_tried_in_order_and_the_first_reachable_one_wins() -> None:
    def fake_run(argv, **kwargs):
        return _completed(0 if HOST_CANDIDATES[1] in " ".join(argv) else 7)

    with patch("factory.contained.division.subprocess.run", side_effect=fake_run):
        assert probe_host_alias("img") == HOST_CANDIDATES[1]


def test_no_reachable_candidate_stops_the_run_and_stops_the_server(
    tmp_path: Path, contained_root: Path
) -> None:
    """An agent given an endpoint it cannot reach fails on its first build with a podman-looking
    error, several steps from the cause."""
    process = MagicMock()
    process.poll.return_value = None
    process.pid = 4242
    with patch("factory.contained.division.shutil.which", return_value="/usr/bin/npx"), \
         patch("factory.contained.division.subprocess.Popen", return_value=process), \
         patch("factory.contained.division.wait_for_listening", return_value=True), \
         patch("factory.contained.division.probe_host_alias", return_value=None), \
         patch("factory.contained.division._kill_group") as kill:
        with pytest.raises(ContainedError, match="not reachable"):
            start_local_division(_plan(tmp_path))
    kill.assert_called_once_with(4242)


# --------------------------------------------------------------------------------------------
# Registration and brief (§5.4)
# --------------------------------------------------------------------------------------------


def test_registration_is_streamable_http_not_stdio() -> None:
    config = mcp_config("http://host.containers.internal:8430/mcp")
    server = config["mcpServers"]["podman"]
    assert server["type"] == "http"
    assert server["url"].endswith("/mcp")


def test_the_plan_gains_the_registration_and_the_brief(tmp_path: Path, contained_root: Path) -> None:
    process = MagicMock()
    process.poll.return_value = None
    with patch("factory.contained.division.shutil.which", return_value="/usr/bin/npx"), \
         patch("factory.contained.division.subprocess.Popen", return_value=process), \
         patch("factory.contained.division.wait_for_listening", return_value=True), \
         patch("factory.contained.division.probe_host_alias", return_value="192.168.127.254"):
        result = start_local_division(_plan(tmp_path))
    assert ".mcp.json" in result.plan.run_command
    assert DIVISION_BRIEF_PATH in result.plan.run_command
    assert "192.168.127.254" in result.plan.run_command
    # The factory invocation itself is unchanged — the division adds to the run, it does not
    # rewrite what the run does.
    assert result.plan.factory_command in result.plan.run_command


def test_the_brief_says_this_is_a_capability_not_a_thing_to_build() -> None:
    """A Refiner given only the tool registration scoped 165 lines of CLI code to wrap them."""
    brief = division.DIVISION_BRIEF
    assert "not something to build" in brief
    assert "Do not write a CLI wrapper" in brief
    assert "build" in brief and "run" in brief and "logs" in brief.lower()
    assert "outside this container" in brief


# --------------------------------------------------------------------------------------------
# Warning at start, guaranteed shutdown at exit (§5.1, §11)
# --------------------------------------------------------------------------------------------


def test_launch_warns_that_the_endpoint_is_unauthenticated(
    tmp_path: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    process = MagicMock()
    process.poll.return_value = None
    with patch("factory.contained.division.shutil.which", return_value="/usr/bin/npx"), \
         patch("factory.contained.division.subprocess.Popen", return_value=process), \
         patch("factory.contained.division.wait_for_listening", return_value=True), \
         patch("factory.contained.division.probe_host_alias", return_value="host.containers.internal"):
        start_local_division(_plan(tmp_path))
    err = capsys.readouterr().err
    assert "NO AUTHENTICATION" in err
    assert str(DIVISION_PORT) in err


def test_stop_signals_the_whole_group_not_just_the_shell(tmp_path: Path) -> None:
    """The server is half a pipeline; signalling only the shell leaves the other half behind."""
    process = MagicMock()
    process.poll.return_value = None
    process.pid = 4242
    with patch("factory.contained.division._kill_group") as kill:
        Division(plan=_plan(tmp_path), endpoint="e", process=process).stop()
    kill.assert_called_once_with(4242)


def test_a_kept_division_records_its_pid_and_rm_stops_it(
    tmp_path: Path, contained_root: Path
) -> None:
    process = MagicMock()
    process.poll.return_value = None
    process.pid = 4242
    plan = _plan(tmp_path)
    Division(plan=plan, endpoint="e", process=process,
             pid_file=division.pid_file_for(plan.name)).keep()
    assert division.pid_file_for(plan.name).read_text() == "4242"

    with patch("factory.contained.division._kill_group") as kill:
        assert division.stop_recorded(plan.name) is True
    kill.assert_called_once_with(4242)
    # The record is cleared, so a second rm reports nothing rather than signalling a reused PID.
    assert not division.pid_file_for(plan.name).exists()
    assert division.stop_recorded(plan.name) is False


def test_stop_is_safe_when_the_server_already_died(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    process = MagicMock()
    process.poll.return_value = 1
    Division(plan=_plan(tmp_path), endpoint="e", process=process).stop()
    process.terminate.assert_not_called()
    assert "already exited" in capsys.readouterr().err


def test_dry_run_starts_nothing_and_still_composes_the_registration(tmp_path: Path) -> None:
    with patch("factory.contained.division.subprocess.Popen") as popen, \
         patch("factory.contained.division.subprocess.run") as run:
        result = start_local_division(_plan(tmp_path), dry_run=True)
    popen.assert_not_called()
    run.assert_not_called()
    assert ".mcp.json" in result.plan.run_command
    result.stop()          # a no-op, and must not raise


# --------------------------------------------------------------------------------------------
# The division is genuinely opt-in (§5.5 step 4)
# --------------------------------------------------------------------------------------------


def test_without_the_flag_nothing_is_started_and_no_tools_are_registered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import argparse
    import os

    from factory.cli import contained as cli

    project = tmp_path / "plain"
    project.mkdir()
    (project / "a.txt").write_text("a\n")
    parser = argparse.ArgumentParser(prog="factory")
    sub = parser.add_subparsers(dest="command")
    cli.build_contained_parser(sub)
    args = parser.parse_args(["contained", "--", "study", str(project)])

    # Patching `start_local_division` rather than `subprocess.Popen`: the module attribute is the
    # shared `subprocess` module, so patching Popen there patches it for every other caller in the
    # process — including the `git rev-parse` this path legitimately runs.
    with patch.dict(
        os.environ,
        {"FACTORY_CONTAINED_DRY_RUN": "1", "FACTORY_CONTAINED_HOME": str(tmp_path / "home")},
        clear=False,
    ), patch("factory.contained.division.start_local_division") as start:
        code = cli.cmd_contained(args)
    out = capsys.readouterr().out
    assert code == 0
    start.assert_not_called()
    assert ".mcp.json" not in out
    assert "8430" not in out


def test_a_server_that_never_binds_is_reported_as_that_not_as_unreachable(
    tmp_path: Path, contained_root: Path
) -> None:
    """A slow start and a routing fault are different problems with different fixes."""
    process = MagicMock()
    process.poll.return_value = None
    process.pid = 4242
    with patch("factory.contained.division.shutil.which", return_value="/usr/bin/npx"), \
         patch("factory.contained.division.subprocess.Popen", return_value=process), \
         patch("factory.contained.division.wait_for_listening", return_value=False), \
         patch("factory.contained.division.probe_host_alias") as probe, \
         patch("factory.contained.division._kill_group"):
        with pytest.raises(ContainedError, match="did not start listening"):
            start_local_division(_plan(tmp_path))
    probe.assert_not_called()


def test_readiness_is_checked_on_the_host_not_from_a_container() -> None:
    """'Has it bound the port' and 'which address can the container use' are separate questions."""
    from factory.contained.division import wait_for_listening

    # Nothing is listening on this port, so the call returns False rather than hanging.
    assert wait_for_listening(1, timeout=0.2) is False


def test_a_second_division_refuses_rather_than_adopting_the_first_ones_endpoint(
    tmp_path: Path, contained_root: Path
) -> None:
    """Two runs sharing one endpoint means `rm` on either pulls the tools out from under the other."""
    (contained_root / "first-run").mkdir(parents=True)
    (contained_root / "first-run" / "division.pid").write_text(str(os.getpid()))
    with patch("factory.contained.division.shutil.which", return_value="/usr/bin/npx"), \
         patch("factory.contained.division.subprocess.Popen") as popen:
        with pytest.raises(ContainedError, match="already held by the run 'first-run'"):
            start_local_division(_plan(tmp_path))
    popen.assert_not_called()


def test_a_stale_pid_file_does_not_block_a_new_division(
    tmp_path: Path, contained_root: Path
) -> None:
    """A run whose server already died must not lock the port forever."""
    (contained_root / "dead-run").mkdir(parents=True)
    pid_file = contained_root / "dead-run" / "division.pid"
    pid_file.write_text("999999")            # a PID that cannot exist
    assert division.port_owner() is None
    assert not pid_file.exists()             # and the stale record is cleaned up
