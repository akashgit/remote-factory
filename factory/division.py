"""The division — the container-manufacturing plane that lives outside the sandbox.

OpenShell's sandbox supervisor installs a seccomp filter after privilege drop with
`PR_SET_NO_NEW_PRIVS` set, so it cannot be dropped and every child inherits it. `mount`, `unshare`
with `CLONE_NEWUSER`, `pivot_root`, and `bpf` are unconditionally blocked, which rules out
Docker-in-Docker, rootless podman, buildah, kaniko, and buildkit *inside* the sandbox. Pod-level
privilege does not help: the filter is self-imposed within the process tree, so a privileged pod
still returns EPERM to the agent.

Builds therefore have to happen in a container that is not the agent's process tree. That single
fact is what the division is, and why `--division` is opt-in and separately named: it deliberately
opens the isolation boundary. What this module can do is keep the opening exactly as narrow as
designed — a bind mount scoped to the project directory, an MCP allowlist with no wildcards, and a
build pod that never asks for privilege.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# The exact tool set the local division needs. MCP policy rules match on method and tool name and
# **cannot inspect arguments**, so this list is the entire enforcement surface — an extra entry is a
# permanently wider boundary, and a wildcard is no boundary at all.
LOCAL_DIVISION_TOOLS = (
    "image_build",
    "container_run",
    "container_logs",
    "container_stop",
    "container_remove",
    "image_list",
)

# The k8s division's loop: create the Build, watch it, read its logs, diagnose the failures that
# produce no logs at all, run a validation pod, clean up. `pods_exec` is deliberately absent — the
# loop never needs a shell in someone else's pod, and policy cannot constrain what it would run.
K8S_DIVISION_TOOLS = (
    "resources_create_or_update",
    "resources_get",
    "resources_list",
    "resources_delete",
    "pods_list_in_namespace",
    "pods_log",
    "pods_run",
    "pods_delete",
    "events_list",
)

# podman-mcp-server and kubernetes-mcp-server both run on the host and are reached through
# OpenShell's injected bridge hostname. Different ports so a division of each kind can coexist.
DIVISION_HOST = "host.openshell.internal"
DIVISION_PORT = 8430
K8S_DIVISION_PORT = 8440
DIVISION_PATH = "/mcp"

POLICY_NAME = "factory_division"

# Where rootless buildah keeps container storage. It must be an emptyDir: on xfs that yields
# "Native Overlay Diff: true", so overlay works without fuse-overlayfs and without /dev/fuse.
CONTAINER_STORAGE_PATH = "/home/build/.local/share/containers"
BUILD_CONTEXT_PATH = "/workspace"

# Resolution order for the build pod's SCC. `nested-container` grants exactly SETUID and SETGID with
# allowPrivilegedContainer false and runAsUser MustRunAsRange, so root is impossible rather than
# merely unused — a strictly tighter grant than anyuid, which permits any UID including root.
SCC_PREFERENCE = ("nested-container", "anyuid")


class DivisionError(Exception):
    """A division precondition that must stop provisioning rather than degrade."""


# ── local division: bind mount ────────────────────────────────────────────────


# Overrides driver detection. Exists so the driver-config rendering can be exercised for both
# backends without standing up two gateways; it never overrides a driver the gateway reports.
DRIVER_ENV = "FACTORY_OPENSHELL_DRIVER"


def detect_compute_driver(gateway: str | None = None, env: dict[str, str] | None = None) -> str | None:
    """Return the gateway's active compute driver (`podman` or `docker`), or None if unknown.

    The driver-config JSON is keyed by driver name, so guessing wrong produces a config the gateway
    silently ignores — the sandbox comes up with no bind mount at all, and the failure only surfaces
    later as an unresolvable path inside `image_build`.
    """
    source = os.environ if env is None else env
    override = source.get(DRIVER_ENV, "").strip().lower()
    if override in ("podman", "docker"):
        return override
    if shutil.which("openshell") is None:
        return None
    cmd = ["openshell", "gateway", "info", "-o", "json"]
    if gateway:
        cmd += ["--gateway", gateway]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        info = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    # `gateway info` reports drivers as a list: {"compute_drivers": [{"name": "docker", ...}]}.
    # The singular keys are tried too because this JSON is alpha and has already moved once.
    drivers = info.get("compute_drivers") if isinstance(info, dict) else None
    candidates: list[Any] = []
    if isinstance(drivers, list):
        for entry in drivers:
            if isinstance(entry, dict):
                candidates += [entry.get("name"), _find_key(entry, "driver_name")]
            else:
                candidates.append(entry)
    candidates += [_find_key(info, "compute_driver"), _find_key(info, "driver")]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.lower() in ("podman", "docker"):
            return candidate.lower()
    return None


def _find_key(blob: Any, key: str) -> Any:
    """Depth-first search for a key in a nested structure of unknown shape.

    OpenShell is alpha and its JSON shape is not stable, so this reads defensively rather than
    indexing a path that may move between releases.
    """
    if isinstance(blob, dict):
        if key in blob:
            return blob[key]
        for value in blob.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(blob, list):
        for item in blob:
            found = _find_key(item, key)
            if found is not None:
                return found
    return None


def bind_mount_config(driver: str, project_path: Path) -> dict[str, Any]:
    """Driver-config JSON that mounts the project at the same absolute path inside and out.

    `image_build` takes `containerFile` as an absolute path **on the machine running
    podman-mcp-server** — the host. So a path the agent writes inside the sandbox has to resolve
    identically outside it, which is why source and target are the same string rather than a tidier
    `/workspace`.

    The mount is the project directory and nothing above it. Widening it to `$HOME` or `/` would
    hand the division the rest of the filesystem for no benefit; the project tree is already the
    tree the agent is authorised to modify.
    """
    if driver not in ("podman", "docker"):
        raise DivisionError(f"unknown compute driver {driver!r}; expected podman or docker")
    resolved = str(project_path)
    return {
        driver: {
            "mounts": [
                {"type": "bind", "source": resolved, "target": resolved, "read_only": False}
            ]
        }
    }


# Where a gateway running on this machine keeps its configuration. `gateway info` does not report
# `enable_bind_mounts` in 0.0.92 — verified against a gateway that has it set — so the file is the
# only place the answer exists locally.
GATEWAY_CONFIG_PATHS = (
    Path("~/.config/openshell/gateway.toml").expanduser(),
    Path("/opt/homebrew/var/openshell/gateway.toml"),
    Path("/etc/openshell/gateway.toml"),
)

# What the gateway says when it refuses a bind mount. Matched against a failed `sandbox create` so
# the refusal can be reported as the misconfiguration it is rather than as an opaque driver error.
BIND_MOUNT_REFUSAL = "enable_bind_mounts"


def bind_mounts_enabled(gateway: str | None = None) -> bool | None:
    """True / False / None-for-unknown, read from a locally readable gateway config.

    None is the common answer and an honest one: the setting lives in the gateway's own config,
    the gateway may be on another machine, and `gateway info` does not expose it.
    """
    for path in GATEWAY_CONFIG_PATHS:
        try:
            text = path.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.split("#", 1)[0].strip()
            if stripped.startswith("enable_bind_mounts"):
                return stripped.partition("=")[2].strip().lower() == "true"
        return False
    return None


def check_bind_mounts_enabled(gateway: str | None = None) -> list[str]:
    """Raise when bind mounts are known to be off; return warnings when it cannot be determined.

    The earlier version of this fell back to "cannot confirm means not enabled" and refused to
    provision. Against openshell 0.0.92 that refuses *every* run, including ones on a correctly
    configured gateway, because the setting is simply not in any API response. So the check now
    distinguishes a known-off gateway — which is worth stopping for, since the sandbox would get a
    copy of the project and `image_build` would fail on a path the host does not have — from an
    unknown one, where the gateway itself refuses the create with a message naming the setting and
    nothing is provisioned either way.
    """
    enabled = bind_mounts_enabled(gateway)
    if enabled is True:
        return []
    if enabled is False:
        raise DivisionError(
            "--division local requires `enable_bind_mounts = true` under the compute driver's "
            "table in the gateway's gateway.toml, and the local config has it off or absent. Set "
            "it, restart the gateway, and retry. Refusing to provision: without the mount the "
            "sandbox gets a copy of the project rather than the host's directory, and image_build "
            "would then fail on a path that does not exist on the host."
        )
    return [
        "could not read a local gateway.toml, so `enable_bind_mounts` is unverified. If it is off, "
        "the gateway refuses the sandbox create by name and nothing is provisioned."
    ]


# ── local division: MCP policy ────────────────────────────────────────────────


# The base policy the division extends. `--policy` replaces the sandbox's default policy outright
# instead of merging into it, so a file carrying only the division's rule leaves the sandbox with no
# filesystem policy at all — verified: `/usr`, `/sandbox` and `/dev/null` all become inaccessible.
BASE_POLICY_PATH = Path(__file__).parent / "templates" / "sandbox-policy.yaml"


def load_base_policy() -> dict[str, Any]:
    loaded = yaml.safe_load(BASE_POLICY_PATH.read_text())
    if not isinstance(loaded, dict):
        raise DivisionError(f"{BASE_POLICY_PATH} does not contain a single YAML mapping")
    return loaded


def build_local_policy(
    project_path: Path | None = None,
    *,
    host: str = DIVISION_HOST,
    port: int = DIVISION_PORT,
    path: str = DIVISION_PATH,
    tools: tuple[str, ...] = LOCAL_DIVISION_TOOLS,
) -> dict[str, Any]:
    """Render the complete sandbox policy for a local division.

    One MCP rule per tool, by name. No wildcard, and no rule that matches a method without naming a
    tool — because policy cannot inspect arguments, the tool name is the only thing being
    constrained, and anything broader constrains nothing.

    `project_path` is the bind mount's target. It has to be named in `filesystem_policy.read_write`
    or the mount is present and unreadable: the bind mount puts the directory in the container and
    the policy decides whether the agent may open it, and those are two different gates.
    """
    duplicates = sorted({t for t in tools if tools.count(t) > 1})
    if duplicates:
        raise DivisionError(f"duplicate tools in the division allowlist: {duplicates}")
    wildcards = sorted(t for t in tools if "*" in t or not t.strip())
    if wildcards:
        raise DivisionError(f"wildcard or empty tool name in the division allowlist: {wildcards}")

    policy = load_base_policy()
    if project_path is not None:
        writable = policy.setdefault("filesystem_policy", {}).setdefault("read_write", [])
        if str(project_path) not in writable:
            writable.append(str(project_path))
    policy.setdefault("network_policies", {})[POLICY_NAME] = {
        "name": POLICY_NAME.replace("_", "-"),
        "endpoints": [
            {
                "host": host,
                "port": port,
                "protocol": "mcp",
                "path": path,
                "enforcement": "enforce",
                # `allow:`-wrapped, and keyed `tool` rather than `name`. The gateway rejects the
                # flat form at parse time with `unknown field 'method', expected 'allow'`, which
                # aborts provisioning before the sandbox exists.
                "rules": [{"allow": {"method": "tools/call", "tool": tool}} for tool in tools],
                "mcp": {
                    # Without this the handshake itself is denied — the rules above cover
                    # `tools/call` and nothing else, so `initialize` and `tools/list` are refused
                    # and the client never learns the server exists. `strict_tool_names` is what
                    # keeps the allowlist meaningful once the other methods are permitted.
                    "allow_all_known_mcp_methods": True,
                    "strict_tool_names": True,
                },
            }
        ],
        # A policy with no `binaries` matches no process and denies everything — verified: the same
        # policy minus this list answers 403 to the request it otherwise allows. These carry the
        # agent's MCP client — `claude` and `node` for an HTTP server, the sandbox's Python for the
        # stdio bridge — and deliberately not `curl`, which would let anything in the sandbox reach
        # the division endpoint.
        "binaries": [
            {"path": "/usr/local/bin/claude"},
            {"path": "/usr/bin/node"},
            *({"path": interpreter} for interpreter in BRIDGE_INTERPRETERS),
        ],
    }
    return policy


def render_policy_yaml(policy: dict[str, Any]) -> str:
    return yaml.safe_dump(policy, sort_keys=False, default_flow_style=False)


def mcp_client_config(
    *, host: str = DIVISION_HOST, port: int = DIVISION_PORT, path: str = DIVISION_PATH
) -> dict[str, Any]:
    """The MCP client registration the agent inside the sandbox needs.

    The network policy *permits* these tool calls; it does not advertise them. Without a client
    registration the agent never learns the server exists, and the division is allowed but unused —
    a failure that looks like the agent simply choosing not to build.

    Written as a project-scoped `.mcp.json`, which the agent CLIs discover on their own, so no
    runner needs a new flag.
    """
    return {
        "mcpServers": {
            "podman": {
                "type": "http",
                "url": f"http://{host}:{port}{path}",
            }
        }
    }


def check_division_endpoint(*, host: str = "127.0.0.1", port: int = DIVISION_PORT) -> None:
    """Raise unless podman-mcp-server is already listening.

    The factory does not start it. `podman-mcp-server` has no authentication on its HTTP endpoint
    (spec §5.1, deferred by decision §9.5), so spawning an unauthenticated service that fronts the
    host's podman socket is not something to do on a user's behalf without being asked. Checking and
    saying exactly what to run is the honest middle.
    """
    import socket

    try:
        with socket.create_connection((host, port), timeout=2.0):
            return
    except OSError as exc:
        raise DivisionError(
            f"--division local needs podman-mcp-server listening on {host}:{port}, and nothing is "
            f"there ({exc}). Start it with:\n"
            f"    podman-mcp-server --port {port}\n"
            "Bind it to a loopback interface only — it has no authentication, and it fronts the "
            "host's podman socket."
        ) from exc


# ── k8s division: namespace, MCP wiring, and the build objects ────────────────


def current_namespace() -> str | None:
    """The namespace of the kubeconfig's current context, or None when it cannot be read.

    Read rather than defaulted. `default` is a real namespace on every cluster, and quietly
    creating build objects there because the current context could not be parsed is the kind of
    mistake that is only noticed by someone else.
    """
    if shutil.which("kubectl") is None and shutil.which("oc") is None:
        return None
    binary = "oc" if shutil.which("oc") else "kubectl"
    try:
        result = subprocess.run(
            [binary, "config", "view", "--minify", "-o", "jsonpath={..namespace}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    namespace = result.stdout.strip()
    return namespace or None


def build_k8s_policy(
    project_path: Path | None = None,
    *,
    host: str = DIVISION_HOST,
    port: int = K8S_DIVISION_PORT,
    path: str = DIVISION_PATH,
    tools: tuple[str, ...] = K8S_DIVISION_TOOLS,
) -> dict[str, Any]:
    """The k8s division's complete sandbox policy — same shape as the local one, different tools."""
    return build_local_policy(project_path, host=host, port=port, path=path, tools=tools)


# Where the stdio bridge is written inside the sandbox, and the interpreters allowed to run it.
MCP_BRIDGE_PATH = ".factory/division/mcp_bridge.py"
DIVISION_BRIEF_PATH = ".factory/division/README.md"
MCP_BRIDGE_SOURCE = Path(__file__).parent / "templates" / "mcp_stdio_bridge.py"
BRIDGE_INTERPRETERS = ("/sandbox/.venv/bin/python", "/sandbox/.venv/bin/python3")


def mcp_bridge_source() -> str:
    return MCP_BRIDGE_SOURCE.read_text()


def division_brief(division: str, *, manifest: str = "", image_ref: str = "") -> str:
    """What the agent needs to know about the division it has been given.

    Registering the MCP server advertises the tools but says nothing about what they are *for*.
    Left to infer it, an agent reads "build a container image" as a feature request and starts
    writing a CLI command that would shell out to podman — verified: a Refiner scoped exactly that,
    165 lines across three files, in the same breath as "do not modify any source file". The tools
    are a capability the run already has, not something to build, and that is what this says.
    """
    if division == "local":
        return (
            "# Division: local\n\n"
            "This run can build container images **right now**, through the `podman` MCP server "
            "already registered in `.mcp.json`. Use `mcp__podman__image_build` "
            "(`containerFile` is an absolute path, `imageName` is the tag), then "
            "`mcp__podman__container_run` to validate and `mcp__podman__container_logs` to read "
            "the result.\n\n"
            "This is an operational capability, not a feature to implement. Do not write code that "
            "wraps these tools, and do not add a CLI command for them — call them.\n"
        )
    return (
        "# Division: k8s\n\n"
        "This run can build container images **right now**, in the cluster, through the "
        "`kubernetes` MCP server already registered in `.mcp.json`. The build context and its "
        "ImageStream are already in the namespace.\n\n"
        f"1. Read `{manifest}` — a complete OpenShift `Build` object.\n"
        "2. Submit it with `mcp__kubernetes__resources_create_or_update`.\n"
        "3. Poll it with `mcp__kubernetes__resources_get` until `status.phase` is `Complete` or "
        "`Failed`. On failure, read `mcp__kubernetes__pods_log` for the build pod and "
        "`mcp__kubernetes__events_list` for the failures that produce no logs, fix "
        "`spec.source.dockerfile` in the manifest, and submit a **new** Build.\n"
        f"4. On success, validate with `mcp__kubernetes__pods_run` on `{image_ref}`.\n\n"
        "This is an operational capability, not a feature to implement. Do not write code that "
        "wraps these tools, and do not add a CLI command for them — call them.\n"
    )


def mcp_client_config_k8s(
    *,
    host: str = DIVISION_HOST,
    port: int = K8S_DIVISION_PORT,
    path: str = DIVISION_PATH,
    bridge: str = MCP_BRIDGE_PATH,
) -> dict[str, Any]:
    """Register the k8s MCP server over **stdio**, through a bridge, not as an HTTP server.

    Claude Code probes `/.well-known/oauth-protected-resource/<path>` before connecting to an HTTP
    MCP server. An `mcp`-protocol policy endpoint matches only its own path, so inside a sandbox
    that probe is denied, the 403 reads as "protected resource", and the client exposes
    `authenticate` instead of the server's tools — an OAuth flow no headless run can complete. The
    same server connects fine from outside. Registering it as stdio skips discovery; the bridge
    then speaks plain HTTP to the endpoint the policy already allows, so the tool allowlist is
    enforced exactly as before.
    """
    return {
        "mcpServers": {
            "kubernetes": {
                "type": "stdio",
                "command": "python3",
                "args": [bridge],
                "env": {
                    "FACTORY_MCP_BRIDGE_URL": f"http://{host}:{port}{path}",
                    # kubernetes-mcp-server rejects the bridge hostname outright with
                    # `invalid Host header`; it allowlists loopback names for DNS-rebinding
                    # protection. The connection still goes to `host`.
                    "FACTORY_MCP_BRIDGE_HOST_HEADER": f"localhost:{port}",
                },
            }
        }
    }


@dataclass(frozen=True)
class BuildObjectsSpec:
    """Inputs for the OpenShift Build objects the k8s division drives."""

    name: str
    namespace: str
    tag: str
    dockerfile: str
    context_configmap: str = "factory-build-context"
    service_account: str = "builder"


def render_image_stream(spec: BuildObjectsSpec) -> dict[str, Any]:
    """The ImageStream the build pushes into. Without it the build fails at admission with
    `InvalidOutputReference: Output image could not be resolved`."""
    return {
        "apiVersion": "image.openshift.io/v1",
        "kind": "ImageStream",
        "metadata": {"name": spec.name, "namespace": spec.namespace},
    }


def render_build(spec: BuildObjectsSpec, *, build_name: str) -> dict[str, Any]:
    """Render a standalone OpenShift Build — the k8s division's unit of work.

    Not the rootless-buildah pod the design first reached for. On the cluster this was verified
    against, an ordinary pod cannot build at all: running as uid 0 with `cap_setuid` present,
    `unshare -U` succeeds but writing `/proc/self/uid_map` is denied, so every buildah invocation —
    including `buildah info` — dies in its own re-exec. Neither available SCC helps: `anyuid`
    refuses to add SETUID/SETGID, and `nested-container` requires `hostUsers: false`, under which
    nested user-namespace creation returns ENOSYS. The storage driver is irrelevant; `vfs` fails
    identically. OpenShift's own build controller runs the build with the privileges it needs, and
    every object involved stays namespace-scoped.

    A **standalone Build** rather than a BuildConfig plus a trigger, and a **ConfigMap** source
    rather than a binary upload, because both choices are what make the loop drivable from inside
    the sandbox: `resources_create_or_update` can create this object as it stands, and a failed
    build is retried by creating another one with a corrected `spec.source.dockerfile`. A binary
    source would need a fresh `oc start-build --from-archive` from the host for every iteration,
    which the agent has no way to perform.
    """
    return {
        "apiVersion": "build.openshift.io/v1",
        "kind": "Build",
        "metadata": {"name": build_name, "namespace": spec.namespace},
        "spec": {
            "serviceAccount": spec.service_account,
            "source": {
                "type": "Dockerfile",
                "dockerfile": spec.dockerfile,
                "configMaps": [
                    {"configMap": {"name": spec.context_configmap}, "destinationDir": "."}
                ],
            },
            "strategy": {"type": "Docker", "dockerStrategy": {}},
            "output": {"to": {"kind": "ImageStreamTag", "name": f"{spec.name}:{spec.tag}"}},
            "triggeredBy": [],
        },
    }


def internal_image_ref(spec: BuildObjectsSpec) -> str:
    """Where the built image lands, as the validation pod must name it."""
    return (
        "image-registry.openshift-image-registry.svc:5000/"
        f"{spec.namespace}/{spec.name}:{spec.tag}"
    )


# ── k8s division: the build pod ───────────────────────────────────────────────


@dataclass(frozen=True)
class BuildPodSpec:
    """Inputs for rendering a build pod."""

    name: str
    namespace: str
    image_ref: str
    containerfile: str = "Containerfile"
    context_claim: str = "factory-build-context"
    service_account: str = "factory-build"
    buildah_image: str = "quay.io/buildah/stable:v1.38"
    subid_configmap: str = "factory-build-subid"


def render_build_pod(spec: BuildPodSpec) -> dict[str, Any]:
    """Render the rootless buildah build pod.

    Three things make this work without privilege, and all three are load-bearing:

    - `BUILDAH_ISOLATION=chroot` keeps buildah away from runc, and therefore away from
      `CLONE_NEWUSER`, which is exactly what the sandbox's filter denies. `oci` isolation would need
      the namespace and fail.
    - container storage on an **emptyDir**, which gives native overlay diff and avoids needing
      fuse-overlayfs and a `/dev/fuse` device plugin.
    - subuid/subgid ranges for the build user, mounted from a ConfigMap so they are visible in the
      manifest rather than only implied by the image.

    `SETUID` and `SETGID` are the only capabilities added, and everything else is dropped. There is
    no `privileged`, no `SYS_ADMIN`, and no `hostPath` anywhere — the buildah tutorial's verified
    capability set excludes `cap_sys_admin`, so asking for it would widen the grant for nothing.
    """
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": spec.name,
            "namespace": spec.namespace,
            "labels": {"factory.division": "build"},
        },
        "spec": {
            "restartPolicy": "Never",
            "serviceAccountName": spec.service_account,
            "containers": [
                {
                    "name": "build",
                    "image": spec.buildah_image,
                    "workingDir": BUILD_CONTEXT_PATH,
                    "command": [
                        "buildah",
                        "bud",
                        "--storage-driver",
                        "overlay",
                        "-f",
                        spec.containerfile,
                        "-t",
                        spec.image_ref,
                        ".",
                    ],
                    "env": [
                        {"name": "BUILDAH_ISOLATION", "value": "chroot"},
                        {"name": "STORAGE_DRIVER", "value": "overlay"},
                    ],
                    "securityContext": {
                        "privileged": False,
                        "allowPrivilegeEscalation": False,
                        "runAsNonRoot": True,
                        "capabilities": {"drop": ["ALL"], "add": ["SETUID", "SETGID"]},
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "volumeMounts": [
                        {"name": "containers", "mountPath": CONTAINER_STORAGE_PATH},
                        {"name": "context", "mountPath": BUILD_CONTEXT_PATH},
                        {"name": "subid", "mountPath": "/etc/subuid", "subPath": "subuid"},
                        {"name": "subid", "mountPath": "/etc/subgid", "subPath": "subgid"},
                    ],
                }
            ],
            "volumes": [
                {"name": "containers", "emptyDir": {}},
                {
                    "name": "context",
                    "persistentVolumeClaim": {"claimName": spec.context_claim},
                },
                {"name": "subid", "configMap": {"name": spec.subid_configmap}},
            ],
        },
    }


# Fields whose presence means the manifest is asking for more than the design allows.
FORBIDDEN_MARKERS = ("privileged", "SYS_ADMIN", "hostPath")


def audit_pod(manifest: dict[str, Any]) -> list[str]:
    """Report every way a rendered pod exceeds the design's privilege budget.

    Used to check our own template and to warn about a user-supplied one. It reports rather than
    raises: `--pod-manifest` is an explicit override, and silently rejecting it would be as wrong as
    silently accepting it.
    """
    findings: list[str] = []
    blob = yaml.safe_dump(manifest)
    if "privileged: true" in blob:
        findings.append("privileged: true is set")
    if "SYS_ADMIN" in blob:
        findings.append("SYS_ADMIN capability is requested")
    if "hostPath" in blob:
        findings.append("a hostPath volume is mounted")
    if "BUILDAH_ISOLATION" in blob and "chroot" not in blob:
        findings.append("BUILDAH_ISOLATION is set to something other than chroot")
    return findings


def strategic_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a patch fragment over a manifest, matching list entries by `name`.

    A documented subset of Kubernetes strategic-merge semantics: dictionaries merge recursively, and
    lists whose entries carry a `name` key merge by that name rather than being replaced wholesale.
    That subset is what `--pod-patch` is for — changing one field, such as a memory limit, without
    restating the container. Lists without `name` keys are replaced, which is the conservative
    reading.
    """
    result = copy.deepcopy(base)
    for key, patch_value in patch.items():
        base_value = result.get(key)
        if isinstance(base_value, dict) and isinstance(patch_value, dict):
            result[key] = strategic_merge(base_value, patch_value)
        elif isinstance(base_value, list) and isinstance(patch_value, list):
            result[key] = _merge_named_list(base_value, patch_value)
        else:
            result[key] = copy.deepcopy(patch_value)
    return result


def _merge_named_list(base: list[Any], patch: list[Any]) -> list[Any]:
    if not all(isinstance(i, dict) and "name" in i for i in base + patch):
        return copy.deepcopy(patch)
    merged = {i["name"]: copy.deepcopy(i) for i in base}
    order = [i["name"] for i in base]
    for item in patch:
        name = item["name"]
        if name in merged:
            merged[name] = strategic_merge(merged[name], item)
        else:
            merged[name] = copy.deepcopy(item)
            order.append(name)
    return [merged[n] for n in order]


def load_pod_manifest(path: Path) -> dict[str, Any]:
    """Load a user-supplied manifest, used verbatim."""
    loaded = yaml.safe_load(path.read_text())
    if not isinstance(loaded, dict):
        raise DivisionError(f"{path} does not contain a single YAML mapping")
    return loaded
