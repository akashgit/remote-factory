"""The local runtime path: one podman container on this machine.

The peer of `factory/cli/contained_k8s.py`, which does the same for a cluster pod. `contained.py`
registers the parser and decides which of the two a command lands in; neither of them knows about
the other, and both take the interpreted `argparse.Namespace` and nothing else.

Everything here is about *one run*: compose its plan, assert its provenance, execute the steps, and
undo the workspace when the launch never got far enough for the workspace to be worth keeping.
"""

from __future__ import annotations

import argparse
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import structlog

from factory.cli.contained_args import resolve_project, validate_env_args
from factory.contained.credentials import resolve_credentials, vertex_model_warning
from factory.contained.env import CONTAINED_ENV_POLICY, redact_argv
from factory.contained.errors import ContainedError
from factory.contained.identity import IdentityError, resolve_identity
from factory.contained.lifecycle import reap_stale
from factory.contained.paths import rewrite_argv
from factory.contained.provenance import Probe, content_probe, provenance_probes
from factory.contained.workspace import (
    Workspace,
    WorkspaceError,
    git_common_dir,
    materialize,
    plan_workspace,
)
from factory.podman import (
    CONTAINER_HOME,
    DRY_RUN_ENV,
    LABEL_CONTAINED,
    LABEL_NAME,
    LABEL_PROJECT,
    LABEL_SOURCE,
    ContainerPlan,
    Mount,
    Step,
    build_run_command,
    container_name,
    dry_run_enabled,
    growth_context_warning,
    plan_steps,
    project_hash,
    resolve_image,
)

log = structlog.get_logger()


def _macos_share_warning(mounts: list[Mount]) -> str | None:
    """On macOS a path the podman machine does not share is not mounted at all.

    It does not fail at `podman run` — the mount is simply absent, which surfaces as an empty
    directory inside. Checked against the machine's *actual* shared paths rather than against
    `$HOME`: the user may have added their own with `podman machine set --volume`, and warning
    about a path that in fact works teaches them to ignore the warning.
    """
    if platform.system() != "Darwin":
        return None
    shared = _machine_shared_paths()
    if not shared:
        return None
    outside = [
        str(m.source) for m in mounts
        if not any(root == m.source or root in m.source.parents for root in shared)
    ]
    if not outside:
        return None
    roots = ", ".join(str(r) for r in shared)
    return (
        f"{', '.join(outside)} is not a path the podman machine shares (it shares: {roots}), so it "
        "will be empty inside the container. Move the project under one of those paths, or add "
        "this one with `podman machine set --volume` and restart the machine."
    )


def _machine_shared_paths() -> list[Path]:
    """The host paths the podman machine actually shares, or [] when that cannot be determined."""
    try:
        result = subprocess.run(
            ["podman", "machine", "inspect", "--format", "{{range .Mounts}}{{.Source}}\n{{end}}"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    paths = [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    return [p for p in paths if p.is_absolute()]


def _compose_env(
    shape_env: dict[str, str], forwarded: dict[str, str], extra: dict[str, str]
) -> dict[str, str]:
    """`FACTORY_` by default, plus the backend variables, plus exactly what `--forward` names.

    Nothing implicit. `--env` is applied last because it is the documented escape hatch for backend
    quirks, and an escape hatch that loses to a computed default is not one.
    """
    env = CONTAINED_ENV_POLICY.resolve(dict(os.environ))
    env["HOME"] = CONTAINER_HOME
    env.update(shape_env)
    env.update(forwarded)
    env.update(extra)
    return env


def _build_plan(args: argparse.Namespace, ws: Workspace, *, dry_run: bool) -> ContainerPlan:
    """Compose the provisioning plan for one local run.

    The project is a bind mount, not an upload, so the plan carries no project transfer and none of
    the `.gitignore` handling a transfer needs. What replaces it is the provenance probe list
   : a mount can be present, empty, stale, or read-only, and all four look identical until
    something is asserted.
    """
    warnings: list[str] = []
    image = args.image or resolve_image()

    extra_env, forwarded = validate_env_args(args)

    # The workspace is mounted at its own absolute path — identical inside and out. Not cosmetic:
    # the local division's builds are executed by an engine *outside* the container, which
    # resolves the build-context path in its own filesystem namespace.
    workspace_mount = Mount(source=ws.path, target=str(ws.path))
    mounts: list[Mount] = [workspace_mount]

    factory_home = Path("~/.factory").expanduser()
    if factory_home.is_dir():
        # Read-write: config, credential profiles, the registry and ACE-evolved playbooks work as on
        # the host and keep accumulating.
        mounts.append(Mount(factory_home, f"{CONTAINER_HOME}/.factory"))

    if ws.kind == "worktree":
        # A worktree's .git is a *file* pointing at the original repository's object store. Without
        # that store mounted, every git command inside fails on a path that exists on the host and
        # not in the container — and the `git_usable` probe is what catches it.
        #
        # **Read-write, and the design said read-only.** Correcting.2 with what running it
        # showed: the CEO creates its own experiment worktrees at `<project>/.factory-worktrees/`
        #, and `git worktree add` writes into the *common* dir — a ref lock, a worktree
        # registration, objects. Read-only, the first cycle dies on
        # "cannot lock ref ...: Read-only file system", which reads as a git bug rather than a mount
        # mode. Nothing else in the design works around it: the copy has to be a valid worktree
        # parent, and a valid worktree parent has a writable common dir.
        #
        # The cost, stated rather than buried: the container can write the source repository's git
        # directory. "The host tree is untouched" remains true — that is a statement about the
        # *working* tree — and the object store was already shared by construction, which is what
        # makes the worktree cheap and what puts the run's branch where `sync`'s merge command can
        # find it. But the blast radius is the copy *plus* the source repo's `.git`, not the copy
        # alone.
        common = git_common_dir(ws.source)
        if common is not None:
            mounts.append(Mount(common, str(common)))

    shape = resolve_credentials()
    for host_path, relative in shape.home_mounts:
        mounts.append(Mount(host_path, f"{CONTAINER_HOME}/{relative}", read_only=True))
    warnings.extend(shape.warnings)
    if not shape.ok:
        warnings.append(
            "no inference credentials are configured, so every agent call in this run will fail.\n"
            "  Set one of these before running, and pass it inward:\n"
            "    export ANTHROPIC_API_KEY=...   then add:  --forward ANTHROPIC_API_KEY\n"
            "  Run `factory contained verify` to check."
        )
    model_warning = vertex_model_warning(shape, args.factory_args)
    if model_warning:
        warnings.append(model_warning)

    for extra in args.mount:
        resolved = Path(extra).expanduser().resolve()
        if not resolved.exists():
            raise ContainedError(f"--mount {extra}: no such path on this machine")
        mounts.append(Mount(resolved, str(resolved)))

    share_warning = _macos_share_warning(mounts)
    if share_warning:
        warnings.append(share_warning)

    identity = resolve_identity(image, workspace_mount, dry_run=dry_run)
    log.debug("contained_identity", detail=identity.detail)

    factory_argv, changes = rewrite_argv(args.factory_args, ws.source, ws.path)
    for before, after in changes:
        # The rewrite rule is generic — any payload token that resolves to an existing in-project
        # path gets translated, including a free-text value that coincidentally names one. Logging
        # every rewrite keeps that visible instead of silent.
        log.debug("contained_path_rewritten", before=before, after=after)

    inner = "factory " + " ".join(shlex.quote(token) for token in factory_argv)
    name = args.name or container_name(ws.source)
    return ContainerPlan(
        name=name,
        image=image,
        workdir=str(ws.path),
        env=_compose_env(shape.env, forwarded, extra_env),
        labels={
            LABEL_CONTAINED: "true",
            LABEL_PROJECT: project_hash(ws.source),
            LABEL_NAME: name,
            LABEL_SOURCE: str(ws.source),
        },
        mounts=tuple(mounts),
        run_command=build_run_command(str(ws.path), inner),
        factory_command=inner,
        user=identity.user,
        userns=identity.userns,
        warnings=tuple(warnings),
    )


def _emit_dry_run(plan: ContainerPlan, steps: list[Step]) -> int:
    """Print the exact commands the real path would run, then provision nothing.

    `steps` is the same list `cmd_contained` executes step-by-step — rendering a separately composed
    command list here is exactly the drift a dry-run contract exists to forbid.
    """
    print(f"DRY RUN — {plan.name} ({plan.image}); nothing is provisioned.")
    for step in steps:
        print(f"[{step.name}] {shlex.join(redact_argv(step.argv, CONTAINED_ENV_POLICY))}")
    return 0


_NAME_TAKEN_MARKERS = ("already in use", "already exists")


def _handle_create_failure(
    step: Step, result: subprocess.CompletedProcess[str], plan: ContainerPlan
) -> tuple[subprocess.CompletedProcess[str], str | None]:
    """When `podman run` fails on a name collision, try to clear it and retry once.

    A failed run that leaves its container behind otherwise blocks every later invocation of the
    same name behind a bare "name already in use", with nothing pointing at how to get unstuck.
    `reap_stale` only ever removes a container this factory created and that is no longer running;
    anything it declines to touch falls through to an actionable message instead of a silent retry,
    since a name collision could equally mean "you meant to reattach".
    """
    if step.name != "create" or not any(m in result.stderr.lower() for m in _NAME_TAKEN_MARKERS):
        return result, None
    reaped, detail = reap_stale(plan.name)
    if reaped:
        log.debug("contained_create_retry_after_reap", name=plan.name, detail=detail)
        result = _run_step(step)
        if result.returncode == 0:
            return result, None
    hint = (
        f"container {plan.name!r} already exists ({detail}). Attach to it with `factory contained "
        f"attach {plan.name}`, remove it with `factory contained rm {plan.name}`, or pass --name to "
        "provision under a different name."
    )
    return result, hint


def _run_step(step: Step) -> subprocess.CompletedProcess[str]:
    log.debug("contained_step", step=step.name, argv=redact_argv(step.argv, CONTAINED_ENV_POLICY))
    timeout = 300 if step.name == "create" else 120
    return subprocess.run(step.argv, capture_output=True, text=True, timeout=timeout, check=False)


def _roll_back(ws: Workspace | None) -> None:
    """Undo a workspace this launch created, when the launch never got as far as running anything.

    Only ever called on the failure path, and only for a copy this invocation made: a reattach to an
    existing run reuses its workspace, and removing that would destroy live work.
    """
    if ws is None or not ws.path.exists():
        return
    from factory.contained.workspace import release

    try:
        release(ws, delete_branch=True)
        # `release` removes the copy; the run directory that held it is now empty and is ours.
        run_dir = ws.path.parent
        if run_dir.is_dir() and not any(run_dir.iterdir()):
            run_dir.rmdir()
    except (WorkspaceError, OSError) as exc:
        # Report rather than mask the original failure, and say exactly what is left over.
        from factory.contained.workspace import cleanup_hint

        print(f"Note: could not clean up the workspace ({exc}).\n{cleanup_hint(ws)}",
              file=sys.stderr)


def run_local(args: argparse.Namespace) -> int:
    dry_run = dry_run_enabled()
    # Bound before the first `try` because the `finally` below has to be able to shut the division
    # down no matter which step raised — including one that raised before it was ever started.
    division = None
    ws: Workspace | None = None
    try:
        project = resolve_project(args.factory_args)
        validate_env_args(args)          # before a copy is made, not after
        run_id = args.name or container_name(project)
        # Dry-run must not touch the host: no worktree, no branch, no rsync. `plan_workspace`
        # computes the same path/kind/branch `materialize` would, purely from path and git-repo
        # detection, without any of `materialize`'s side effects.
        ws = plan_workspace(project, run_id) if dry_run else materialize(project, run_id)
        plan = _build_plan(args, ws, dry_run=dry_run)
        if args.division:
            from factory.contained.division import start_local_division

            division = start_local_division(plan, dry_run=dry_run)
            plan = division.plan
    except (ContainedError, WorkspaceError, IdentityError) as exc:
        # A half-materialized run is worse than none: reporting and stopping here means the next
        # attempt starts clean instead of layering on top of a plan already known bad. That includes
        # the worktree and branch this just added to the *user's* repository — the factory started
        # nothing, so there is no work to lose, and leaving them behind means the user's own
        # `git worktree list` grows by one on every failed attempt.
        _roll_back(ws)
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        probes = _probes_for(ws, project, dry_run=dry_run)
        steps = plan_steps(plan, probes)

        # Warnings go to stderr and never change the exit code. Ordered least to most consequential
        # so the one that will actually break the run is the last thing on screen.
        for warning in (growth_context_warning(factory_args=args.factory_args), *plan.warnings):
            if warning:
                print(f"Warning: {warning}", file=sys.stderr)

        if dry_run:
            return _emit_dry_run(plan, steps)

        if shutil.which("podman") is None:
            print(
                "Error: `podman` is not installed. Run `factory contained setup`, or set "
                f"{DRY_RUN_ENV}=1 to compose the commands without running them.",
                file=sys.stderr,
            )
            _roll_back(ws)
            return 1

        from factory.contained.usage import record_target

        record_target("local")
        _announce(plan)
        code, created = _execute(plan, steps, probes)
        _settle_workspace(ws, code=code, created=created)
        if division is not None and code == 0:
            # The run outlives this command, so the endpoint it depends on has to as well. `rm`
            # stops it; the `finally` below only fires for a launch that never got that far.
            division.keep()
            division = None
        return code
    finally:
        if division is not None:
            division.stop()


def _probes_for(ws: Workspace, project: Path, *, dry_run: bool) -> list[Probe]:
    """The assertions that run between provisioning and the first agent call.

    A mount can be present, empty, stale, or read-only, and all four look identical until something
    is asserted — which is why these exist at all and why a failure leaves the runtime up.
    """
    if dry_run:
        # ws.path does not exist yet — nothing was materialized — so there is nothing there to
        # read. The source project always exists, so the content_hash probe is composed from it
        # instead: same argv shape (still checked against ws.path, the eventual runtime
        # destination), a real digest, but of a projection rather than a measurement.
        content = content_probe(ws.source)
        if content is not None:
            print(
                "Note: the content_hash probe below is a projection from the source tree — "
                f"dry-run does not create the copy at {ws.path} it would eventually check "
                "against.",
                file=sys.stderr,
            )
    else:
        content = content_probe(ws.path)

    return provenance_probes(
        str(ws.path),
        expect_factory_state=(project / ".factory" / "config.json").exists(),
        expect_git=(project / ".git").exists(),
        content=content,
    )


def _settle_workspace(ws: Workspace, *, code: int, created: bool) -> None:
    """What becomes of the copy once the steps have run: kept for inspection, or removed."""
    if code == 0:
        return
    if not created:
        # Nothing was provisioned, so the workspace this launch made has no purpose and no
        # contents worth keeping. When a container *was* created the workspace stays: it is what
        # the user inspects.
        _roll_back(ws)
        return
    from factory.contained.workspace import cleanup_hint

    print(f"\n{cleanup_hint(ws)}", file=sys.stderr)


def _announce(plan: ContainerPlan) -> None:
    """Print the run's identifier before provisioning starts.

    It is knowable as soon as the plan exists, and it is the one line a user needs to keep: without
    it they cannot attach to, sync, or remove the run they just started.
    """
    print(f"Starting {plan.name}")
    print(f"  attach:  factory contained attach {plan.name}")
    print(f"  result:  factory contained sync {plan.name}")
    print(f"  stop:    factory contained rm {plan.name}")
    print()


def _execute(plan: ContainerPlan, steps: list[Step], probes: list[Probe]) -> tuple[int, bool]:
    """Run the provisioning steps. Returns the exit code and whether a container now exists.

    The caller needs the second value to decide whether the workspace is still worth keeping: a
    failure before the container exists leaves nothing to inspect, and the copy it made is litter in
    the user's repository.
    """
    hints = {f"assert:{p.name}": p.hint for p in probes}
    created = False
    for step in steps:
        try:
            result = _run_step(step)
        except KeyboardInterrupt:
            print(
                f"\nInterrupted. The container {plan.name} may still be running the factory — the "
                "interrupt reached this client, not the container. Stop it with:\n"
                f"  podman stop {plan.name}",
                file=sys.stderr,
            )
            return 130, created
        create_hint = None
        if result.returncode != 0:
            result, create_hint = _handle_create_failure(step, result, plan)
        if result.returncode != 0:
            hint = create_hint or hints.get(step.name, result.stderr.strip())
            print(f"contained: step '{step.name}' failed\n  {hint}", file=sys.stderr)
            if created:
                # The container survives a failed assertion on purpose: it is the only way to look
                # at what actually landed in the mount.
                print(
                    f"  The container is still there for inspection:\n"
                    f"    podman exec -it {plan.name} sh\n"
                    f"    factory contained rm {plan.name}",
                    file=sys.stderr,
                )
            return 1, created
        if step.name == "create":
            created = True

    print(f"{plan.name} is running.")
    return 0, created
