"""Which UID the container runs as — the decision that silently costs an agent its edits.

A bind mount carries ownership through unchanged, so a container whose UID does not own the
workspace gets a read-only tree and *no error*: the failure surfaces several steps later as an agent
whose file writes vanished. Every branch here is therefore asserted on the concrete argv or the
concrete `Identity`, not on "it returned something".

Nothing in this file may reach a real podman. `identity.py` shells out through the module-global
`subprocess`, so that is what is patched; a leak would show up as a multi-second test.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.contained.identity import (
    Identity,
    IdentityError,
    mount_owner,
    podman_is_rootless,
    resolve_identity,
)
from factory.podman import Mount


def _completed(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.fixture()
def mount(tmp_path: Path) -> Mount:
    workspace = tmp_path / "rta"
    workspace.mkdir()
    return Mount(source=workspace, target=str(workspace))


def _info(rootless: object) -> str:
    return json.dumps({"host": {"security": {"rootless": rootless}}})


# --------------------------------------------------------------------------------------------
# Asking podman which mode it is in
# --------------------------------------------------------------------------------------------


def test_rootless_connection_is_reported_as_rootless() -> None:
    """The answer decides between keep-id and an explicit --user, so a bool must survive intact."""
    with patch("factory.contained.identity.subprocess.run",
               return_value=_completed(_info(True))) as run:
        assert podman_is_rootless() is True
    assert run.call_args.args[0] == ["podman", "info", "--format", "json"]


def test_rootful_connection_is_reported_as_rootful() -> None:
    with patch("factory.contained.identity.subprocess.run", return_value=_completed(_info(False))):
        assert podman_is_rootless() is False


def test_a_missing_podman_binary_is_unknown_rather_than_an_exception() -> None:
    """`podman_is_rootless` is called before anything is provisioned; it must not raise there."""
    with patch("factory.contained.identity.subprocess.run", side_effect=FileNotFoundError):
        assert podman_is_rootless() is None


def test_an_unreachable_engine_is_unknown_rather_than_rootful() -> None:
    """`podman info` fails when the machine is stopped. Reading that as "rootful" would send the
    run down the probe path with a nonzero exit code already in hand."""
    with patch("factory.contained.identity.subprocess.run",
               return_value=_completed("", returncode=125)):
        assert podman_is_rootless() is None


def test_output_that_is_not_json_is_unknown() -> None:
    with patch("factory.contained.identity.subprocess.run",
               return_value=_completed("Cannot connect to Podman")):
        assert podman_is_rootless() is None


def test_a_non_boolean_rootless_field_is_unknown_not_truthy() -> None:
    """Some podman builds report this as a string. `bool("false")` is True, which would pick
    keep-id on a rootful connection — where podman rejects it outright."""
    with patch("factory.contained.identity.subprocess.run",
               return_value=_completed(_info("false"))):
        assert podman_is_rootless() is None


def test_info_without_a_security_section_is_unknown() -> None:
    with patch("factory.contained.identity.subprocess.run", return_value=_completed("{}")):
        assert podman_is_rootless() is None


# --------------------------------------------------------------------------------------------
# The probe: who owns the mount, as the kernel inside the container sees it
# --------------------------------------------------------------------------------------------


def test_the_probe_mounts_the_workspace_and_stats_it(mount: Mount) -> None:
    """The probe is the contract — it has to mount the same path the run will and stat *that*."""
    with patch("factory.contained.identity.subprocess.run",
               return_value=_completed("1000:1000\n")) as run:
        assert mount_owner("img:latest", mount) == (1000, 1000)
    argv = run.call_args.args[0]
    assert argv[:5] == ["podman", "run", "--rm", "-v", mount.as_flag()]
    assert argv[-4:] == ["stat", "-c", "%u:%g", mount.target]


def test_only_the_last_line_of_the_probe_is_parsed(mount: Mount) -> None:
    """A cold image pull writes progress to stdout ahead of the answer."""
    with patch("factory.contained.identity.subprocess.run",
               return_value=_completed("Trying to pull img:latest...\n0:0\n")):
        assert mount_owner("img:latest", mount) == (0, 0)


def test_a_failed_probe_is_none_rather_than_a_guess(mount: Mount) -> None:
    with patch("factory.contained.identity.subprocess.run",
               return_value=_completed("", returncode=125, stderr="no such image")):
        assert mount_owner("img:latest", mount) is None


def test_a_probe_that_times_out_is_none(mount: Mount) -> None:
    """A stopped podman machine hangs rather than failing; 120s later this must still answer."""
    with patch("factory.contained.identity.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="podman", timeout=120)):
        assert mount_owner("img:latest", mount) is None


def test_a_probe_that_cannot_start_is_none(mount: Mount) -> None:
    with patch("factory.contained.identity.subprocess.run", side_effect=PermissionError):
        assert mount_owner("img:latest", mount) is None


def test_probe_output_that_is_not_a_uid_pair_is_none(mount: Mount) -> None:
    """`stat` on a path the machine does not share prints an error to stdout on some builds."""
    with patch("factory.contained.identity.subprocess.run",
               return_value=_completed("stat: cannot statx\n")):
        assert mount_owner("img:latest", mount) is None


def test_an_empty_probe_answer_is_none(mount: Mount) -> None:
    with patch("factory.contained.identity.subprocess.run", return_value=_completed("   \n")):
        assert mount_owner("img:latest", mount) is None


# --------------------------------------------------------------------------------------------
# Resolving the identity the run is created with
# --------------------------------------------------------------------------------------------


def test_dry_run_projects_the_host_uid_and_starts_nothing(mount: Mount) -> None:
    """Composing a command must not provision anything, not even a throwaway probe container."""
    with patch("factory.contained.identity.subprocess.run") as run:
        identity = resolve_identity("img:latest", mount, dry_run=True)
    run.assert_not_called()
    assert identity.userns is None
    assert identity.user is not None and identity.user.endswith(":0")
    assert "dry-run" in identity.detail


def test_rootless_podman_uses_keep_id_and_never_probes(mount: Mount) -> None:
    """keep-id maps the host UID straight through, so the answer is known without measuring."""
    with patch("factory.contained.identity.podman_is_rootless", return_value=True), \
         patch("factory.contained.identity.mount_owner") as probe:
        identity = resolve_identity("img:latest", mount)
    probe.assert_not_called()
    assert identity == Identity(user=None, userns="keep-id", detail=identity.detail)
    assert "keep-id" in identity.detail


def test_rootful_podman_runs_as_the_uid_the_container_sees(mount: Mount) -> None:
    """The probe's answer, not the host's `ls -l`: under rootful podman they differ."""
    with patch("factory.contained.identity.podman_is_rootless", return_value=False), \
         patch("factory.contained.identity.mount_owner", return_value=(501, 20)):
        identity = resolve_identity("img:latest", mount)
    assert identity.user == "501:0"
    assert identity.userns is None


def test_group_zero_is_used_rather_than_the_mounts_own_gid(mount: Mount) -> None:
    """The runtime image follows the arbitrary-UID convention — group 0 with g=u — which is also
    what the cluster's restricted SCC requires. One image, one identity story."""
    with patch("factory.contained.identity.podman_is_rootless", return_value=False), \
         patch("factory.contained.identity.mount_owner", return_value=(501, 20)):
        identity = resolve_identity("img:latest", mount)
    assert identity.user == "501:0" and not identity.user.endswith(":20")


def test_an_unreachable_podman_falls_through_to_the_probe(mount: Mount) -> None:
    """`podman info` failing is not evidence of rootlessness, so keep-id must not be assumed —
    podman rejects `--userns=keep-id` outright on a rootful connection."""
    with patch("factory.contained.identity.podman_is_rootless", return_value=None), \
         patch("factory.contained.identity.mount_owner", return_value=(0, 0)) as probe:
        identity = resolve_identity("img:latest", mount)
    probe.assert_called_once()
    assert identity.user == "0:0"


def test_an_unreadable_mount_aborts_before_anything_is_provisioned(mount: Mount) -> None:
    """This is the failure the module exists to prevent, so it must be loud and reproducible: the
    message carries the exact `podman run` the user can paste to see it themselves."""
    with patch("factory.contained.identity.podman_is_rootless", return_value=False), \
         patch("factory.contained.identity.mount_owner", return_value=None):
        with pytest.raises(IdentityError) as excinfo:
            resolve_identity("img:latest", mount)
    message = str(excinfo.value)
    assert mount.target in message
    assert "podman machine start" in message
    assert f"podman run --rm -v {mount.as_flag()} img:latest" in message
