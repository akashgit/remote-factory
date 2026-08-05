"""Kubernetes/OpenShift integration — composing the commands and manifests for a cluster run.

Everything that knows the `kubectl`/`oc` CLI lives here, for the same reason `factory/podman.py`
exists: the surface is external and moves independently, and one file to fix is the difference
between a version bump and an archaeology session. The factory shells out rather than adding a
Kubernetes client library — that matches how the local target shells out to podman, and it supplies
`exec -it`, `cp` and `port-forward` for free.

Two shapes are worth reading before the code.

**The workspace arrives as one tarball, not as a directory copy.** `oc cp` of a tree is one API
round trip per file and is painfully slow on a repository. Instead the pod carries an initContainer
that blocks until the workspace has been unpacked into the PVC, the host streams a single tarball
into that initContainer over `exec -i`, and the initContainer then exits and lets the factory
container start. The wait loop is what makes the ordering work at all: an initContainer that is
waiting is *running*, and a running container is one you can exec into.

**The pod is a plain pod under the restricted SCC.** No privileged flags, no host mounts, no
capabilities. The workspace is a copy on a PVC that survives pod restart, eviction and node drain,
so a multi-hour run is recoverable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from factory.contained.errors import ContainedError

log = structlog.get_logger()

# Where the workspace lands inside the pod. Unlike the local target there is no path-preserving
# requirement — nothing outside the pod resolves these paths — so one fixed root keeps the manifests
# readable and the path rewriting trivial.
WORKSPACE_ROOT = "/workspace"

FACTORY_CONTAINER = "factory"
LOADER_CONTAINER = "workspace-loader"
SIDECAR_CONTAINER = "build-sidecar"

SERVICE_ACCOUNT = "factory"
PVC_NAME = "factory-workspace"
SECRET_NAME = "factory-credentials"

# The build sidecar runs a different image from the agent's container, and that is the whole point:
# it is the only holder of `oc` and the ServiceAccount token, and the runtime image deliberately has
# neither. Using one image for both collapses that separation — and fails at the first build with
# `oc: command not found`.
SIDECAR_IMAGE_ENV = "FACTORY_CONTAINED_SIDECAR_IMAGE"
DEFAULT_SIDECAR_IMAGE = "quay.io/openshift/origin-cli:latest"

LABEL_CONTAINED = "factory.contained"
LABEL_PROJECT = "factory.project"
LABEL_NAME = "factory.name"
LABEL_RUN = "factory.run"

# How long the loader waits for the host before giving up. Long enough for a large repository over a
# slow link; short enough that a host that died mid-upload does not pin a pod indefinitely.
LOADER_TIMEOUT_SECONDS = 900


def unpack_marker(run_name: str) -> str:
    """The marker the loader waits for, per run.

    It lives on the PVC rather than in a shared emptyDir so a pod that restarts after a successful
    unpack does not re-request the tarball it already has. It is named after the run because the PVC
    outlives the run that filled it: one shared marker would let the *next* run find it, skip its own
    upload, and quietly execute against the previous run's files.
    """
    return f"{WORKSPACE_ROOT}/.factory-unpacked-{run_name}"


class ClusterError(ContainedError):
    """A cluster operation failed in a way that should stop the run, with the cause named."""


def cli_binary() -> str:
    """`oc` when present, else `kubectl`.

    Preferred rather than required: everything the base runtime needs works with either, and the
    OpenShift-only pieces (the division's `Build` objects) check for the *API*, not the binary — a
    cluster is not OpenShift because someone installed `oc`.
    """
    for candidate in ("oc", "kubectl"):
        if shutil.which(candidate):
            return candidate
    raise ClusterError(
        "neither `oc` nor `kubectl` is on PATH. Install one and retry — `factory contained verify "
        "--target k8s` lists every cluster prerequisite."
    )


def current_namespace() -> str | None:
    """The namespace from the current context. Never hardcoded."""
    result = _run([cli_binary(), "config", "view", "--minify", "-o",
                   "jsonpath={..namespace}"])
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip() or None


def has_cluster_context() -> bool:
    """Whether a cluster is configured at all.

    `ls` spans both targets, and a laptop that has never touched a cluster should not be told its
    cluster is broken. This separates "not set up" from "set up and unreachable".
    """
    result = _run([cli_binary(), "config", "current-context"], timeout=15)
    return result is not None and result.returncode == 0 and bool(result.stdout.strip())


def resolve_sidecar_image(env: dict[str, str] | None = None) -> str:
    import os

    source = os.environ if env is None else env
    return source.get(SIDECAR_IMAGE_ENV) or DEFAULT_SIDECAR_IMAGE


def resolve_namespace(explicit: str | None) -> str:
    namespace = explicit or current_namespace()
    if not namespace:
        # Never say "pass --namespace" to someone who just did. The two causes have different
        # fixes, and blaming the user for the flag they used sends them round in circles.
        if explicit is not None:
            raise ClusterError(f"--namespace was given as {explicit!r}, which is not a usable name.")
        raise ClusterError(
            "no namespace given. Pass --namespace <name> before the subcommand, or select one "
            "with `oc project <name>`."
        )
    return namespace


def _run(argv: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        return None


# ------------------------------------------------------------------------------------------------
# Command composition
# ------------------------------------------------------------------------------------------------


def build_apply_argv(namespace: str) -> list[str]:
    return [cli_binary(), "apply", "-n", namespace, "-f", "-"]


def build_get_pods_argv(namespace: str) -> list[str]:
    """Every pod the factory created in this namespace — and nothing else."""
    return [
        cli_binary(), "get", "pods", "-n", namespace,
        "-l", f"{LABEL_CONTAINED}=true", "-o", "json",
    ]


def build_pod_exec_argv(
    name: str, namespace: str, argv: list[str], *, tty: bool = False,
    container: str = FACTORY_CONTAINER,
) -> list[str]:
    cmd = [cli_binary(), "exec"]
    if tty:
        cmd += ["-i", "-t"]
    else:
        cmd.append("-i")
    cmd += ["-n", namespace, name, "-c", container, "--", *argv]
    return cmd


def build_pod_attach_argv(
    name: str, namespace: str | None = None, *, session: str = "factory"
) -> list[str]:
    """`oc exec -it <pod> -- tmux attach`.

    tmux has no network protocol, so an exec with a TTY is the transport. A pod restart loses the
    session; the workspace survives on the PVC.
    """
    return build_pod_exec_argv(
        name, resolve_namespace(namespace), ["tmux", "attach", "-t", session], tty=True
    )


def build_delete_pod_argv(name: str, namespace: str) -> list[str]:
    return [cli_binary(), "delete", "pod", name, "-n", namespace, "--ignore-not-found"]


def render_access_review(
    verb: str, resource: str, namespace: str, *, subresource: str = "", group: str = "",
    as_service_account: str | None = None,
) -> str:
    """A SubjectAccessReview asking whether a subject may do one thing in one namespace.

    **The API object, not `oc auth can-i`** — and the difference is not stylistic. Measured against
    OpenShift 4.21 on 2026-08-04:

    | asked                | SubjectAccessReview | `oc auth can-i --as` |
    |----------------------|---------------------|----------------------|
    | `create pods`        | true                | yes                  |
    | `create pods/exec`   | **false**           | **yes**              |
    | `get pods/log`       | true                | yes                  |
    | `create secrets`     | false               | no                   |

    The CLI collapses a subresource onto its parent when impersonating, so it answers "yes" for
    `pods/exec` on a ServiceAccount that RBAC plainly denies. That single wrong answer would make
    `_no_exec_check` — the one check standing between the k8s division's sidecar and an agent that
    can exec into it — report the boundary as broken on *every* cluster, forever. Spec §8 says
    "via `SelfSubjectAccessReview`", meaning this object; the shorthand is not a substitute.

    `subresource` is a field of its own here rather than a `resource/sub` string, which is exactly
    the distinction the CLI loses.
    """
    attributes: dict[str, str] = {"namespace": namespace, "verb": verb, "resource": resource}
    if subresource:
        attributes["subresource"] = subresource
    if group:
        # Omitted means the *core* group, not "any group". A review for `builds` with no group asks
        # about a core resource that does not exist and comes back denied — which would report a
        # correctly-configured division namespace as missing its build permissions.
        attributes["group"] = group
    spec: dict[str, object] = {"resourceAttributes": attributes}
    if as_service_account:
        spec["user"] = f"system:serviceaccount:{namespace}:{as_service_account}"
        kind = "SubjectAccessReview"
    else:
        # Without a subject it is a *self* review — "can I", not "can they". Both matter, and they
        # answer different questions: whether you can create the pod, and whether the pod can do
        # what the run needs.
        kind = "SelfSubjectAccessReview"
    return json.dumps(
        {"apiVersion": "authorization.k8s.io/v1", "kind": kind, "spec": spec}, sort_keys=True
    )


def build_access_review_argv() -> list[str]:
    """Post an access review and print nothing but the verdict."""
    return [cli_binary(), "create", "-f", "-", "-o", "jsonpath={.status.allowed}"]


def access_review(
    verb: str, resource: str, namespace: str, *, subresource: str = "", group: str = "",
    as_service_account: str | None = None,
) -> bool | None:
    """Whether the subject may do this. `None` when the review could not be run at all.

    None is distinct from False on purpose: "denied" and "we could not find out" call for different
    messages, and collapsing them reports a namespace as misconfigured when the cluster was simply
    unreachable.
    """
    payload = render_access_review(
        verb, resource, namespace, subresource=subresource, group=group,
        as_service_account=as_service_account,
    )
    try:
        result = subprocess.run(
            build_access_review_argv(), input=payload, capture_output=True, text=True, timeout=60
        )
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        log.warning("k8s_access_review_failed", stderr=result.stderr.strip()[:200])
        return None
    return result.stdout.strip() == "true"


def build_api_resources_argv(api_group: str) -> list[str]:
    """Detect an API by presence, not by which binary is installed (spec §6)."""
    return [cli_binary(), "api-resources", "--api-group", api_group, "-o", "name"]


# OpenShift records the group range a namespace's pods may use in this annotation, as
# "<start>/<size>". Kubernetes chowns a volume to the pod's `fsGroup` and marks it setgid, which is
# the only supported way to make a PVC writable by a container running as an arbitrary UID.
_SUPPLEMENTAL_GROUPS_ANNOTATION = "openshift.io/sa.scc.supplemental-groups"


def namespace_fs_group(namespace: str) -> int | None:
    """The `fsGroup` this namespace's pods may use, or None when the cluster does not say.

    **Without this the workspace upload fails and it looks like a tar bug.** A freshly provisioned
    PVC mounts as `root:root 0755`; the container runs as an arbitrary UID with gid 0; and the
    unpack dies on `Cannot mkdir: Permission denied` for a directory the pod can plainly see. It is
    only a *group* permission problem, and `fsGroup` is the field that fixes it.

    Read from the namespace rather than hardcoded because an SCC with `fsGroup: MustRunAs` rejects a
    value outside its range — so a fixed number works on one cluster and fails admission on the
    next. `None` means "say nothing and let the cluster default it", which is right for plain
    Kubernetes, where volumes are not root-owned in the first place.
    """
    result = _run([
        cli_binary(), "get", "namespace", namespace,
        "-o", f"jsonpath={{.metadata.annotations.{_SUPPLEMENTAL_GROUPS_ANNOTATION.replace('.', chr(92) + '.')}}}",
    ])
    if result is None or result.returncode != 0:
        return None
    raw = result.stdout.strip().split("/")[0]
    try:
        return int(raw)
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------------
# The pod
# ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PodPlan:
    """Everything needed to create one factory pod."""

    name: str
    namespace: str
    image: str
    project_dir: str
    env: dict[str, str]
    labels: dict[str, str]
    run_command: str
    factory_command: str = ""
    storage_class: str | None = None
    secret_name: str = SECRET_NAME
    division: bool = False
    fs_group: int | None = None
    sidecar_image: str = ""
    warnings: tuple[str, ...] = field(default=())


def loader_command(run_name: str) -> str:
    """The initContainer's script: wait for the host to unpack, then get out of the way.

    Bounded rather than infinite. A host that dies mid-upload otherwise leaves a pod sitting in
    `Init` forever, which reads as a scheduling problem rather than as an upload that never
    finished.
    """
    marker = unpack_marker(run_name)
    return (
        f'echo "waiting for the workspace upload (timeout {LOADER_TIMEOUT_SECONDS}s)"; '
        f'waited=0; '
        f'while [ ! -f "{marker}" ]; do '
        f'  sleep 2; waited=$((waited+2)); '
        f'  if [ "$waited" -ge {LOADER_TIMEOUT_SECONDS} ]; then '
        f'    echo "the workspace was never uploaded; the host did not finish streaming it" >&2; '
        f'    exit 1; '
        f'  fi; '
        f'done; '
        f'echo "workspace present"'
    )


def unpack_command(run_name: str) -> str:
    """What the host runs *inside* the loader, with the tarball on stdin.

    The marker is written by the same command that unpacks, and only on success, so a partial
    transfer leaves the loader waiting rather than starting the factory on half a tree.
    """
    return f'tar xzf - -C "{WORKSPACE_ROOT}" && touch "{unpack_marker(run_name)}"'


def render_pod(plan: PodPlan) -> str:
    """The pod manifest, as YAML.

    Written out rather than templated from a library so it can be read as the thing that is applied.
    Everything here is restricted-SCC-compatible: non-root, no privilege escalation, all
    capabilities dropped, the default seccomp profile. The runtime image is built for arbitrary
    UIDs, so no `runAsUser` is pinned — the namespace picks one.
    """
    labels = "\n".join(f"    {key}: {_yaml_scalar(value)}" for key, value in sorted(plan.labels.items()))
    env = "\n".join(
        f"        - name: {key}\n          value: {_yaml_scalar(value)}"
        for key, value in sorted(plan.env.items())
    )
    sidecar = _render_sidecar(plan) if plan.division else ""
    # Omitted rather than guessed when the cluster does not publish a range: an fsGroup outside an
    # SCC's `MustRunAs` range fails admission, which is worse than the default the cluster picks.
    fs_group = f"\n    fsGroup: {plan.fs_group}" if plan.fs_group is not None else ""
    return f"""\
apiVersion: v1
kind: Pod
metadata:
  name: {plan.name}
  namespace: {plan.namespace}
  labels:
{labels}
spec:
  restartPolicy: Never
  serviceAccountName: {SERVICE_ACCOUNT}
  securityContext:
    runAsNonRoot: true{fs_group}
    seccompProfile:
      type: RuntimeDefault
  volumes:
    - name: workspace
      persistentVolumeClaim:
        claimName: {PVC_NAME}
  initContainers:
    - name: {LOADER_CONTAINER}
      image: {plan.image}
      command: ["sh", "-c", {_yaml_scalar(loader_command(plan.name))}]
      volumeMounts:
        - name: workspace
          mountPath: {WORKSPACE_ROOT}
      securityContext:
        allowPrivilegeEscalation: false
        capabilities:
          drop: ["ALL"]
  containers:
    - name: {FACTORY_CONTAINER}
      image: {plan.image}
      workingDir: {plan.project_dir}
      # `sleep infinity` for the same reason the local target uses it: the factory is not a
      # well-behaved init, the run itself lives in tmux, and the pod has to outlast the run so a
      # failure is still readable (spec §3.1, §3.4).
      command: ["sh", "-lc", "sleep infinity"]
      env:
{env}
      envFrom:
        - secretRef:
            name: {plan.secret_name}
            optional: false
      volumeMounts:
        - name: workspace
          mountPath: {WORKSPACE_ROOT}
      securityContext:
        allowPrivilegeEscalation: false
        capabilities:
          drop: ["ALL"]
{sidecar}"""


def _render_sidecar(plan: PodPlan) -> str:
    """The build sidecar (spec §6.3) — a separate container, never a process beside the agent.

    It is the only holder of a shell path to the cluster: it carries `oc` and the ServiceAccount
    token, and the agent's container carries neither. That separation is only a boundary because the
    Role excludes `pods/exec`; with that verb the agent execs into here and recovers the shell.
    """
    from factory.contained.k8s_division import sidecar_command

    return f"""\
    - name: {SIDECAR_CONTAINER}
      image: {plan.sidecar_image or resolve_sidecar_image()}
      command: ["sh", "-lc", {_yaml_scalar(sidecar_command())}]
      env:
        - name: FACTORY_BUILD_NAMESPACE
          value: {_yaml_scalar(plan.namespace)}
        - name: FACTORY_RUN_NAME
          value: {_yaml_scalar(plan.name)}
        # The build context is the *project* directory, not the workspace root, so a relative COPY
        # in the agent's Containerfile resolves the way it does on a laptop.
        - name: FACTORY_BUILD_CONTEXT
          value: {_yaml_scalar(plan.project_dir)}
      volumeMounts:
        - name: workspace
          mountPath: {WORKSPACE_ROOT}
      securityContext:
        allowPrivilegeEscalation: false
        capabilities:
          drop: ["ALL"]
"""


def _yaml_scalar(value: str) -> str:
    """Quote a scalar for YAML without pulling in a serializer for six fields."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def render_pvc(namespace: str, storage_class: str | None, size: str = "10Gi") -> str:
    """The workspace claim. RWO: one pod mounts it, and it survives that pod (spec §4.4)."""
    storage_class_line = (
        f"  storageClassName: {storage_class}\n" if storage_class else ""
    )
    return f"""\
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {PVC_NAME}
  namespace: {namespace}
  labels:
    {LABEL_CONTAINED}: "true"
spec:
  accessModes:
    - ReadWriteOnce
{storage_class_line}\
  resources:
    requests:
      storage: {size}
"""


# ------------------------------------------------------------------------------------------------
# Applying and waiting
# ------------------------------------------------------------------------------------------------


def apply_manifest(manifest: str, namespace: str) -> None:
    """Apply YAML with the *user's* own credentials, never a token the factory holds."""
    try:
        result = subprocess.run(
            build_apply_argv(namespace), input=manifest, capture_output=True, text=True, timeout=120
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ClusterError(f"applying the manifest failed: {exc}") from exc
    if result.returncode != 0:
        raise ClusterError(f"applying the manifest failed: {result.stderr.strip()}")
    log.info("k8s_applied", namespace=namespace, output=result.stdout.strip()[:200])


def wait_for_container(
    name: str, namespace: str, container: str, *, timeout: int = 300
) -> str:
    """Block until `container` is running or has finished. Returns `"running"` or `"terminated"`.

    Both are answers, and conflating them hangs: an initContainer that already did its work on an
    earlier pod for this run terminates before the host ever looks, and a wait that only accepts
    "running" then times out against a container that succeeded.

    Polled rather than `oc wait`ed: the condition here is per-container ("the loader is up"), and
    `oc wait --for=condition=Ready` is per-pod and is never satisfied while an initContainer is
    still running — which is precisely the window the upload needs.
    """
    import time

    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        result = _run([
            cli_binary(), "get", "pod", name, "-n", namespace, "-o", "json"
        ])
        if result is not None and result.returncode == 0:
            try:
                pod = json.loads(result.stdout or "{}")
            except json.JSONDecodeError:
                pod = {}
            statuses = (
                pod.get("status", {}).get("initContainerStatuses", [])
                + pod.get("status", {}).get("containerStatuses", [])
            )
            for status in statuses:
                if status.get("name") != container:
                    continue
                state = status.get("state", {})
                if "running" in state:
                    return "running"
                terminated = state.get("terminated")
                if isinstance(terminated, dict):
                    if terminated.get("exitCode") == 0:
                        return "terminated"
                    raise ClusterError(
                        f"container {container} in pod {name} exited "
                        f"{terminated.get('exitCode')} ({terminated.get('reason')}). "
                        f"`{cli_binary()} logs {name} -c {container} -n {namespace}` has why."
                    )
                last = json.dumps(state)[:200]
            phase = pod.get("status", {}).get("phase", "")
            if phase in ("Failed", "Succeeded") and not last:
                raise ClusterError(
                    f"pod {name} reached {phase} before {container} ran. "
                    f"`{cli_binary()} describe pod {name} -n {namespace}` has the reason."
                )
        time.sleep(2)
    raise ClusterError(
        f"timed out after {timeout}s waiting for container {container} in pod {name} to run"
        + (f" (last state: {last})" if last else "")
        + f". `{cli_binary()} describe pod {name} -n {namespace}` has the reason — an unschedulable "
        "pod and an unpullable image both look like this from here."
    )


def stream_workspace(tarball: Path, name: str, namespace: str) -> None:
    """Stream the packed workspace into the loader, which unpacks it and exits.

    One exec, one tarball — the whole reason this is not `oc cp` of a directory.
    """
    argv = build_pod_exec_argv(
        name, namespace, ["sh", "-c", unpack_command(name)], container=LOADER_CONTAINER
    )
    log.info("k8s_streaming_workspace", pod=name, bytes=tarball.stat().st_size)
    with tarball.open("rb") as handle:
        result = subprocess.run(argv, stdin=handle, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise ClusterError(
            f"streaming the workspace into {name} failed: {result.stderr.strip()}. The loader is "
            f"still waiting, so retrying is safe once the cause is fixed."
        )


def fetch_workspace(name: str, namespace: str, destination: Path) -> None:
    """Stream a tarball back the same way it went in."""
    argv = build_pod_exec_argv(
        name, namespace,
        ["sh", "-c", f'cd "{WORKSPACE_ROOT}" && tar czf - .'],
    )
    with destination.open("wb") as handle:
        result = subprocess.run(argv, stdout=handle, stderr=subprocess.PIPE, timeout=1800)
    if result.returncode != 0:
        raise ClusterError(
            f"fetching the workspace from {name} failed: {result.stderr.decode().strip()}"
        )


# ------------------------------------------------------------------------------------------------
# Lifecycle (spec §2.3), over pods the factory created and only those
# ------------------------------------------------------------------------------------------------


def cluster_runtimes(namespace: str | None = None) -> list:
    """Factory-created pods in the namespace, as `lifecycle.Runtime` records."""
    from factory.contained.lifecycle import LifecycleError, Runtime

    try:
        target = resolve_namespace(namespace)
    except ClusterError as exc:
        raise LifecycleError(str(exc)) from exc
    result = _run(build_get_pods_argv(target))
    if result is None:
        raise LifecycleError("`oc`/`kubectl` is not usable; cluster runtimes cannot be listed")
    if result.returncode != 0:
        raise LifecycleError(f"listing pods in {target} failed: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise LifecycleError("listing pods returned output that isn't JSON") from exc

    from datetime import datetime

    runtimes = []
    for item in payload.get("items", []):
        metadata = item.get("metadata", {})
        labels = metadata.get("labels", {})
        created = None
        stamp = metadata.get("creationTimestamp")
        if isinstance(stamp, str) and stamp:
            try:
                created = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                created = None
        runtimes.append(
            Runtime(
                name=metadata.get("name", ""),
                target="k8s",
                project=str(labels.get(LABEL_PROJECT, "")),
                state=str(item.get("status", {}).get("phase", "unknown")),
                created=created,
            )
        )
    return runtimes


def remove_cluster_runtime(name: str, *, namespace: str | None = None, assume_yes: bool = False) -> int:
    """Delete the pod. The PVC is left alone unless the user asks — it holds the work.

    A PVC deleted with the pod takes the run's output with it, and the only copy of a multi-hour
    run's work is exactly the thing not to remove on a user's behalf.
    """
    target = resolve_namespace(namespace)
    # Sweep whatever the run labelled as its own first, so a failed pod delete does not leave them
    # orphaned with nothing pointing at them (spec §6.4). Only the division creates any — validation
    # pods — but the sweep belongs here rather than there: "delete what this run labelled" is a
    # lifecycle concern, and a sweep that only exists when a feature is installed is a sweep that
    # silently stops happening.
    swept = _run(sweep_argv(target, name))
    # `oc delete --ignore-not-found` reports "No resources found" on stdout when it matched nothing,
    # so a bare non-empty check prints "swept No resources found" — which reads as if something was
    # swept. Only a line that actually says `deleted` is one.
    if swept is not None and swept.returncode == 0:
        deleted = [line for line in swept.stdout.splitlines() if "deleted" in line]
        if deleted:
            print(f"{name}: swept {len(deleted)} pod(s) the run created")

    result = _run(build_delete_pod_argv(name, target))
    if result is None or result.returncode != 0:
        detail = result.stderr.strip() if result else "the CLI could not be run"
        print(f"contained: deleting pod {name} failed: {detail}", file=sys.stderr)
        return 1
    print(f"{name}: pod deleted.")
    print(
        f"  The workspace is still on PVC {PVC_NAME} in {target}. Fetch it with "
        f"`factory contained --target k8s sync {name}` before deleting the claim."
    )
    return 0


def sweep_argv(namespace: str, run_name: str) -> list[str]:
    """Delete everything a run labelled as its own (spec §6.4).

    Selected by the run's own label, so a sweep can never reach a pod the run did not create. The
    ImageStream is deliberately not swept: it retains its tags, which is the point of having built
    them.
    """
    return [
        cli_binary(), "delete", "pods", "-n", namespace,
        "-l", f"{LABEL_RUN}={run_name}", "--ignore-not-found",
    ]


def sync_cluster_runtime(name: str, *, namespace: str | None = None) -> int:
    """Stream the workspace back to the host and report where it landed."""
    from factory.contained.workspace import contained_home

    target = resolve_namespace(namespace)
    destination = contained_home() / name / "workspace.tar.gz"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        fetch_workspace(name, target, destination)
    except ClusterError as exc:
        print(f"contained: {exc}", file=sys.stderr)
        return 1
    print(f"{name}: workspace fetched to {destination}.")
    print(
        "  Review:  tar tzf "
        f"{destination}\n"
        f"  Unpack:  mkdir -p <dir> && tar xzf {destination} -C <dir>\n"
        "Nothing is merged automatically."
    )
    return 0
