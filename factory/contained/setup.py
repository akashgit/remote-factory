"""Getting a machine (or a namespace) ready to run `factory contained`, in one pass (spec §2.6).

`verify` reports; `setup` fixes. It ends in exactly one of two states — everything green with a
runnable command printed, or the full list of what is still missing with the command for each.
Never in between.

Two properties make it safe to run at any time:

- **Idempotent.** Re-running changes nothing that is already correct, so it is also the supported
  way to repair a partial setup and nothing needs to be torn down first.
- **Nothing silent.** Every step announces what it will do before doing it, and steps that touch
  credentials or a cluster ask first.

What is automated locally is deliberately narrow: starting a stopped podman machine and pulling the
runtime image. Inference is never automated — it is the one step that touches credential material,
so it is described and left to the user.
"""

from __future__ import annotations

import subprocess
import sys

import structlog

from factory.contained.prereq import Check, local_checks, render_checks
from factory.podman import build_pull_argv, resolve_image

log = structlog.get_logger()


def run_setup(
    target: str | None,
    *,
    interactive: bool,
    namespace: str | None = None,
    division: bool = False,
    assume_yes: bool = False,
) -> int:
    """Run setup for one target, or ask which when not told."""
    if target is None and interactive:
        target = _ask_target()

    code = 0
    if target in (None, "local", "both"):
        _setup_local()
        checks = local_checks()
        print(render_checks(checks, setup_command=None))
        code = 0 if all(c.ok for c in checks) else 1

    if target in ("k8s", "both"):
        from factory.contained.k8s_setup import setup_k8s

        k8s_code = setup_k8s(
            namespace=namespace,
            division=division,
            interactive=interactive,
            assume_yes=assume_yes,
        )
        code = code or k8s_code

    return code


def _ask_target() -> str:
    print("What are you setting up?")
    print("  1) local  — a podman container on this machine")
    print("  2) k8s    — a pod on a cluster")
    print("  3) both")
    try:
        choice = input("Choice [1]: ").strip() or "1"
    except EOFError:
        # stdin closed before an answer arrived — a pipe, a CI job, or `< /dev/null`. The default
        # is the documented one; an unanswered prompt must not become a bare `Error:`.
        print("\nNo answer given; setting up the local runtime (the default).")
        return "local"
    return {"1": "local", "2": "k8s", "3": "both"}.get(choice, "local")


def _setup_local() -> None:
    """Perform the local steps that are safe to automate; describe the ones that are not.

    Every branch announces before acting. The trailing `local_checks()`/`render_checks()` in
    `run_setup` is what reports the outcome, including for the cases handled here — so nothing in
    this function needs its own second, weaker copy of a check's message.
    """
    engine = next((c for c in local_checks() if c.name == "container_engine"), None)
    if engine is not None and not engine.ok:
        _start_machine()

    image = resolve_image()
    if _image_present(image):
        print(f"Runtime image already present: {image}")
    else:
        print(f"Pulling the runtime image: {image}")
        result = subprocess.run(build_pull_argv(image))
        if result.returncode != 0:
            print(
                f"\nCould not pull {image}.\n"
                "That usually means the image is not published yet, or the registry needs a login "
                "(`podman login ghcr.io`).\n"
                "\n"
                "Either way you have two options:\n"
                "  1. Use an image you already have:\n"
                "       export FACTORY_CONTAINED_IMAGE=<your-image-reference>\n"
                "  2. Build one from a checkout of this repository:\n"
                "       git clone https://github.com/akashgit/remote-factory\n"
                "       cd remote-factory\n"
                f"       podman build -f containers/factory/Containerfile -t {image} .\n"
                "     (the Containerfile ships in the git repository, not in the installed "
                "package)",
                file=sys.stderr,
            )


def _image_present(reference: str) -> bool:
    from factory.podman import build_image_exists_argv

    try:
        return subprocess.run(build_image_exists_argv(reference), capture_output=True).returncode == 0
    except (FileNotFoundError, PermissionError, OSError):
        return False


def _start_machine() -> None:
    """Start a stopped podman machine, announcing first.

    Automated because it mutates nothing durable and because on macOS it is the single most common
    reason a contained run fails — the machine stops quietly and every later error blames podman.
    """
    try:
        listed = subprocess.run(
            ["podman", "machine", "list", "--format", "{{.Name}}"],
            capture_output=True, text=True,
        )
    except (FileNotFoundError, PermissionError, OSError):
        return
    if listed.returncode != 0 or not listed.stdout.strip():
        print("No podman machine found. Create one with: podman machine init")
        return
    print("The podman engine is not reachable. Starting the podman machine...")
    subprocess.run(["podman", "machine", "start"])


def summarize(checks: list[Check]) -> str:
    """Shorthand used by `verify`'s callers that want the same rendering as setup."""
    return render_checks(checks)
