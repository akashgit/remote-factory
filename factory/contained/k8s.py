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

LABEL_CONTAINED = "factory.contained"
LABEL_PROJECT = "factory.project"
LABEL_NAME = "factory.name"
LABEL_RUN = "factory.run"

# The marker the loader waits for. Inside the PVC rather than in a shared emptyDir, so a pod that
# restarts after a successful unpack does not re-request the tarball it already has.
UNPACK_MARKER = f"{WORKSPACE_ROOT}/.factory-unpacked"

# How long the loader waits for the host before giving up. Long enough for a large repository over a
# slow link; short enough that a host that died mid-upload does not pin a pod indefinitely.
LOADER_TIMEOUT_SECONDS = 900


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
    """The namespace from the current context. Never hardcoded (spec §2.2)."""
    result = _run([cli_binary(), "config", "view", "--minify", "-o",
                   "jsonpath={..namespace}"])
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip() or None


def resolve_namespace(explicit: str | None) -> str:
    namespace = explicit or current_namespace()
    if not namespace:
        raise ClusterError(
            "no namespace given and the current context does not name one. Pass --namespace, or "
            "select one with `oc project <name>`."
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

    tmux has no network protocol, so an exec with a TTY is the transport here exactly as it is
    locally. A pod restart loses the session; the workspace survives on the PVC (spec §4.6).
    """
    return build_pod_exec_argv(
        name, resolve_namespace(namespace), ["tmux", "attach", "-t", session], tty=True
    )


def build_delete_pod_argv(name: str, namespace: str) -> list[str]:
    return [cli_binary(), "delete", "pod", name, "-n", namespace, "--ignore-not-found"]


def build_can_i_argv(
    verb: str, resource: str, namespace: str, *, as_service_account: str | None = None
) -> list[str]:
    """A SelfSubjectAccessReview, or a SubjectAccessReview when asking about the ServiceAccount.

    Both matter and they answer different questions: whether *you* can create the pod, and whether
    the *pod* can do what the run needs. Reporting only the first passes a namespace that will fail
    on the agent's first cluster call.
    """
    cmd = [cli_binary(), "auth", "can-i", verb, resource, "-n", namespace, "--quiet"]
    if as_service_account:
        cmd += ["--as", f"system:serviceaccount:{namespace}:{as_service_account}"]
    return cmd


def build_api_resources_argv(api_group: str) -> list[str]:
    """Detect an API by presence, not by which binary is installed (spec §6)."""
    return [cli_binary(), "api-resources", "--api-group", api_group, "-o", "name"]


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
    warnings: tuple[str, ...] = field(default=())


def loader_command() -> str:
    """The initContainer's script: wait for the host to unpack, then get out of the way.

    Bounded rather than infinite. A host that dies mid-upload otherwise leaves a pod sitting in
    `Init` forever, which reads as a scheduling problem rather than as an upload that never
    finished.
    """
    return (
        f'echo "waiting for the workspace upload (timeout {LOADER_TIMEOUT_SECONDS}s)"; '
        f'waited=0; '
        f'while [ ! -f "{UNPACK_MARKER}" ]; do '
        f'  sleep 2; waited=$((waited+2)); '
        f'  if [ "$waited" -ge {LOADER_TIMEOUT_SECONDS} ]; then '
        f'    echo "the workspace was never uploaded; the host did not finish streaming it" >&2; '
        f'    exit 1; '
        f'  fi; '
        f'done; '
        f'echo "workspace present"'
    )


def unpack_command() -> str:
    """What the host runs *inside* the loader, with the tarball on stdin.

    The marker is written by the same command that unpacks, and only on success, so a partial
    transfer leaves the loader waiting rather than starting the factory on half a tree.
    """
    return f'tar xzf - -C "{WORKSPACE_ROOT}" && touch "{UNPACK_MARKER}"'


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
    runAsNonRoot: true
    seccompProfile:
      type: RuntimeDefault
  volumes:
    - name: workspace
      persistentVolumeClaim:
        claimName: {PVC_NAME}
  initContainers:
    - name: {LOADER_CONTAINER}
      image: {plan.image}
      command: ["sh", "-c", {_yaml_scalar(loader_command())}]
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
      image: {plan.image}
      command: ["sh", "-lc", {_yaml_scalar(sidecar_command())}]
      env:
        - name: FACTORY_BUILD_NAMESPACE
          value: {_yaml_scalar(plan.namespace)}
        - name: FACTORY_RUN_NAME
          value: {_yaml_scalar(plan.name)}
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


def wait_for_container(name: str, namespace: str, container: str, *, timeout: int = 300) -> None:
    """Block until `container` is running, so the host can exec into it.

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
                    return
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
        name, namespace, ["sh", "-c", unpack_command()], container=LOADER_CONTAINER
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
    if swept is not None and swept.returncode == 0 and swept.stdout.strip():
        print(f"{name}: swept {swept.stdout.strip()}")

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
