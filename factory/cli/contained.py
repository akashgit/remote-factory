"""`factory contained` — run any factory command inside a podman container or a cluster pod.

The runtime is a place to run the factory, not a mode of the factory: everything after `--` is
handed inward verbatim, except for path rewriting.
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

from factory.contained.credentials import resolve_credentials, vertex_model_warning
from factory.contained.env import CONTAINED_ENV_POLICY, redact_argv
from factory.contained.errors import ContainedError
from factory.contained.identity import IdentityError, resolve_identity
from factory.contained.lifecycle import dispatch_lifecycle, reap_stale
from factory.contained.paths import rewrite_argv
from factory.contained.prereq import local_checks, render_checks
from factory.contained.provenance import Probe, content_probe, provenance_probes
from factory.contained.setup import run_setup
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


LIFECYCLE_SUBCOMMANDS = ("ls", "attach", "rm", "sync", "setup", "verify", "bundle")

# Flags whose meaning exists only for one runtime. Using one against the other is a mistake worth
# naming: silently ignoring it makes a user believe a namespace or a mount took effect.
_LOCAL_ONLY = ("mount",)
_K8S_ONLY = ("namespace", "storage_class")

# Flags are described here rather than in argparse's own listing: which target a flag belongs to is
# the thing a user most needs to know, and a flat alphabetical list hides it.
_HELP_EPILOG = """\
Run any factory command against a pinned toolchain and a copy of your project, so your
working tree is untouched. Everything after `--` is passed through unchanged.

  factory contained -- ceo ~/code/my-project

Targets:
  local   a podman container on this machine (the default). Fastest to start.
  k8s     a pod on a Kubernetes/OpenShift cluster. For long, unattended runs.

Subcommands:
  setup                  Install what is missing, then check it
  verify                 Check prerequisites; report the fix for each failure
  ls                     List the runtimes this tool created
  attach NAME            Watch a running run (Ctrl-b d detaches; the run continues)
  sync NAME              Show how to get the run's work back
  rm NAME                Delete a runtime
  bundle                 Print the cluster prerequisites as YAML (k8s)

Both targets:
  --target local|k8s     Which runtime                              (default: local)
  --division             Let the agent build container images
  --name NAME            Name this run                              (default: derived)
  --env KEY=VALUE        Extra environment for the run, repeatable
  --forward VAR          Pass a variable from your shell inward, repeatable
  --image REF            Use a different runtime image
  --yes                  Skip confirmation prompts

Local only:
  --mount PATH           Also mount this host path, repeatable

K8s only:
  --namespace NS         Namespace                    (default: your current context)
  --storage-class SC     Storage class for the workspace volume

Environment:
  FACTORY_CONTAINED_IMAGE          Runtime image to use
  FACTORY_CONTAINED_HOME           Where workspace copies live (default ~/.factory-contained)
  FACTORY_CONTAINED_DRY_RUN=1      Print what would run; provision nothing

`contained` gives a run a reproducible environment and keeps it off your working tree.
It is not a security sandbox: it does not restrict what the agent's code can do, and it
does not replace reviewing the result. `--division` additionally opens an unauthenticated
build endpoint on this machine for the length of the run.

Full guide: https://akashgit.github.io/remote-factory/contained/
"""

# Set by `build_contained_parser`, read by `cmd_contained`. `interpret` needs the parser itself (to
# call `.error()` on) and the namespace has no room for it: `set_defaults` would put every key into
# `--help` output and into every namespace repr, which is noise in exactly the place a user is
# trying to read.
_PARSER: argparse.ArgumentParser | None = None


def build_contained_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the `contained` subcommand.

    The payload after `--` is `argparse.REMAINDER`: it is handed to the factory inside the runtime
    verbatim. Validating it here would mean the host has to know every subcommand the contained
    factory supports, which it cannot — and a passthrough that second-guesses its payload breaks
    every time the CLI grows.
    """
    global _PARSER
    p = sub.add_parser(
        "contained",
        help="Run any factory command in a container (local) or a pod (k8s)",
        usage="factory contained [runtime flags] -- <factory command>\n"
              "       factory contained {ls|attach|rm|sync|setup|verify|bundle} [name]",
        epilog=_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        # `--name` and `--namespace` share a prefix. Without this, argparse's default prefix
        # matching lets `--name` silently resolve to `--namespace` (or any future flag that happens
        # to share a prefix with another), which is exactly the kind of flag-aliasing this parser
        # has to name loudly rather than let happen quietly.
        allow_abbrev=False,
    )
    # One REMAINDER for everything positional, split afterwards by `interpret`. A declarative split
    # is not expressible: an optional positional carrying `choices` would try to match the first
    # word of the payload and reject it as an invalid choice.
    # Every flag is SUPPRESSed from argparse's own listing and described in the epilog instead:
    # a flat list hides which target each flag belongs to, and printing both lists each flag twice.
    p.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    p.add_argument("--target", choices=["local", "k8s"], default="local", help=argparse.SUPPRESS)
    p.add_argument("--division", action="store_true", default=False, help=argparse.SUPPRESS)
    p.add_argument("--name", default=None, help=argparse.SUPPRESS)
    p.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", dest="extra_env",
                   help=argparse.SUPPRESS)
    p.add_argument("--forward", action="append", default=[], metavar="VAR", help=argparse.SUPPRESS)
    p.add_argument("--mount", action="append", default=[], metavar="PATH", help=argparse.SUPPRESS)
    p.add_argument("--namespace", default=None, help=argparse.SUPPRESS)
    p.add_argument("--storage-class", default=None, dest="storage_class", help=argparse.SUPPRESS)
    p.add_argument("--image", default=None, help=argparse.SUPPRESS)
    # `rm` prompts before deleting an active runtime and the cluster upload prompts on a secret-scan
    # finding; `--yes` skips both, for automation.
    p.add_argument("--yes", action="store_true", default=False, help=argparse.SUPPRESS)
    _PARSER = p
    return p


def interpret(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Split the positional remainder and check flag scoping. Call once, before anything else.

    argparse offers no post-parse hook, so this is invoked explicitly — by `cmd_contained`, and by
    the tests, which must exercise the same interpretation the CLI performs.

    Sets `args.subcommand` and `args.factory_args` always; `args.name` only when a lifecycle
    positional supplies one. `--name` is parsed onto `args.name` before this runs, and the
    verbatim-payload branches must leave it alone — otherwise a run like
    `contained --name foo -- study /p` would have its explicit name overwritten with None here.
    """
    rest = list(args.rest)
    if rest and rest[0] == "--":          # argparse leaves the separator inside a REMAINDER
        args.subcommand, args.factory_args = None, rest[1:]
    elif rest and rest[0] in LIFECYCLE_SUBCOMMANDS:
        args.subcommand = rest[0]
        args.factory_args = []
        tail = rest[1:]
        # `--yes` is the one trailing flag accepted here, because `rm <name> --yes` is the order
        # people type it. It is documented as the exception; every other flag in this position is
        # rejected below rather than silently dropped.
        if "--yes" in tail:
            args.yes = True
            tail = [token for token in tail if token != "--yes"]
        # Everything else that looks like a flag here is a mistake worth naming, not swallowing.
        # The REMAINDER split means `--target k8s` typed *after* the subcommand never reaches
        # `args.target` — it lands here as a plain string instead, so a silent absorption would
        # leave `args.target` at its default ("local") while the user believes they asked for k8s,
        # and would hand a lifecycle command a name like "--target" to resolve.
        flag_like = [token for token in tail if token.startswith("-")]
        if flag_like:
            parser.error(
                f"unrecognized flag {flag_like[0]!r} after `factory contained "
                f"{args.subcommand}`. Runtime flags (--target, --namespace, --name, ...) go before "
                f"the subcommand, for example:\n"
                f"  factory contained --target k8s {args.subcommand}"
            )
        # Only the positional overrides `--name` here, and only when one was actually given —
        # `ls` takes no name.
        if tail:
            args.name = tail[0]
    else:
        args.subcommand, args.factory_args = None, rest
        _reject_subcommand_typo(parser, rest)

    # `bundle` only ever emits cluster YAML, so it implies the cluster target. Without this the
    # namespace flag it needs is rejected as out-of-scope for the default target, and the command
    # the generated manifest tells you to run cannot be run.
    if args.subcommand == "bundle":
        args.target = "k8s"

    for dest in _LOCAL_ONLY:
        if getattr(args, dest) and args.target != "local":
            parser.error(f"--{dest.replace('_', '-')} only applies to --target local")
    for dest in _K8S_ONLY:
        if getattr(args, dest) and args.target != "k8s":
            parser.error(f"--{dest.replace('_', '-')} only applies to --target k8s")

    if args.subcommand in ("attach", "rm", "sync") and not args.name:
        parser.error(f"`factory contained {args.subcommand}` needs a runtime name. Try `ls`.")
    if not args.subcommand and not args.factory_args:
        parser.error(
            "`factory contained` expects a factory command after `--`, for example:\n"
            "  factory contained -- ceo ~/code/rta\n"
            "  factory contained --division -- study ~/code/rta"
        )


def _reject_subcommand_typo(parser: argparse.ArgumentParser, rest: list[str]) -> None:
    """Catch `lst` for `ls` before it is treated as a factory command.

    Without this the token falls through to the passthrough path and fails much later with "no
    existing directory found in ['lst']" — a message about materializing workspaces, for what is
    simply a typo.
    """
    if not rest:
        return
    first = rest[0]
    if first.startswith("-") or Path(first).expanduser().exists():
        return
    close = [c for c in LIFECYCLE_SUBCOMMANDS if _within_one_edit(first, c)]
    if close:
        parser.error(
            f"unknown subcommand {first!r} — did you mean {close[0]!r}?\n"
            f"  factory contained {close[0]}"
        )


def _within_one_edit(a: str, b: str) -> bool:
    """A cheap edit-distance-1 check: one substitution, insertion, or deletion."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    for index in range(len(longer)):
        if shorter == longer[:index] + longer[index + 1:]:
            return True
    return False


def _target_given(args: argparse.Namespace) -> bool:
    """Whether the user actually typed `--target`, not just landed on its default.

    `--target` defaults to `"local"` (never `None`), so the parsed value alone cannot tell "the user
    asked for local" from "the user didn't say" — and only the second case should trigger
    `run_setup`'s interactive question. Recognizes both the space form (`--target local`) and the
    equals form; an explicit `--target=local` must not be mistaken for "didn't say".
    """
    return any(token == "--target" or token.startswith("--target=") for token in sys.argv)


def _parse_extra_env(pairs: list[str]) -> dict[str, str]:
    """Parse repeated `--env KEY=VALUE` into a mapping, rejecting anything malformed."""
    parsed: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise ContainedError(
                f"--env {pair!r} is not KEY=VALUE. Each --env takes one variable, and the value may "
                "be empty but the '=' may not be omitted."
            )
        parsed[key.strip()] = value
    return parsed


def _resolve_project(factory_args: list[str]) -> Path:
    """The first existing directory named in the payload — the project a run works on.

    Everything after `--` is opaque to the host: it is not parsed as `factory ceo`'s own
    flags, so the one thing that can safely be assumed is that a contained run always starts from a
    project already on this machine, somewhere in that payload.
    """
    for token in factory_args:
        candidate = Path(token).expanduser()
        if candidate.is_dir():
            resolved = candidate.resolve()
            # The rule is generic — the first existing directory anywhere in the payload — so a
            # free-text value that coincidentally names one is picked silently otherwise. Logging it
            # is what keeps that visible.
            log.debug("contained_project_resolved", argument=token, project=str(resolved))
            return resolved
    raise ContainedError(
        f"no existing directory found in {factory_args!r}. `factory contained` materializes a "
        "workspace from a project already on this machine, for example:\n"
        "  factory contained -- ceo ~/code/rta"
    )


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


def _compose_env(args: argparse.Namespace, shape_env: dict[str, str]) -> dict[str, str]:
    """`FACTORY_` by default, plus the backend variables, plus exactly what `--forward` names.

    Nothing implicit. `--env` is applied last because it is the documented escape hatch
    for backend quirks, and an escape hatch that loses to a computed default is not one.
    """
    env = CONTAINED_ENV_POLICY.resolve(dict(os.environ))
    env["HOME"] = CONTAINER_HOME
    env.update(shape_env)
    for name in args.forward:
        value = os.environ.get(name)
        if value is None:
            raise ContainedError(f"--forward {name}: not set in this environment")
        env[name] = value
    env.update(_parse_extra_env(args.extra_env))
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
        env=_compose_env(args, shape.env),
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


def _verify(args: argparse.Namespace) -> int:
    if args.target == "k8s":
        from factory.contained.k8s_setup import verify_k8s

        checks = verify_k8s(namespace=args.namespace, division=args.division)
        print(render_checks(checks, ready_command="factory contained --target k8s -- ceo <path>"))
        return 0 if all(c.ok for c in checks) else 1
    checks = local_checks()
    print(render_checks(checks))
    return 0 if all(c.ok for c in checks) else 1


def cmd_contained(args: argparse.Namespace) -> int:
    """Run the factory inside a container (local) or a pod (k8s)."""
    assert _PARSER is not None, "build_contained_parser must run before cmd_contained"
    interpret(_PARSER, args)

    if args.subcommand == "verify":
        return _verify(args)
    if args.subcommand == "setup":
        return run_setup(
            args.target if _target_given(args) else None,
            interactive=sys.stdin.isatty(),
            namespace=args.namespace,
            division=args.division,
            assume_yes=args.yes,
        )
    if args.subcommand == "bundle":
        from factory.contained.bundle import render_bundle

        print(render_bundle(namespace=args.namespace, storage_class=args.storage_class,
                            division=args.division, image=args.image or resolve_image()))
        return 0
    if args.subcommand:
        return dispatch_lifecycle(args)

    if args.target == "k8s":
        from factory.cli.contained_k8s import run_k8s

        return run_k8s(args)

    return _run_local(args)


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
    except WorkspaceError as exc:
        # Report rather than mask the original failure, and say exactly what is left over.
        from factory.contained.workspace import cleanup_hint

        print(f"Note: could not clean up the workspace ({exc}).\n{cleanup_hint(ws)}",
              file=sys.stderr)


def _run_local(args: argparse.Namespace) -> int:
    dry_run = dry_run_enabled()
    # Bound before the first `try` because the `finally` below has to be able to shut the division
    # down no matter which step raised — including one that raised before it was ever started.
    division = None
    ws: Workspace | None = None
    try:
        project = _resolve_project(args.factory_args)
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

        probes: list[Probe] = provenance_probes(
            str(ws.path),
            expect_factory_state=(project / ".factory" / "config.json").exists(),
            expect_git=(project / ".git").exists(),
            content=content,
        )
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

        _announce(plan)
        code, created = _execute(plan, steps, probes)
        if code != 0 and not created:
            # Nothing was provisioned, so the workspace this launch made has no purpose and no
            # contents worth keeping. When a container *was* created the workspace stays: it is what
            # the user inspects.
            _roll_back(ws)
        elif code != 0:
            from factory.contained.workspace import cleanup_hint

            print(f"\n{cleanup_hint(ws)}", file=sys.stderr)
        if division is not None and code == 0:
            # The run outlives this command, so the endpoint it depends on has to as well. `rm`
            # stops it; the `finally` below only fires for a launch that never got that far.
            division.keep()
            division = None
        return code
    finally:
        if division is not None:
            division.stop()


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
