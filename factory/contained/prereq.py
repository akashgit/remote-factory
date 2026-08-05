"""What must be true before a contained run can work, and how to make it true (spec §3.0, §4.0).

Three checks locally, down from the seven the OpenShell runtime needed: container engine, runtime
image, inference. The gateway, its certificates, its `gateway.toml` and the four settings in it that
failed quietly are all gone with the pivot, and so is the class of trap they carried.

Every failing check carries the command that resolves it. A check that can detect a problem can
almost always name its remedy; one that cannot says so explicitly.

Nothing here may raise. `shutil.which` gates every subprocess call and `_run` swallows
`FileNotFoundError`/`OSError`, because "nothing installed yet" is the normal case this module exists
to describe, not an error condition — a clean machine must get a list of what is missing, not a
traceback.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from factory.contained.credentials import resolve_credentials
from factory.podman import (
    build_image_exists_argv,
    build_info_argv,
    resolve_image,
)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str | None = None


def _run(argv: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str] | None:
    """Run a subprocess, returning None instead of raising when the binary is not on PATH (or
    otherwise cannot execute). Every check must degrade to `ok=False`, never crash."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        return None


def local_checks() -> list[Check]:
    """The three local prerequisite checks (spec §3.0), always the same three, in spec order."""
    return [_engine_check(), _image_check(), _inference_check()]


def _engine_check() -> Check:
    """Exercise the connection, not merely the binary.

    On macOS this is the common failure: `podman machine start` is required after a reboot and the
    machine stops quietly, so finding the binary proves nothing. `podman info` is the cheapest call
    that actually round-trips to the engine.
    """
    if shutil.which("podman") is None:
        return Check(
            name="container_engine",
            ok=False,
            detail="`podman` was not found on PATH",
            fix="brew install podman && podman machine init && podman machine start",
        )
    result = _run(build_info_argv())
    if result is None or result.returncode != 0:
        detail = "podman is installed but its engine is not reachable"
        if result is not None and result.stderr.strip():
            detail = f"{detail}: {result.stderr.strip().splitlines()[0][:160]}"
        return Check(
            name="container_engine",
            ok=False,
            detail=detail,
            fix="podman machine start",
        )
    return Check(
        name="container_engine",
        ok=True,
        detail=f"podman reachable ({_engine_summary()})",
    )


def _engine_summary() -> str:
    result = _run(["podman", "version", "--format", "{{.Client.Version}}"])
    version = result.stdout.strip() if result and result.returncode == 0 else "version unknown"
    rootless = _run(["podman", "info", "--format", "{{.Host.Security.Rootless}}"])
    mode = "rootless" if rootless and rootless.stdout.strip() == "true" else "rootful"
    return f"{version}, {mode}"


def _image_check() -> Check:
    reference = resolve_image()
    result = _run(build_image_exists_argv(reference))
    ok = result is not None and result.returncode == 0
    return Check(
        name="runtime_image",
        ok=ok,
        detail=(
            f"{reference} present locally"
            if ok
            else f"{reference} is not present locally"
        ),
        fix=(
            None if ok else
            f"factory contained setup   # pulls {reference}\n"
            f"              or, if it is not published yet, point at one you have:\n"
            f"              export FACTORY_CONTAINED_IMAGE=<your-image>"
        ),
    )


def _inference_check() -> Check:
    """Report the resolved credential *shape* — never material (spec §3.5).

    Which backend, which model, which variable or file supplied it. A check whose purpose is
    configuration must not become a way to print a key.
    """
    shape = resolve_credentials()
    return Check(name="inference", ok=shape.ok, detail=shape.detail, fix=shape.fix)


# Checks that `setup` can actually repair. Offering `setup` for anything else sends the user to a
# command that will report the same failure — a loop with no exit.
SETUP_CAN_FIX = frozenset({"container_engine", "runtime_image"})


def render_checks(
    checks: list[Check],
    *,
    ready_command: str | None = None,
    setup_command: str | None = "factory contained setup",
) -> str:
    """Render the checks, then say what to do next — and only what will work.

    `setup_command` is None when the caller *is* setup: telling someone to run the command that just
    failed is worse than saying nothing.
    """
    lines = []
    for check in checks:
        status = "ok  " if check.ok else "FAIL"
        lines.append(f"[{status}] {check.name}: {check.detail}")
        if not check.ok and check.fix:
            lines.append(f"         fix: {check.fix}")
    failures = [c for c in checks if not c.ok]
    lines.append("")
    if not failures:
        ready = ready_command or "factory contained -- ceo <path>"
        lines.append(f"All checks passed. Start a run with `{ready}`.")
        return "\n".join(lines)

    repairable = [c.name for c in failures if c.name in SETUP_CAN_FIX]
    if setup_command and repairable:
        lines.append(
            f"{len(failures)} check(s) failed. `{setup_command}` can fix "
            f"{', '.join(repairable)}; the rest need the fix shown above each one."
        )
    else:
        lines.append(
            f"{len(failures)} check(s) failed. Each one shows the command that fixes it above."
        )
    return "\n".join(lines)
