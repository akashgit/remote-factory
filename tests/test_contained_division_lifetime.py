"""The division server's lifetime, and the ownership check that keeps two runs off one port.

The endpoint has to outlive the command that started it — the launch returns as soon as the tmux
session exists, while the run continues for hours — so the process is detached into its own group
and its PGID is written next to the workspace. Everything here is about that record being correct:
a lost PGID leaves an unauthenticated build server listening on every interface with nothing
tracking it, and a *wrong* one means `rm` on one run pulls the tools out from under another.

No process is ever spawned and no socket is ever bound.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory.contained.division import (
    DIVISION_BRIEF_PATH,
    DIVISION_PORT,
    Division,
    brief_path,
    pid_file_for,
    port_in_use,
    port_owner,
    probe_host_alias,
    stop_recorded,
    wait_for_listening,
)


@pytest.fixture()
def contained_root(tmp_path: Path):
    root = tmp_path / "contained-home"
    with patch.dict(os.environ, {"FACTORY_CONTAINED_HOME": str(root)}, clear=False):
        yield root


def _process(pid: int = 4242, poll: int | None = None) -> MagicMock:
    process = MagicMock(spec=subprocess.Popen)
    process.pid = pid
    process.poll.return_value = poll
    process.returncode = poll
    return process


# --------------------------------------------------------------------------------------------
# Recording the server so something can stop it later
# --------------------------------------------------------------------------------------------


def test_a_dry_run_division_records_nothing(contained_root: Path) -> None:
    """Nothing was started, so a PID file would name a process that does not exist — and `rm`
    would signal whatever inherited that number."""
    division = Division(plan=MagicMock(), endpoint="http://h:8430/mcp", process=None)
    division.keep()
    assert not contained_root.exists()


def test_keeping_writes_the_pid_next_to_the_workspace(contained_root: Path) -> None:
    pid_file = pid_file_for("rta-abc123")
    Division(plan=MagicMock(), endpoint="e", process=_process(), pid_file=pid_file).keep()
    assert pid_file.read_text() == "4242"


def test_stopping_a_dry_run_division_is_a_no_op() -> None:
    Division(plan=MagicMock(), endpoint="e", process=None).stop()


def test_stopping_a_server_that_already_exited_says_so_rather_than_signalling(
    capsys: pytest.CaptureFixture[str]
) -> None:
    """Signalling a dead PID's number is how an unrelated process gets killed."""
    process = _process(poll=0)
    with patch("factory.contained.division.os.killpg") as killpg:
        Division(plan=MagicMock(), endpoint="e", process=process).stop()
    killpg.assert_not_called()
    assert "already exited" in capsys.readouterr().err


def test_stopping_signals_the_whole_process_group(
    contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The server is one half of a shell pipeline, so signalling only the shell leaves the other
    half — and whatever it is feeding — behind."""
    pid_file = pid_file_for("rta-abc123")
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("4242")
    process = _process()
    with patch("factory.contained.division.os.getpgid", return_value=99), \
         patch("factory.contained.division.os.killpg") as killpg:
        Division(plan=MagicMock(), endpoint="e", process=process, pid_file=pid_file).stop()
    killpg.assert_called_once_with(99, signal.SIGTERM)
    assert not pid_file.exists()
    assert f"nothing is listening on {DIVISION_PORT}" in capsys.readouterr().err


def test_a_server_that_ignores_sigterm_is_killed() -> None:
    process = _process()
    process.wait.side_effect = subprocess.TimeoutExpired(cmd="npx", timeout=10)
    with patch("factory.contained.division.os.getpgid", return_value=99), \
         patch("factory.contained.division.os.killpg"):
        Division(plan=MagicMock(), endpoint="e", process=process).stop()
    process.kill.assert_called_once()


def test_signalling_a_group_that_is_already_gone_is_logged_not_raised() -> None:
    """Cleanup runs on the failure path; a second exception there buries the first."""
    process = _process()
    with patch("factory.contained.division.os.getpgid", side_effect=ProcessLookupError):
        Division(plan=MagicMock(), endpoint="e", process=process).stop()


# --------------------------------------------------------------------------------------------
# stop_recorded — what `rm` uses
# --------------------------------------------------------------------------------------------


def test_a_run_with_no_recorded_division_stops_nothing(contained_root: Path) -> None:
    assert stop_recorded("rta-abc123") is False


def test_a_recorded_division_is_stopped_and_its_record_removed(contained_root: Path) -> None:
    pid_file = pid_file_for("rta-abc123")
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("4242")
    with patch("factory.contained.division.os.getpgid", return_value=99), \
         patch("factory.contained.division.os.killpg") as killpg:
        assert stop_recorded("rta-abc123") is True
    killpg.assert_called_once()
    assert not pid_file.exists()


def test_a_corrupt_pid_file_stops_nothing_rather_than_signalling_a_guess(
    contained_root: Path
) -> None:
    pid_file = pid_file_for("rta-abc123")
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("not a pid")
    assert stop_recorded("rta-abc123") is False


# --------------------------------------------------------------------------------------------
# port_owner — one port, one server
# --------------------------------------------------------------------------------------------


def test_no_contained_home_means_nobody_owns_the_port(contained_root: Path) -> None:
    assert port_owner() is None


def test_a_live_pid_file_identifies_the_owning_run(contained_root: Path) -> None:
    """Without this, a second `--division` run finds the port bound, concludes its own server came
    up, and silently drives the first run's endpoint."""
    (contained_root / "rta-abc123").mkdir(parents=True)
    (contained_root / "rta-abc123" / "division.pid").write_text("4242")
    with patch("factory.contained.division.os.kill"):
        assert port_owner() == "rta-abc123"


def test_a_stale_pid_file_is_cleaned_up_and_ownership_moves_on(contained_root: Path) -> None:
    (contained_root / "gone" ).mkdir(parents=True)
    stale = contained_root / "gone" / "division.pid"
    stale.write_text("4242")
    with patch("factory.contained.division.os.kill", side_effect=ProcessLookupError):
        assert port_owner() is None
    assert not stale.exists()


def test_a_process_owned_by_someone_else_still_counts_as_the_owner(
    contained_root: Path
) -> None:
    """`PermissionError` from signal 0 means the process exists — which is the question asked."""
    (contained_root / "rta-abc123").mkdir(parents=True)
    (contained_root / "rta-abc123" / "division.pid").write_text("4242")
    with patch("factory.contained.division.os.kill", side_effect=PermissionError):
        assert port_owner() == "rta-abc123"


def test_a_directory_with_no_pid_file_is_skipped(contained_root: Path) -> None:
    (contained_root / "rta-abc123").mkdir(parents=True)
    assert port_owner() is None


# --------------------------------------------------------------------------------------------
# The two port probes, which ask opposite questions
# --------------------------------------------------------------------------------------------


def test_the_pre_launch_probe_does_not_wait() -> None:
    """It runs before anything is started, so blocking would delay every `--division` launch."""
    socket = MagicMock()
    socket.__enter__.return_value.connect_ex.return_value = 0
    with patch("factory.contained.division.socket.socket", return_value=socket):
        assert port_in_use(DIVISION_PORT) is True


def test_nothing_listening_reports_free() -> None:
    socket = MagicMock()
    socket.__enter__.return_value.connect_ex.return_value = 61
    with patch("factory.contained.division.socket.socket", return_value=socket):
        assert port_in_use(DIVISION_PORT) is False


def test_waiting_returns_as_soon_as_the_server_binds() -> None:
    """`npx` downloads the package before the process exists at all, so the wait has to be real —
    but it must not add latency once the server is up."""
    socket = MagicMock()
    socket.__enter__.return_value.connect_ex.return_value = 0
    with patch("factory.contained.division.socket.socket", return_value=socket), \
         patch("factory.contained.division.time.sleep") as sleep:
        assert wait_for_listening(DIVISION_PORT, timeout=5) is True
    sleep.assert_not_called()


def test_waiting_gives_up_at_the_deadline() -> None:
    socket = MagicMock()
    socket.__enter__.return_value.connect_ex.return_value = 61
    with patch("factory.contained.division.socket.socket", return_value=socket), \
         patch("factory.contained.division.time.sleep"):
        assert wait_for_listening(DIVISION_PORT, timeout=0.01) is False


# --------------------------------------------------------------------------------------------
# probe_host_alias — which name for "the host" a container can actually reach
# --------------------------------------------------------------------------------------------


def test_the_first_reachable_candidate_wins_and_the_rest_are_not_tried() -> None:
    with patch("factory.contained.division.subprocess.run",
               return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
        assert probe_host_alias("img", ("a", "b")) == "a"
    assert run.call_count == 1


def test_an_unreachable_candidate_is_skipped_for_the_next() -> None:
    """On macOS podman's own name for the host resolves to the VM's gateway rather than to macOS,
    so the canonical name is routinely the one that fails."""
    results = [
        subprocess.CompletedProcess([], 7, "", "connection refused"),
        subprocess.CompletedProcess([], 0, "", ""),
    ]
    with patch("factory.contained.division.subprocess.run", side_effect=results):
        assert probe_host_alias("img", ("a", "b")) == "b"


def test_a_probe_that_cannot_run_is_skipped_rather_than_aborting_the_sweep() -> None:
    results = [subprocess.TimeoutExpired(cmd="podman", timeout=60),
               subprocess.CompletedProcess([], 0, "", "")]
    with patch("factory.contained.division.subprocess.run", side_effect=results):
        assert probe_host_alias("img", ("a", "b")) == "b"


def test_no_reachable_candidate_is_a_hard_none() -> None:
    """An agent given a tool endpoint it cannot reach fails on its first build with a connection
    error that reads like a podman fault."""
    with patch("factory.contained.division.subprocess.run",
               return_value=subprocess.CompletedProcess([], 7, "", "")):
        assert probe_host_alias("img", ("a", "b")) is None


# --------------------------------------------------------------------------------------------
# The brief
# --------------------------------------------------------------------------------------------


def test_the_brief_lands_inside_the_workspace_where_the_agent_will_read_it() -> None:
    assert brief_path(Path("/w/rta")) == Path("/w/rta") / DIVISION_BRIEF_PATH
