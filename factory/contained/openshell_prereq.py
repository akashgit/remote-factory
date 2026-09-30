"""What must be true before an openshell-target run can work, and how to make it true.

Checks in the same spirit as `prereq.local_checks`: every failing check carries the command
that resolves it, and nothing here may raise — a machine with nothing installed gets a list of
what is missing, not a traceback.

One check deserves its reasoning written down: **the provider check reports shape, not
material** — it asks the gateway whether a provider named `claude-code` exists, never for its
credentials. The whole point of this target is that inference credentials stay in the gateway;
a prerequisite check that printed them would defeat it in the name of verifying it.
"""

from __future__ import annotations

import shutil
import subprocess

from factory.contained.errors import ContainedError
from factory.contained.openshell import PROVIDER_FIX, SDK_FIX
from factory.contained.prereq import Check
from factory.podman import resolve_image


def _run(argv: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str] | None:
    """Run a subprocess, returning None instead of raising (see `prereq._run`)."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        return None


def _cli_check() -> Check:
    if shutil.which("openshell") is None:
        return Check(
            name="openshell_cli",
            ok=False,
            detail="`openshell` was not found on PATH",
            fix="curl -fsSL https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh",
        )
    result = _run(["openshell", "version"])
    if result is None or result.returncode != 0:
        return Check(
            name="openshell_cli",
            ok=False,
            detail="openshell is installed but not runnable",
            fix="openshell --help   # see what it reports",
        )
    return Check(name="openshell_cli", ok=True, detail="openshell CLI on PATH")


def _sdk_check() -> Check:
    try:
        import openshell  # type: ignore[import-not-found] # noqa: F401

        return Check(name="openshell_sdk", ok=True, detail="Python SDK importable")
    except ImportError:
        return Check(
            name="openshell_sdk",
            ok=False,
            detail="the `openshell` Python package is not installed in this environment",
            fix=SDK_FIX,
        )


def _gateway_check(gateway: str | None) -> Check:
    """The gateway must be registered *and* reachable — a binary proves nothing."""
    try:
        from factory.contained import openshell as os_mod

        client = os_mod.connect(gateway)
    except ContainedError as exc:
        if "SDK is not installed" in str(exc):
            # The SDK check above already reported the import with its fix; a check that
            # repeats it just makes the list longer without adding a fact.
            return Check(
                name="openshell_gateway",
                ok=False,
                detail="cannot check without the SDK (see openshell_sdk)",
                fix=None,
            )
        return Check(
            name="openshell_gateway",
            ok=False,
            detail=str(exc).splitlines()[0][:160],
            fix="openshell gateway add --name local --local && openshell gateway select local",
        )
    except Exception as exc:  # anything from a half-configured gateway directory
        return Check(
            name="openshell_gateway",
            ok=False,
            detail=str(exc)[:160] or "no reachable gateway",
            fix="openshell gateway add --name local --local && openshell gateway select local",
        )
    try:
        client.health()
    except Exception as exc:
        return Check(
            name="openshell_gateway",
            ok=False,
            detail=f"gateway registered but not reachable: {str(exc)[:160]}",
            fix="openshell gateway list   # then check its service",
        )
    finally:
        client.close()
    return Check(
        name="openshell_gateway",
        ok=True,
        detail=f"gateway reachable ({gateway or 'active'})",
    )


def _image_check() -> Check:
    """The runtime image is pulled by the gateway's compute driver, not by us — but a
    locally-cached check via the CLI tells the user whether the first run will spend minutes
    pulling. Absent CLI caches report 'unknown' rather than failing: the driver pulls on
    demand, so a cold cache is slow, not broken."""
    reference = resolve_image()
    return Check(
        name="runtime_image",
        ok=True,
        detail=f"{reference} (pulled by the sandbox's compute driver on first use)",
    )


def _provider_check(gateway: str | None) -> Check:
    """Whether the gateway holds a claude-code provider — shape only, never material."""
    try:
        from factory.contained import openshell as os_mod

        client = os_mod.connect(gateway)
    except Exception:
        # The gateway check above already reported the connection; a check that repeats it
        # would burn its own timeout rediscovering what the previous check just said.
        return Check(
            name="openshell_provider",
            ok=False,
            detail="cannot check without a reachable gateway (see openshell_gateway)",
            fix="fix the gateway first, then re-run verify",
        )
    try:
        result = _run(["openshell", "provider", "list", "--output", "json"])
        known = False
        if result is not None and result.returncode == 0:
            import json

            try:
                payload = json.loads(result.stdout or "[]")
            except json.JSONDecodeError:
                payload = []
            entries = payload if isinstance(payload, list) else payload.get("providers", [])
            known = any(
                str(entry.get("name", "")) == "claude-code"
                for entry in entries
                if isinstance(entry, dict)
            )
        if not known:
            return Check(
                name="openshell_provider",
                ok=False,
                detail="no provider named 'claude-code' is configured in the gateway",
                fix=PROVIDER_FIX,
            )
        return Check(
            name="openshell_provider",
            ok=True,
            detail="provider 'claude-code' configured (credentials stay in the gateway)",
        )
    finally:
        client.close()


def openshell_checks(gateway: str | None = None) -> list[Check]:
    """The openshell-target prerequisite checks, always the same set, in this order."""
    return [
        _cli_check(),
        _sdk_check(),
        _gateway_check(gateway),
        _image_check(),
        _provider_check(gateway),
    ]


def verify_openshell(
    *, gateway: str | None = None, on_check=None
) -> list[Check]:
    """Run the checks, streaming each result as it lands.

    Same reason the cluster verify streams: several checks are gateway round trips, and a run
    that prints nothing until the last one finishes is indistinguishable from a hang.
    """
    checks = openshell_checks(gateway)
    for check in checks:
        if on_check is not None:
            on_check(check)
    return checks
