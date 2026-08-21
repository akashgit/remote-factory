"""Podman integration — composing the commands that run the factory inside a container.

Everything that knows about the `podman` CLI lives here. That surface is external and moves
independently of the factory, so keeping it in one file means one place to fix when it changes.

The module **composes** command lines and does not execute them; execution and error handling live
in `factory.cli.contained`. That split is what makes `FACTORY_CONTAINED_DRY_RUN=1` honest — dry-run
prints the same argv the real path runs, rather than a separate rendering that drifts from it.

Two shapes deserve explanation up front.

**PID 1.** The factory is not a well-behaved init: it spawns agent subprocesses, and a container
whose PID 1 neither forwards signals nor reaps children accumulates zombies and ignores `podman
stop`. So the container is created with `--init` (podman's catatonit becomes PID 1) around a
trivial `sleep infinity`, and the run itself is started afterwards inside tmux. The process tree
then has a supervisor at both levels.

**Why the factory starts via `exec` rather than as the container's command.** The provenance
assertions have to run after the workspace is in place and *before* the first agent call, and to
abort naming the file and the likely cause. Folding them into the container's command would put
their failure inside `podman logs`, where the host has to poll for container death and guess which
assertion broke. Running them as `podman exec` steps between create and run keeps per-probe exit
codes and stderr on the host, which is where the message a user reads is composed.
"""

from __future__ import annotations

import hashlib
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from factory.contained.provenance import Probe

# Dry-run env var for contained runtimes.
DRY_RUN_ENV = "FACTORY_CONTAINED_DRY_RUN"

IMAGE_ENV = "FACTORY_CONTAINED_IMAGE"
DEFAULT_IMAGE = "ghcr.io/akashgit/remote-factory/factory-runtime:latest"

LABEL_PROJECT = "factory.project"
LABEL_NAME = "factory.name"
LABEL_CONTAINED = "factory.contained"
LABEL_SOURCE = "factory.source"

# One well-known session name, because `attach` has to find it without being told.
TMUX_SESSION = "factory"

# The runtime image's home directory. The container runs under an arbitrary UID matched to the
# workspace's owner, which usually has no /etc/passwd entry — so `$HOME` has to be stated
# explicitly or the shell inherits `/` and everything that writes a dotfile writes it to the image's
# read-only root. Anything home-relative on the host (`~/.factory`, gcloud's ADC) is mounted under
# this rather than at its host path.
CONTAINER_HOME = "/home/factory"

# The container's PID-1 payload under `--init`. It has to outlive the factory:.4 keeps the
# container after the run ends, because a failed run is exactly when its state is worth reading.
# `sleep infinity` dies on SIGTERM, so `podman stop` still completes inside the grace period rather
# than escalating to SIGKILL — which is what.6 step 6 checks.
IDLE_COMMAND = "sleep infinity"

# The two variables that feed growth dimensions. They merge 50/50 into the composite score, so their
# absence does not break a run — it silently makes the run's scores incomparable to host scores.
GROWTH_CONTEXT_VARS = ("FACTORY_MANAGED_DIRS", "FACTORY_VAULT_PATH")

# podman's name for the host. On macOS the container runs inside the podman machine VM, so this
# resolves to the VM's gateway rather than to macOS itself —.1/F6, and
# `factory.contained.division`, which probes rather than assumes.
HOST_ALIAS = "host.containers.internal"


def dry_run_enabled(env: dict[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(DRY_RUN_ENV, "").strip().lower() in ("1", "true", "yes")


def resolve_image(env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    return source.get(IMAGE_ENV) or DEFAULT_IMAGE


def project_hash(project_path: Path) -> str:
    """Stable identifier for a project path, used as a container label value."""
    return hashlib.sha1(str(project_path).encode()).hexdigest()[:12]


_HASH_SUFFIX = 6
# podman itself accepts long names; this cap keeps `ls` output aligned and container names typable.
MAX_NAME = 32


def container_name(project_path: Path) -> str:
    """Derive a container name from a project path.

    The hash suffix keeps two same-named projects in different directories apart, so it is never
    the part that gets truncated — the readable stem is. Identity for lookup lives in the labels
    (`factory.project`, `factory.name`), which have no length limit, so a truncated stem costs
    nothing but legibility.
    """
    digest = project_hash(project_path)[:_HASH_SUFFIX]
    stem = "".join(c if c.isalnum() else "-" for c in project_path.name.lower()).strip("-")
    stem = stem[: MAX_NAME - _HASH_SUFFIX - 1].strip("-") or "factory"
    return f"{stem}-{digest}"


@dataclass(frozen=True)
class Mount:
    """One bind mount into the container.

    `target` is a full path, not a parent: unlike an upload, a bind mount lands exactly where it is
    told. Locally `source` and `target` are the same string for the workspace, which is the
    path-preserving property.5 depends on — the local division's builds are executed by an engine
    *outside* the container and resolve their context path in the host engine's namespace.
    """

    source: Path
    target: str
    read_only: bool = False

    def as_flag(self) -> str:
        suffix = ":ro" if self.read_only else ":rw"
        return f"{self.source}:{self.target}{suffix}"


@dataclass(frozen=True)
class ContainerPlan:
    """Everything needed to provision one container, in the order it must happen."""

    name: str
    image: str
    workdir: str
    env: dict[str, str]
    labels: dict[str, str]
    mounts: tuple[Mount, ...]
    # `run_command` is the whole shell line the container runs; `factory_command` is just the
    # `factory ...` invocation inside it. Both are stored because the division re-composes the
    # former from the latter — folding in an MCP registration and the division brief — and
    # re-deriving one by string-surgery on the other is how the two drift apart.
    run_command: str
    factory_command: str = ""
    user: str | None = None
    userns: str | None = None
    network_aliases: tuple[str, ...] = field(default=())
    warnings: tuple[str, ...] = field(default=())


def build_create_argv(plan: ContainerPlan) -> list[str]:
    """Compose the `podman run -d` that creates the container.

    Everything the run needs is supplied here: mounts, environment, labels, identity. The container
    starts detached and returns its identifier immediately.
    """
    cmd = ["podman", "run", "-d", "--init", "--name", plan.name]
    for key, value in sorted(plan.labels.items()):
        cmd += ["--label", f"{key}={value}"]
    for key, value in sorted(plan.env.items()):
        cmd += ["--env", f"{key}={value}"]
    for mount in plan.mounts:
        cmd += ["-v", mount.as_flag()]
    if plan.userns:
        cmd += [f"--userns={plan.userns}"]
    if plan.user:
        cmd += ["--user", plan.user]
    cmd += ["--workdir", plan.workdir]
    cmd += [plan.image, "sh", "-lc", IDLE_COMMAND]
    return cmd


def build_exec_argv(
    name: str, argv: list[str], *, tty: bool = False, detach: bool = False
) -> list[str]:
    """Compose `podman exec` for an arbitrary command inside a running container.

    TTY allocation is stated explicitly rather than auto-detected, because the factory runs this
    both from a terminal (attach) and from a pipe (provisioning), and auto-detection would quietly
    do the wrong thing in whichever case the caller forgot about.
    """
    cmd = ["podman", "exec"]
    if detach:
        cmd.append("-d")
    if tty:
        cmd += ["-i", "-t"]
    cmd += [name, *argv]
    return cmd


def build_attach_argv(name: str, *, session: str = TMUX_SESSION) -> list[str]:
    """Compose the reattach.

    tmux has no network protocol — its client-server link is a Unix socket — so an `exec` with a
    TTY is the transport. The multiplexer is what makes detaching safe: without it, `podman attach`
    is the only route to the running process's stdio and `Ctrl-C` sends SIGINT to the factory.

    Revives a dead pane before attaching. The session is created with `remain-on-exit`, so a run
    that has finished — or a shell the user typed `exit` into — leaves the pane dead rather than
    destroying the session. Attaching to a dead pane would show a frozen screen and accept no
    input, so it is respawned into a shell first, which keeps the scrollback and gives the user
    somewhere to type.
    """
    revive = (
        f'if [ "$(tmux list-panes -t {shlex.quote(session)} -F "#{{pane_dead}}" 2>/dev/null '
        f'| head -1)" = "1" ]; then tmux respawn-pane -t {shlex.quote(session)} "exec sh -i"; fi; '
        f"exec tmux attach -t {shlex.quote(session)}"
    )
    return build_exec_argv(name, ["sh", "-lc", revive], tty=True)


def build_pane_liveness_argv(name: str, *, session: str = TMUX_SESSION) -> list[str]:
    """Ask whether anything in the run's session is still alive.

    Session *existence* is the wrong question: the session is deliberately kept after the run ends
    so its output stays readable, so asking `has-session` reports a finished run as running. What
    distinguishes them is whether any pane still has a live process — `#{pane_dead}` is `0` for one
    that does.
    """
    return build_exec_argv(name, ["tmux", "list-panes", "-t", session, "-F", "#{pane_dead}"])


def build_start_argv(plan: ContainerPlan) -> list[str]:
    """Compose the exec that starts the detached tmux session holding the run."""
    return build_exec_argv(
        plan.name, ["sh", "-lc", build_tmux_launch(plan.workdir, plan.run_command)]
    )


def build_rm_argv(name: str, *, force: bool = True) -> list[str]:
    cmd = ["podman", "rm"]
    if force:
        cmd.append("--force")
    cmd.append(name)
    return cmd


def build_ps_argv(*, all_states: bool = True) -> list[str]:
    """List every container the factory created — and nothing else.

    A tool that shows a user resources it did not create invites them to assume it manages those
    too, so the filter is the factory's own label rather than a bare `podman ps`.
    """
    cmd = ["podman", "ps"]
    if all_states:
        cmd.append("--all")
    cmd += ["--filter", f"label={LABEL_CONTAINED}=true", "--format", "json"]
    return cmd


def build_image_exists_argv(reference: str) -> list[str]:
    return ["podman", "image", "exists", reference]


def build_pull_argv(reference: str) -> list[str]:
    return ["podman", "pull", reference]


def build_info_argv() -> list[str]:
    """Exercise the connection, not merely the binary.

    On macOS the machine stops quietly and `podman machine start` is required after a reboot, so a
    check that only finds the binary reports a healthy setup for a machine that is down.
    """
    return ["podman", "info", "--format", "json"]


def build_stat_argv(image: str, mount: Mount, *, user: str | None = None) -> list[str]:
    """Compose a throwaway container that reports a mount's ownership as the container sees it.

    .2 refuses to encode an identity rule that is wrong for one of rootless / rootful / macOS.
     This is the measurement that replaces the rule: mount the path, ask the kernel inside the
     container who owns it, and match the run's identity to the answer.
    """
    cmd = ["podman", "run", "--rm", "-v", mount.as_flag()]
    if user:
        cmd += ["--user", user]
    cmd += [image, "stat", "-c", "%u:%g", mount.target]
    return cmd


def build_tmux_launch(workdir: str, command: str, *, session: str = TMUX_SESSION) -> str:
    """Compose the detached tmux session that holds the run.

    Detached, so the exec that starts it returns as soon as the session exists and the caller can
    print the identifier instead of blocking on the whole cycle. The trailing interactive shell
    keeps the session alive after the factory exits, which is what makes a *failed* run
    inspectable — the case where its state is most worth reading.
    """
    inner = f"{command}; printf '\\n[factory exited %s]\\n' \"$?\"; exec sh -i"
    # `remain-on-exit` is what stops one stray Ctrl-D from destroying the run's session for good.
    # Without it, exiting the shell closes the last pane, which closes the window, which ends the
    # session and takes the entire scrollback with it — leaving a container that `ls` still calls
    # running and an `attach` that answers "no sessions" with no way back.
    quoted = shlex.quote(session)
    return (
        f"tmux new-session -d -s {quoted} -c {shlex.quote(workdir)} {shlex.quote(inner)}; "
        # `remain-on-exit` is what stops one stray Ctrl-D from destroying the session for good, and
        # the hook is what stops that from stranding whoever pressed it: without it the client stays
        # attached to a pane that is dead and accepts no input, so the only way out is to know the
        # tmux detach key. Together: the session and its scrollback survive, and exiting returns you
        # to your own shell.
        f"tmux set-option -t {quoted} remain-on-exit on; "
        f"tmux set-hook -t {quoted} pane-died detach-client"
    )


def build_run_command(
    workdir: str,
    factory_argv: str,
    *,
    mcp_config: dict[str, object] | None = None,
    files: dict[str, str] | None = None,
) -> str:
    """Compose the shell command the container runs.

    The MCP registration and any division files are written inside the container because they
    belong next to the project, whose location inside is known only here.

    The first thing it does is pre-answer Claude Code's trust and MCP-approval prompts for this
    workspace (`factory.contained.claude_state`). They are interactive-only, and a contained run has
    a real terminal that nobody is watching — so unanswered they read as a hang, after the tokens it
    took to reach them have already been spent.
    """
    import json

    from factory.contained.claude_state import render_seed_command

    raw_servers = (mcp_config or {}).get("mcpServers", {})
    servers: tuple[str, ...] = tuple(raw_servers) if isinstance(raw_servers, dict) else ()
    parts: list[str] = [
        render_seed_command(workdir, servers),
        f"cd {shlex.quote(workdir)}",
    ]
    if mcp_config is not None:
        payload = shlex.quote(json.dumps(mcp_config, sort_keys=True))
        parts.append(f"printf '%s' {payload} > .mcp.json")
    for relative, content in sorted((files or {}).items()):
        directory = str(Path(relative).parent)
        if directory not in (".", ""):
            parts.append(f"mkdir -p {shlex.quote(directory)}")
        parts.append(f"printf '%s' {shlex.quote(content)} > {shlex.quote(relative)}")
    parts.append(factory_argv)
    return " && ".join(parts)


# Payloads that can produce an eval score. Warning about score comparability ahead of `backlog-list`
# or `ls` trains the user to skip warnings, which costs them the one that matters.
SCORING_COMMANDS = frozenset({"ceo", "run", "eval", "improve", "workflow", "refactory", "baseline"})


def scores_something(factory_args: list[str]) -> bool:
    """Whether this payload could produce an eval score.

    Looks only at the first non-flag word — the subcommand. The host does not otherwise interpret
    the payload, and it does not need to here either.
    """
    for token in factory_args:
        if token.startswith("-"):
            continue
        return token in SCORING_COMMANDS
    return False


def growth_context_warning(
    env: dict[str, str] | None = None, factory_args: list[str] | None = None
) -> str | None:
    """Warn that in-container scores will not be comparable — but only when scores are involved.

    Never an error. A container without this context still runs; its eval scores are simply not
    comparable to host scores, and the operator needs to know that before comparing them.
    """
    if factory_args is not None and not scores_something(factory_args):
        return None
    source = os.environ if env is None else env
    missing = [name for name in GROWTH_CONTEXT_VARS if not source.get(name, "").strip()]
    if not missing:
        return None
    return (
        "Eval scores from this run will not be comparable to scores computed on this machine: "
        f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set, and those directories "
        "feed part of the score. Set them and pass them with --forward to make the numbers "
        "comparable, or ignore this if you are not comparing scores."
    )


@dataclass(frozen=True)
class Step:
    """One provisioning command, named so a failure can say which stage broke."""

    name: str
    argv: list[str]


def plan_steps(plan: ContainerPlan, probes: list[Probe] | None = None) -> list[Step]:
    """The full provisioning sequence as ordered, named steps.

    This is what dry-run prints and what the real path executes, so the two cannot drift — a
    dry-run that renders a command the real path does not run is worse than no dry-run at all.
    """
    steps = [Step("create", build_create_argv(plan))]
    for probe in probes or []:
        steps.append(Step(f"assert:{probe.name}", build_exec_argv(plan.name, probe.argv)))
    steps.append(Step("run", build_start_argv(plan)))
    return steps
