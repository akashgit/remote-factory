"""The local container-manufacturing plane — opt-in via `--division` (spec §5).

The division gives the contained agent the *host's* podman engine, so it can build an image, run it,
read the failure and iterate.

**Why the builds happen outside the container.** The runtime container has no container engine of
its own, and giving it one means nested containerization — which needs a privileged container or a
user-namespace configuration that is fragile on Linux and unavailable inside the macOS podman
machine. So the division reaches outward, and it is opt-in and separately named for exactly that
reason. (Under the previous runtime the reason was different — a seccomp filter blocked `mount` and
`CLONE_NEWUSER` for the agent and every child — but the conclusion is the same.)

**What that costs, stated plainly.** For the life of the run, anything that can reach port 8430 can
build and run containers on the host. `podman-mcp-server` has no authentication and the pivot
removed the enforcement point that used to sit in front of it, so the mitigation here is disclosure
rather than technology: a loud warning naming the endpoint and its lifetime, and a shutdown tied to
the run rather than left to chance. §5.2 of the design records what tightening looks like when that
time comes.

Four mechanical details, each of which fails silently if got wrong:

- The server speaks **Streamable HTTP** (`--port 8430`, endpoint `/mcp`) — it is not a stdio server.
- It nonetheless **exits when stdin reaches EOF**, even in HTTP mode, which is why a naive
  background spawn leaves nothing listening and writes no error. `server_argv` gives it a writer
  that never writes and never exits.
- The address the *container* must use for "the host" is platform-dependent. On macOS the container
  runs inside the podman machine VM, so podman's own `host.containers.internal` may resolve to the
  VM's gateway rather than to macOS. `probe_host_alias` asks rather than assumes. (Probed on this
  machine — macOS, libkrun, rootful — all three candidates reach a server bound on the host.)
- **The server must outlive the command that started it**, which is the one place this module
  departs from a literal reading of §5.1. The launch returns as soon as the detached tmux session
  exists, by design (§3.1), while the run continues for minutes or hours; a server whose lifetime
  was the launcher's would be gone before the agent's first build. So it is detached into its own
  process group, its PGID is recorded next to the workspace, and `factory contained rm` stops it.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

from factory.contained.errors import ContainedError
from factory.contained.workspace import contained_home
from factory.podman import (
    ContainerPlan,
    HOST_ALIAS,
    build_run_command,
)

log = structlog.get_logger()

DIVISION_PORT = 8430
MCP_SERVER_PACKAGE = "podman-mcp-server"
MCP_SERVER_NAME = "podman"

# `npx` downloads the package on a cold first run before the server process exists at all, so this
# is sized for that case rather than for a warm start.
STARTUP_TIMEOUT = 90.0

# Candidates for "the host", most-canonical first. `host.containers.internal` is podman's own name
# and is right on Linux; on macOS it resolves to the podman machine VM rather than to macOS, so the
# gvproxy host-gateway address is tried next. Probed, never assumed (spec §5.1, open item F6).
HOST_CANDIDATES = (HOST_ALIAS, "192.168.127.254", "host.docker.internal")

DIVISION_BRIEF_PATH = ".factory/division/README.md"

DIVISION_BRIEF = """\
# Container division — you can build and run images

This run has the container-manufacturing plane enabled. **This is a capability you already have,
not something to build.** Do not write a CLI wrapper around podman; call the tools.

## The tools

They are registered as `mcp__{server}__*` and cover the whole podman surface: build an image, run a
container, read its logs, stop it, remove it, inspect it, list images and containers, pull, push.

## The loop

1. **build** — `image_build` with a Containerfile and a tag. The build context is a path on the
   host, and your workspace is mounted at the same absolute path inside and out, so the path you
   can see is the path the build engine resolves.
2. **run** — start a container on the tag you just built.
3. **read** — fetch its logs. This is the step that tells you whether the image actually works,
   as opposed to whether it built.
4. **fix** — edit the Containerfile or the source, and go back to 1.

A build that succeeds is not evidence that the image runs. Always complete the loop.

## What is true about this environment

- Builds execute on the **host's** engine, outside this container. They are not confined by it.
- Images you build land in the host's image store and are visible to `podman images` there.
- The endpoint is unauthenticated and lives only for the length of this run.
- You cannot reach the cluster from here. This division is the host's engine, nothing else.
"""


@dataclass
class Division:
    """A running division: the server process, the address the container reaches it at, the plan."""

    plan: ContainerPlan
    endpoint: str
    process: subprocess.Popen[bytes] | None
    pid_file: Path | None = None

    def keep(self) -> None:
        """Record the server so it can be stopped later, and leave it running.

        **The endpoint has to outlive the command that started it.** The launch returns as soon as
        the detached tmux session exists — that is what lets it print the run's identifier instead
        of blocking for the length of a cycle (spec §3.1) — but the run itself keeps going for
        minutes or hours afterwards. A server tied to the launching process would be gone before
        the agent's first build, and the agent would see a connection error that reads like a
        podman fault.

        So the server is detached into its own process group and its PGID is written next to the
        workspace. `factory contained rm <name>` stops it, and `stop()` below is what the launch
        path uses when it fails partway and the server should not survive.
        """
        if self.process is None or self.pid_file is None:
            return
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(str(self.process.pid))
        log.debug("division_kept", pid=self.process.pid, pid_file=str(self.pid_file))

    def stop(self) -> None:
        """Shut the server down. Used when the launch fails; `rm` uses `stop_recorded`."""
        if self.process is None:
            return
        if self.process.poll() is not None:
            log.debug("division_already_exited", returncode=self.process.returncode)
            print("Division: podman-mcp-server had already exited.", file=sys.stderr)
            self.process = None
            return
        _kill_group(self.process.pid)
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        log.debug("division_stopped", port=DIVISION_PORT)
        print(
            f"Division: podman-mcp-server stopped; nothing is listening on {DIVISION_PORT}.",
            file=sys.stderr,
        )
        if self.pid_file is not None and self.pid_file.exists():
            self.pid_file.unlink()
        self.process = None


def _kill_group(pid: int) -> None:
    """Signal the whole process group.

    The server is one half of a shell pipeline (see `server_argv`), so signalling only the shell
    leaves the other half — and whatever it is feeding — behind.
    """
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError) as exc:
        log.warning("division_kill_failed", pid=pid, error=str(exc))


def pid_file_for(run_id: str) -> Path:
    """Where a run's division PID is recorded. Next to the workspace, not inside it."""
    return contained_home() / run_id / "division.pid"


def stop_recorded(run_id: str) -> bool:
    """Stop the division belonging to a run, if one is recorded. Called by `rm`.

    Returns whether anything was stopped. A stale PID file — the process already gone — is cleaned
    up and reported as nothing stopped, rather than left to accumulate.
    """
    pid_file = pid_file_for(run_id)
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return False
    _kill_group(pid)
    pid_file.unlink(missing_ok=True)
    log.debug("division_stopped_by_lifecycle", run_id=run_id, pid=pid)
    return True


def server_argv(port: int = DIVISION_PORT) -> list[str]:
    """The command that starts the server.

    Two things here are not decoration. `npx -y` rather than a global install, so the runtime does
    not require one more thing on the host to have been set up in advance; the package is cached
    after the first run.

    And the `tail -f /dev/null |` prefix, because the server **exits when stdin reaches EOF even in
    HTTP mode**. A background spawn with stdin closed — or pointed at /dev/null, which EOFs
    immediately — leaves nothing listening and writes no error at all. The pipeline gives it a
    writer that never writes and never exits, which is a stdin that stays open without the
    launching process having to stay alive to hold it.
    """
    return ["sh", "-c", f"tail -f /dev/null | npx -y {MCP_SERVER_PACKAGE} --port {port}"]


def wait_for_listening(port: int, timeout: float | None = None) -> bool:
    """Block until something accepts on `port`, or the timeout expires.

    Without this the container-side reachability probe runs against a server that has not finished
    starting, concludes the host is unreachable, and tears down a division that was seconds from
    working. `npx` makes the first run the slow case: it downloads the package before the server
    process exists at all.

    Checked from the host rather than from a container because this question is only "has it bound
    the port yet" — *which address the container must use* is the separate question
    `probe_host_alias` answers, and conflating the two makes a slow start look like a routing fault.
    """
    deadline = time.monotonic() + (STARTUP_TIMEOUT if timeout is None else timeout)
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(1.0)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def probe_argv(image: str, host: str, port: int = DIVISION_PORT) -> list[str]:
    """A throwaway container that asks whether `host:port` is reachable from inside one.

    Any HTTP response counts as reachable — the endpoint answers a bare GET with an error, and it
    is the TCP path being tested, not the protocol. `curl` returns non-zero only when it could not
    connect at all.
    """
    return [
        "podman", "run", "--rm", image,
        "curl", "-sS", "--max-time", "3", "-o", "/dev/null", f"http://{host}:{port}/mcp",
    ]


def probe_host_alias(image: str, candidates: tuple[str, ...] = HOST_CANDIDATES) -> str | None:
    """Which name for "the host" a container can actually reach the division at.

    Returns None when none of them work, which is a hard failure for the caller: an agent given a
    tool endpoint it cannot reach fails on its first build with a connection error that reads like
    a podman fault.
    """
    for host in candidates:
        try:
            result = subprocess.run(
                probe_argv(image, host), capture_output=True, text=True, timeout=60
            )
        except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            log.debug("division_host_resolved", host=host)
            return host
        log.debug("division_host_unreachable", host=host, stderr=result.stderr.strip()[:120])
    return None


def mcp_config(endpoint: str) -> dict[str, object]:
    """The `.mcp.json` the container writes next to the project before the factory starts."""
    return {"mcpServers": {MCP_SERVER_NAME: {"type": "http", "url": endpoint}}}


def port_owner() -> str | None:
    """Which run already owns the division port, if any.

    One port, one server, and the PID file is the only record of whose it is. Without this check a
    second `--division` run finds the port bound, concludes the server it just started came up, and
    silently drives the *first* run's endpoint — after which `rm` on either one pulls the tools out
    from under the other. Both symptoms appear far from the cause.
    """
    home = contained_home()
    if not home.is_dir():
        return None
    for candidate in sorted(home.iterdir()):
        pid_file = candidate / "division.pid"
        try:
            pid = int(pid_file.read_text().strip())
        except (OSError, ValueError):
            continue
        try:
            os.kill(pid, 0)                       # signal 0: does the process still exist?
        except ProcessLookupError:
            pid_file.unlink(missing_ok=True)      # stale; the run is gone
            continue
        except PermissionError:
            pass                                  # alive, owned by someone else
        return candidate.name
    return None


def _warn(endpoint: str, run_id: str, *, dry_run: bool = False) -> None:
    """Tell the user what was started, what it exposes, and what to do about it.

    All three, in that order. The exposure is real and the user cannot mitigate it by understanding
    our reasoning — only by knowing the bind scope and having a way to stop it.
    """
    started = "Would start" if dry_run else "Started"
    stop = (
        "It stops when the run is removed:"
        if not dry_run
        else "Nothing was started — this is a dry run."
    )
    print(
        "\n"
        "  ┌─ Container builds enabled (--division) ───────────────────────────────────────\n"
        f"  │ {started} podman-mcp-server so the agent can build and run container images.\n"
        f"  │ The run reaches it at {endpoint}\n"
        "  │\n"
        f"  │ It listens on 0.0.0.0:{DIVISION_PORT} — every network interface, not just this\n"
        "  │ machine — and it has no authentication. For as long as the run lasts, anyone\n"
        "  │ who can reach that port can build and run containers as you.\n"
        "  │\n"
        "  │ Avoid --division on untrusted networks.\n"
        f"  │ {stop}\n"
        + (f"  │     factory contained rm {run_id}\n" if not dry_run else "")
        + "  └───────────────────────────────────────────────────────────────────────────────\n",
        file=sys.stderr,
    )


def start_local_division(plan: ContainerPlan, *, dry_run: bool = False) -> Division:
    """Start the division and fold its registration and brief into the plan.

    In dry-run nothing is spawned and nothing is probed: the plan is composed against the canonical
    host alias so the printed argv has the same shape the real path produces, and `stop()` on the
    returned object is a no-op.
    """
    if dry_run:
        endpoint = f"http://{HOST_ALIAS}:{DIVISION_PORT}/mcp"
        _warn(endpoint, plan.name, dry_run=True)
        print(f"[division] {' '.join(server_argv())}", file=sys.stderr)
        return Division(plan=_with_division(plan, endpoint), endpoint=endpoint, process=None)

    if shutil.which("npx") is None:
        raise ContainedError(
            "--division needs `npx` on PATH to start podman-mcp-server. Install Node.js "
            "(`brew install node`) and retry, or drop --division to run without the "
            "container-manufacturing plane."
        )

    owner = port_owner()
    if owner is not None and owner != plan.name:
        raise ContainedError(
            f"the division port {DIVISION_PORT} is already held by the run {owner!r}. One port, one "
            f"server: starting a second would silently drive {owner}'s endpoint, and stopping "
            "either would pull the tools out from under the other. Finish or remove that run first "
            f"(`factory contained rm {owner}`), or run this one without --division."
        )

    log_dir = contained_home() / plan.name
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "division.log"
    # `start_new_session` puts the pipeline in its own process group, which is what lets the server
    # survive this command and still be stoppable as a unit later.
    with log_path.open("ab") as handle:
        process = subprocess.Popen(
            server_argv(),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=handle,
            start_new_session=True,
        )
    log.debug("division_started", pid=process.pid, port=DIVISION_PORT, log=str(log_path))

    if not wait_for_listening(DIVISION_PORT):
        division = Division(
            plan=plan, endpoint="", process=process, pid_file=pid_file_for(plan.name)
        )
        division.stop()
        raise ContainedError(
            f"podman-mcp-server did not start listening on port {DIVISION_PORT} within "
            f"{STARTUP_TIMEOUT}s. Its output is in {log_path}. A first run downloads the package, "
            "which is the slow case; a port already in use is the other."
        )

    host = probe_host_alias(plan.image)
    if host is None:
        division = Division(
            plan=plan, endpoint="", process=process, pid_file=pid_file_for(plan.name)
        )
        division.stop()
        raise ContainedError(
            f"the division's endpoint on port {DIVISION_PORT} is not reachable from inside a "
            f"container by any of {', '.join(HOST_CANDIDATES)}. On macOS the container runs inside "
            "the podman machine VM, so podman's name for the host resolves to the VM's gateway "
            "rather than to this machine — check that podman-mcp-server binds 0.0.0.0 and that the "
            "port is not firewalled."
        )
    endpoint = f"http://{host}:{DIVISION_PORT}/mcp"
    _warn(endpoint, plan.name)
    return Division(
        plan=_with_division(plan, endpoint),
        endpoint=endpoint,
        process=process,
        pid_file=pid_file_for(plan.name),
    )


def _with_division(plan: ContainerPlan, endpoint: str) -> ContainerPlan:
    """Re-compose the run command with the MCP registration and the brief.

    The brief is not decoration. Without it, a Refiner given only the tool registration scoped 165
    lines of new CLI code to wrap the tools it already had, while its own task text forbade
    modifying source (spec §5.4).
    """
    return dataclasses.replace(
        plan,
        run_command=build_run_command(
            plan.workdir,
            plan.factory_command,
            mcp_config=mcp_config(endpoint),
            files={DIVISION_BRIEF_PATH: DIVISION_BRIEF.format(server=MCP_SERVER_NAME)},
        ),
    )


def brief_path(workspace: Path) -> Path:
    """Where the brief lands in a workspace. Used by tests and by the k8s division."""
    return workspace / DIVISION_BRIEF_PATH
