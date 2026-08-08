"""The cluster container-manufacturing plane — `--target k8s --division`.

OpenShift only, detected by **API presence** rather than by the `oc` binary: a cluster is not
OpenShift because someone installed a CLI, and the refusal has to name the reason at launch rather
than after a Build that will never be admitted.

**Builds go through OpenShift `Build` objects.** The platform's build controller holds the
privileges OpenShift reserves for building. Rootless buildah, kaniko and buildkit all depend on the
`uid_map` write these nodes deny — probed to the bottom, and not a manifest problem. Output goes to
the cluster-internal registry; the validation pod pulls from
`image-registry.openshift-image-registry.svc:5000` and push credentials stay with the build service
account.

**The agent reaches the cluster only through MCP.** `kubernetes-mcp-server` runs inside the pod over
stdio, and `oc` is not in the image — which is what makes the tool allowlist a boundary rather than
a decoration. Unlike the local division, this boundary is enforced by RBAC and by the absence of any
shell path to the cluster, rather than by a filter the agent's own process could bypass.

**The build context reaches the Build through a sidecar.** A ConfigMap-carried context has a ~700KB
ceiling that forces a wheel-only build; a sidecar sharing the PVC has none. The sidecar is a
*separate container* — never a process beside the agent — and it is the only holder of `oc` and the
ServiceAccount token. That separation is a boundary only while the Role excludes `pods/exec`; with
that verb the agent execs into the sidecar and recovers the shell. `k8s_setup._no_exec_check`
asserts its absence, and it is the one check that fails when something succeeds.
"""

from __future__ import annotations

import json
import shlex

from factory.contained.k8s import (
    LABEL_RUN,
    REQUEST_DIR,
    RESULT_DIR,
    build_api_resources_argv,
    sidecar_command,
)
from factory.contained.k8s import sweep_argv as _sweep_argv

# `REQUEST_DIR`, `RESULT_DIR` and `sidecar_command` are re-exported from `k8s`: they describe the
# pod spec, which that module owns, and the file drop below is their other half.
INTERNAL_REGISTRY = "image-registry.openshift-image-registry.svc:5000"

MCP_CLUSTER_SERVER = "kubernetes"
MCP_BUILD_SERVER = "factory-build"

DIVISION_BRIEF_PATH = ".factory/division/README.md"

DIVISION_BRIEF = """\
# Cluster division — you can build images and validate them

This run has the cluster container-manufacturing plane enabled. **These are capabilities you
already have, not things to build.** Do not write a CLI wrapper, and do not look for `oc` — it is
deliberately not in this image.

## The tools

- `mcp__{build_server}__start_build(dockerfile, tag)` — submit a build of this workspace.
- `mcp__{cluster_server}__*` — read the cluster: list pods, read logs, create and delete the
  validation pods you need. Namespace-scoped, and the namespace is already selected.

## The loop

1. **submit** — `start_build` with the Containerfile's path **relative to your project directory**
   and a tag. Your project directory is the build context, so a relative `COPY` resolves the way it
   would on a laptop. A sidecar container reads the context off the shared volume and starts an
   OpenShift `Build`; you never touch the build machinery yourself.
2. **read the result** — the call blocks until the build finishes and returns the build log plus
   whether it succeeded. Success means the Build reached `Complete`, not merely that a command
   exited zero.
3. **fix** — a build that fails tells you why in that log and nowhere else. Edit the Containerfile
   or the source and resubmit; resubmitting is cheap and is the intended way to iterate.
4. **validate** — when the build succeeds, run a **validation pod** on the resulting image and read
   its logs. A build that succeeds is not evidence that the image runs.

## What is true about this environment

- Images land in the cluster-internal registry at `{registry}`. Reference them from a validation
  pod by their ImageStream tag; push credentials stay with the build service account and never
  reach you.
- You may create **validation pods only** — run a pod on an image you built, read its logs, delete
  it. No Deployments, Services, ConfigMaps, Secrets or RBAC.
- **Label every pod you create `{run_label}: {run_name}`.** That label is how the run sweeps up
  after itself; a pod without it survives the run and is nobody's to clean up.
- You cannot exec into other pods. That is deliberate, and it is what keeps the build sidecar a
  boundary rather than a formality.
"""


def openshift_available(runner=None) -> bool:
    """Whether this cluster serves the OpenShift Build API.

    Detected by API presence, not by the `oc` binary: `oc` against a vanilla cluster works
    fine for everything except the one thing the division needs.
    """
    import subprocess

    run = runner or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=60))
    try:
        result = run(build_api_resources_argv("build.openshift.io"))
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "builds" in (result.stdout or "")


def start_build_server_source() -> str:
    """The one-tool stdio MCP server the factory ships, `start_build(dockerfile, tag)`.

    Written to the workspace and registered alongside `kubernetes-mcp-server`. It is a *file drop*,
    not a cluster client: it writes a request onto the shared volume and polls for the sidecar's
    result. That is the whole interface — the agent can ask for a build and read what happened, and
    has no route to the cluster credentials that perform it.

    stdlib only, and no imports from the factory package: it runs as its own process inside the
    runtime image, and a dependency on the installed factory would make the division's tool surface
    fail whenever the wheel moved.
    """
    return f'''\
#!/usr/bin/env python3
"""start_build — a one-tool stdio MCP server.

Writes a build request onto the volume the build sidecar watches, then polls for its result. It
holds no credentials and speaks to no cluster: the sidecar is the only thing that does.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

REQUEST_DIR = {REQUEST_DIR!r}
RESULT_DIR = {RESULT_DIR!r}
TIMEOUT = 1800

TOOL = {{
    "name": "start_build",
    "description": (
        "Build a container image from this workspace using the cluster's build plane. "
        "Returns the build log and whether it succeeded. Iterate by fixing the Containerfile "
        "and calling this again."
    ),
    "inputSchema": {{
        "type": "object",
        "properties": {{
            "dockerfile": {{
                "type": "string",
                "description": "Path to the Containerfile, relative to the project directory you are working in",
            }},
            "tag": {{
                "type": "string",
                "description": "Image tag to build, e.g. 'my-app'",
            }},
        }},
        "required": ["dockerfile", "tag"],
    }},
}}


def start_build(dockerfile: str, tag: str) -> str:
    os.makedirs(REQUEST_DIR, exist_ok=True)
    os.makedirs(RESULT_DIR, exist_ok=True)
    name = uuid.uuid4().hex[:12]
    with open(os.path.join(REQUEST_DIR, name + ".json"), "w") as handle:
        json.dump({{"dockerfile": dockerfile, "tag": tag}}, handle)

    status_path = os.path.join(RESULT_DIR, name + ".status")
    log_path = os.path.join(RESULT_DIR, name + ".log")
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        if os.path.exists(status_path):
            with open(status_path) as handle:
                status = handle.read().strip()
            log = ""
            if os.path.exists(log_path):
                with open(log_path, errors="replace") as handle:
                    log = handle.read()
            verdict = "succeeded" if status == "0" else "FAILED (exit " + status + ")"
            return "Build " + verdict + "\\n\\n" + log[-20000:]
        time.sleep(2)
    return (
        "Timed out after " + str(TIMEOUT) + "s waiting for the build sidecar. It may not be "
        "running: check the pod's build-sidecar container."
    )


def respond(message):
    sys.stdout.write(json.dumps(message) + "\\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            respond({{
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {{
                    "protocolVersion": "2025-06-18",
                    "capabilities": {{"tools": {{}}}},
                    "serverInfo": {{"name": "factory-build", "version": "1"}},
                }},
            }})
        elif method == "tools/list":
            respond({{"jsonrpc": "2.0", "id": request_id, "result": {{"tools": [TOOL]}}}})
        elif method == "tools/call":
            params = request.get("params", {{}})
            arguments = params.get("arguments", {{}})
            try:
                text = start_build(arguments["dockerfile"], arguments["tag"])
            except Exception as exc:                       # noqa: BLE001 - reported to the caller
                respond({{
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {{
                        "content": [{{"type": "text", "text": "start_build failed: " + str(exc)}}],
                        "isError": True,
                    }},
                }})
                continue
            respond({{
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {{"content": [{{"type": "text", "text": text}}]}},
            }})
        elif request_id is not None:
            respond({{
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {{"code": -32601, "message": "method not found: " + str(method)}},
            }})


if __name__ == "__main__":
    main()
'''


SERVER_PATH = ".factory/division/start_build_server.py"


def mcp_config(namespace: str) -> dict[str, object]:
    """Register both servers for the agent inside the pod.

    `kubernetes-mcp-server` is given the namespace and an explicit in-cluster credential source, so
    it never auto-detects a provider that wants an interactive login — an agent that silently sits
    in a needs-auth state looks identical to one whose tools are broken.
    """
    return {
        "mcpServers": {
            MCP_CLUSTER_SERVER: {
                "command": "npx",
                "args": [
                    "-y", "kubernetes-mcp-server@latest",
                    "--namespace", namespace,
                    "--disable-destructive",
                ],
                "env": {"KUBECONFIG": ""},
            },
            MCP_BUILD_SERVER: {
                "command": "python3",
                "args": [SERVER_PATH],
            },
        }
    }


def division_files(namespace: str, run_name: str) -> dict[str, str]:
    """The files the pod writes next to the project before the factory starts."""
    return {
        SERVER_PATH: start_build_server_source(),
        DIVISION_BRIEF_PATH: DIVISION_BRIEF.format(
            build_server=MCP_BUILD_SERVER,
            cluster_server=MCP_CLUSTER_SERVER,
            registry=INTERNAL_REGISTRY,
            run_label=LABEL_RUN,
            run_name=run_name,
        ),
    }


# Re-exported so the division's own tests and brief refer to one sweep, not two. The implementation
# lives in `k8s.py` because "delete what this run labelled" is a lifecycle concern that must keep
# happening whether or not a division was ever enabled.
sweep_argv = _sweep_argv


def registration_json(namespace: str) -> str:
    """The `.mcp.json` payload, for tests and for the dry-run rendering."""
    return json.dumps(mcp_config(namespace), sort_keys=True)


def quoted_sidecar_command() -> str:
    """The sidecar command, shell-quoted — used where it is embedded in another command line."""
    return shlex.quote(sidecar_command())
