"""Running the factory in a cluster pod.

The sequence, and why it is this sequence:

1. **Materialize** the same workspace copy the local target uses — the run starts from the files on
   this machine, uncommitted changes included, and that rule does not change because the
   destination is remote.
2. **Scan** it for secrets, because from here it leaves the machine.
3. **Pack** it into one tarball. `oc cp` of a tree is one API round trip per file.
4. **Create** the pod, whose initContainer blocks waiting for the workspace.
5. **Stream** the tarball into that initContainer, which unpacks it and exits.
6. **Assert** provenance inside the pod, before the factory starts — the packer copies what it is
   told, so the filtered-transfer trap that a bind mount removed locally is live here.
7. **Start** the run in tmux.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

import structlog

from factory.contained.credentials import resolve_credentials, vertex_model_warning
from factory.contained.env import CONTAINED_ENV_POLICY
from factory.contained.errors import ContainedError
from factory.contained.k8s import (
    FACTORY_CONTAINER,
    LABEL_CONTAINED,
    LABEL_NAME,
    LABEL_PROJECT,
    LOADER_CONTAINER,
    PVC_NAME,
    WORKSPACE_ROOT,
    ClusterError,
    PodPlan,
    apply_manifest,
    build_pod_exec_argv,
    render_pod,
    render_pvc,
    ADC_PATH,
    ADC_SECRET_KEY,
    SECRET_NAME,
    namespace_fs_group,
    secret_keys,
    resolve_sidecar_image,
    resolve_namespace,
    stream_workspace,
    wait_for_container,
)
from factory.contained.paths import rewrite_argv
from factory.contained.provenance import content_probe, provenance_probes
from factory.contained.secrets import confirm_upload, scan
from factory.contained.workspace import (
    Workspace,
    WorkspaceError,
    contained_home,
    materialize,
    plan_workspace,
)
from factory.podman import (
    TMUX_SESSION,
    build_run_command,
    container_name,
    dry_run_enabled,
    growth_context_warning,
    project_hash,
    resolve_image,
)

log = structlog.get_logger()

# Directories that must never be packed. They are large, they are host-shaped, and an arm64 .venv
# unpacked onto an amd64 node is actively wrong rather than merely wasteful. `.git` is *not* here:
# without it the pod reports no_repo, the CEO silently drops to build mode, and the eventual error
# names a flag several steps from the cause.
PACK_EXCLUDES = frozenset({
    ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".factory-worktrees",
})


def run_k8s(args: argparse.Namespace) -> int:
    """Provision a cluster pod and start the run in it."""
    dry_run = dry_run_enabled()
    try:
        from factory.cli.contained import _resolve_project

        project = _resolve_project(args.factory_args)
        namespace = resolve_namespace(args.namespace)
        if args.division:
            _require_openshift(dry_run)
        run_id = args.name or container_name(project)
        # Self-contained: nothing from this machine is mounted in a pod, so the copy has to carry
        # its own .git rather than a pointer to one (see `plan_workspace`).
        ws = (
            plan_workspace(project, run_id, self_contained=True) if dry_run
            else materialize(project, run_id, self_contained=True)
        )
        plan = _build_pod_plan(args, ws, namespace, run_id)
    except (ContainedError, WorkspaceError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    for warning in (growth_context_warning(), *plan.warnings):
        if warning:
            print(f"Warning: {warning}", file=sys.stderr)

    if dry_run:
        return _emit_dry_run(plan, args)

    try:
        if not _scan_and_confirm(ws, assume_yes=args.yes):
            return 1
        from factory.contained.usage import record_target

        record_target("k8s")
        tarball = _pack(ws, run_id)
        _provision(plan, tarball)
        return _start(plan, ws, project)
    except ClusterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _require_openshift(dry_run: bool) -> None:
    """Refuse at launch, naming the reason (spec.6 step 1).

    Detected by API presence rather than by the `oc` binary. A run that gets as far as submitting a
    Build the cluster will never admit has already spent a workspace upload and a pod start.
    """
    if dry_run:
        return
    from factory.contained.k8s_division import openshift_available

    if not openshift_available():
        raise ClusterError(
            "--target k8s --division needs the OpenShift Build API (build.openshift.io), which "
            "this cluster does not serve. Plain-Kubernetes builds are out of scope by decision: "
            "rootless buildah, kaniko and buildkit all depend on a /proc/self/uid_map write these "
            "nodes deny. Run without --division — the factory still runs, it just cannot build "
            "images."
        )


def _project_dir(ws: Workspace) -> str:
    """Where the project lands in the pod. Unlike the local target this is not path-preserving —
    nothing outside the pod resolves it."""
    return f"{WORKSPACE_ROOT}/{ws.source.name}"


def _build_pod_plan(
    args: argparse.Namespace, ws: Workspace, namespace: str, run_id: str
) -> PodPlan:
    warnings: list[str] = []
    project_dir = _project_dir(ws)

    shape = resolve_credentials()
    # The pod's credentials come from the namespace Secret, not from this machine. What crosses here
    # is configuration only — the Secret is mounted with `envFrom` and the factory never reads it.
    env = CONTAINED_ENV_POLICY.resolve(dict(os.environ))
    for name in args.forward:
        value = os.environ.get(name)
        if value is None:
            raise ContainedError(f"--forward {name}: not set in this environment")
        env[name] = value
    from factory.cli.contained import _parse_extra_env

    env.update(_parse_extra_env(args.extra_env))

    model_warning = vertex_model_warning(shape, args.factory_args)
    if model_warning:
        warnings.append(model_warning)
    if any(_is_secretish(key) for key in env):
        warnings.append(
            "a credential-looking variable is being forwarded into the pod manifest, where it is "
            "visible to anyone who can read pods in the namespace. The credentials Secret is "
            "the supported route."
        )

    factory_argv, changes = rewrite_argv(args.factory_args, ws.source, project_dir)
    for before, after in changes:
        log.debug("contained_path_rewritten", before=before, after=after)
    inner = "factory " + " ".join(shlex.quote(token) for token in factory_argv)

    mcp_config: dict[str, object] | None = None
    files: dict[str, str] = {}
    if args.division:
        from factory.contained import k8s_division

        mcp_config = k8s_division.mcp_config(namespace)
        files = k8s_division.division_files(namespace, run_id)

    # A Google credential has to arrive as a file, so the launch has to know whether one is there.
    # Keys only — the value never leaves the cluster.
    adc = ADC_SECRET_KEY in secret_keys(SECRET_NAME, namespace)
    if adc:
        env["GOOGLE_APPLICATION_CREDENTIALS"] = ADC_PATH

    return PodPlan(
        name=run_id,
        namespace=namespace,
        image=args.image or resolve_image(),
        project_dir=project_dir,
        env=env,
        labels={
            LABEL_CONTAINED: "true",
            LABEL_PROJECT: project_hash(ws.source),
            LABEL_NAME: run_id,
        },
        run_command=build_run_command(project_dir, inner, mcp_config=mcp_config, files=files),
        factory_command=inner,
        storage_class=args.storage_class,
        division=args.division,
        fs_group=namespace_fs_group(namespace),
        sidecar_image=resolve_sidecar_image(),
        adc=adc,
        warnings=tuple(warnings),
    )


def _is_secretish(key: str) -> bool:
    from factory.contained.env import is_secret_key

    return is_secret_key(key)


def _scan_and_confirm(ws: Workspace, *, assume_yes: bool) -> bool:
    """Nothing leaves the machine before this returns True."""
    result = scan(ws.path)
    return confirm_upload(result, assume_yes=assume_yes)


def _pack(ws: Workspace, run_id: str) -> Path:
    """Pack the workspace into one tarball, under its own directory name.

    Packed as `<project>/...` rather than `./...` so it unpacks to `/workspace/<project>`, which is
    the path everything downstream — the working directory, the rewritten payload, the provenance
    probes — already agrees on.
    """
    destination = contained_home() / run_id / "upload.tar.gz"
    destination.parent.mkdir(parents=True, exist_ok=True)

    def _filter(entry: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = set(Path(entry.name).parts)
        return None if parts & PACK_EXCLUDES else entry

    with tarfile.open(destination, "w:gz") as archive:
        archive.add(ws.path, arcname=ws.source.name, filter=_filter)
    log.debug("contained_packed", path=str(destination), bytes=destination.stat().st_size)
    return destination


def _provision(plan: PodPlan, tarball: Path) -> None:
    """Create the claim and the pod, then stream the workspace into the waiting loader."""
    apply_manifest(render_pvc(plan.namespace, plan.storage_class), plan.namespace)
    apply_manifest(render_pod(plan), plan.namespace)
    # The identifier first, before any long-running work: a run whose name the user cannot see is a
    # run they cannot manage.
    print(plan.name)
    state = wait_for_container(plan.name, plan.namespace, LOADER_CONTAINER)
    if state == "running":
        stream_workspace(tarball, plan.name, plan.namespace)
    else:
        # Already unpacked for *this* run — the pod restarted after a successful upload. The marker
        # is per-run, so this can never mean "a previous run's files are already here".
        log.debug("contained_workspace_already_present", pod=plan.name)
    wait_for_container(plan.name, plan.namespace, FACTORY_CONTAINER)


def _start(plan: PodPlan, ws: Workspace, project: Path) -> int:
    """Assert provenance inside the pod, then start the run."""
    probes = provenance_probes(
        plan.project_dir,
        expect_factory_state=(project / ".factory" / "config.json").exists(),
        expect_git=(project / ".git").exists(),
        content=content_probe(ws.path),
    )
    for probe in probes:
        argv = build_pod_exec_argv(plan.name, plan.namespace, probe.argv)
        result = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            print(
                f"contained: assertion '{probe.name}' failed in pod {plan.name}\n  {probe.hint}\n"
                f"  The pod is still there for inspection:\n"
                f"    oc exec -it {plan.name} -n {plan.namespace} -- sh\n"
                f"    factory contained --target k8s rm {plan.name}",
                file=sys.stderr,
            )
            return 1

    # A pod of this name may already be mid-run: `apply` is idempotent, so a re-invocation reuses it
    # rather than failing, and the tmux launch then collides with the session already there. Raw,
    # that surfaces as "duplicate session: factory", which names tmux for what is really "you
    # already have this run". The local target has the same shape of check on container creation.
    existing = subprocess.run(
        build_pod_exec_argv(plan.name, plan.namespace, ["tmux", "has-session", "-t", TMUX_SESSION]),
        capture_output=True, text=True, timeout=120,
    )
    if existing.returncode == 0:
        print(
            f"contained: {plan.name} is already running a session — this is the same run, not a new "
            f"one.\n"
            f"  attach:  factory contained --target k8s attach {plan.name}\n"
            f"  restart: factory contained --target k8s rm {plan.name}, then run this again",
            file=sys.stderr,
        )
        return 1

    launch = build_pod_exec_argv(
        plan.name, plan.namespace,
        ["sh", "-lc", _tmux_launch(plan)],
    )
    result = subprocess.run(launch, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        print(f"contained: starting the run failed: {result.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"  attach:  factory contained --target k8s attach {plan.name}")
    print(f"  result:  factory contained --target k8s sync {plan.name}")
    print(f"  logs:    oc logs -f {plan.name} -n {plan.namespace} -c {FACTORY_CONTAINER}")
    return 0


def _tmux_launch(plan: PodPlan) -> str:
    from factory.podman import build_tmux_launch

    return build_tmux_launch(plan.project_dir, plan.run_command)


def _emit_dry_run(plan: PodPlan, args: argparse.Namespace) -> int:
    """Print the manifests and the commands the real path would apply and run, and do neither."""
    print(f"DRY RUN — {plan.name} in {plan.namespace} ({plan.image}); nothing is provisioned.")
    print(f"[apply] pvc/{PVC_NAME}")
    print(render_pvc(plan.namespace, plan.storage_class))
    print(f"[apply] pod/{plan.name}")
    print(render_pod(plan))
    print(f"[upload] {shlex.join(build_pod_exec_argv(plan.name, plan.namespace, ['sh', '-c', 'tar xzf - -C ' + WORKSPACE_ROOT], container=LOADER_CONTAINER))}")
    print(f"[run] {shlex.join(build_pod_exec_argv(plan.name, plan.namespace, ['sh', '-lc', _tmux_launch(plan)]))}")
    return 0
