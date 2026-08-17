"""Two directions `verify` gets wrong quietly: a live engine error, and a failure setup cannot fix.

Nothing in `prereq` may raise — "nothing installed yet" is the normal case it exists to describe,
so a clean machine must get a list of what is missing rather than a traceback.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from factory.contained.prereq import Check, local_checks, render_checks


def _completed(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_an_engine_failure_carries_its_own_first_line_into_the_detail() -> None:
    """"podman is installed but its engine is not reachable" is true of a dozen causes. The
    engine's own first line is what distinguishes "machine stopped" from "socket permission"."""
    with patch("factory.contained.prereq.shutil.which", return_value="/usr/bin/podman"), \
         patch("factory.contained.prereq._run",
               return_value=_completed("", returncode=125,
                                       stderr="Cannot connect to Podman socket\nmore detail\n")):
        engine = next(c for c in local_checks() if c.name == "container_engine")
    assert not engine.ok
    assert "Cannot connect to Podman socket" in engine.detail
    assert "more detail" not in engine.detail


def test_an_engine_that_cannot_be_reached_at_all_still_names_the_fix() -> None:
    with patch("factory.contained.prereq.shutil.which", return_value="/usr/bin/podman"), \
         patch("factory.contained.prereq._run", return_value=None):
        engine = next(c for c in local_checks() if c.name == "container_engine")
    assert not engine.ok and engine.fix == "podman machine start"


def test_a_failure_setup_cannot_repair_does_not_advertise_setup() -> None:
    """Telling someone to run a command that will not fix their problem sends them round in
    circles — inference is deliberately not automated, because it touches credential material."""
    rendered = render_checks([Check(name="inference", ok=False, detail="no key",
                                    fix="export ANTHROPIC_API_KEY=...")])
    assert "factory contained setup" not in rendered
    assert "shows the command that fixes it" in rendered


def test_a_repairable_failure_names_setup_and_which_checks_it_covers() -> None:
    rendered = render_checks([Check(name="runtime_image", ok=False, detail="absent", fix="pull")])
    assert "factory contained setup" in rendered
    assert "runtime_image" in rendered
