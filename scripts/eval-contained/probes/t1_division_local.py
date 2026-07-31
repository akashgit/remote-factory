#!/usr/bin/env python3
# COVERS: C8,C9,C10,C11,C12,C13,C14,C18
"""C8–C14 and C18 — the division's configuration, rendered and inspected.

The division deliberately opens the isolation boundary: a container it launches is not confined by
OpenShell, because builds cannot happen inside the boundary at all. These criteria do not ask
whether that is wise — the spec already decided it — they check that the opening is exactly as
narrow as designed. A bind mount one directory too wide, a build pod that asks for privilege, or an
MCP allowlist with one extra tool are all silent widenings: everything still works, and the boundary
is permanently larger than the design says.

Everything here is rendered rather than provisioned, which is why it is t0/t1. C8 additionally
carries a t2 component — confirming the mount actually landed — so the collector will skip it
unless a live sandbox is available; the record emitted for it here covers only the rendered half.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os  # noqa: E402

from _probe_lib import (  # noqa: E402
    emit,
    factory_bin,
    fresh_dir,
    note,
    probe_record,
    run,
)

# `openshell` is kept on PATH deliberately. With it absent, the bind-mount precondition refuses via
# the "binary missing" branch, which is not the branch C10 is about — the criterion concerns a
# gateway that does not report `enable_bind_mounts`. Keeping the binary reachable exercises the
# live-query branch instead. Proving the third case, a gateway that answers with the flag set false,
# needs a registered gateway and therefore t2.
BASE_ENV = {
    "PATH": f"{os.environ.get('HOME', '')}/.local/bin:/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp/factory-eval-contained/home",
    "LANG": "C",
    "FACTORY_MANAGED_DIRS": "/tmp/factory-eval-contained/managed",
    "FACTORY_VAULT_PATH": "/tmp/factory-eval-contained/vault",
}
DRY = {"FACTORY_OPENSHELL_DRY_RUN": "1"}

EXPECTED_TOOLS = [
    "image_build",
    "container_run",
    "container_logs",
    "container_stop",
    "container_remove",
    "image_list",
]

PATCH_YAML = """\
spec:
  containers:
    - name: build
      resources:
        limits:
          memory: 3Gi
"""

PRIVILEGED_MANIFEST = """\
apiVersion: v1
kind: Pod
metadata:
  name: operator-supplied
spec:
  containers:
    - name: build
      image: quay.io/buildah/stable:v1.38
      securityContext:
        privileged: true
"""


def _invoke(project: Path, extra: list[str], env: dict[str, str]) -> tuple[dict[str, object], dict]:
    capture = run(factory_bin() + ["contained", str(project)] + extra, env=env, cwd=project, timeout=120.0)
    try:
        payload = json.loads(str(capture.get("stdout", "")) or "{}")
    except json.JSONDecodeError:
        payload = {}
    return capture, payload


def main() -> int:
    if not factory_bin():
        note("t1_division_local: no factory entry point")
        return 1

    project = fresh_dir("division_project")
    fresh_dir("home")
    (project / ".gitignore").write_text(".factory/\n")
    (project / ".factory").mkdir()
    (project / ".factory" / "config.json").write_text("{}\n")

    dry = {**BASE_ENV, **DRY}

    # ── bind mount, rendered for each compute driver ──────────────────────────
    podman_cap, podman = _invoke(project, ["--division", "local"], {**dry, "FACTORY_OPENSHELL_DRIVER": "podman"})
    docker_cap, docker = _invoke(project, ["--division", "local"], {**dry, "FACTORY_OPENSHELL_DRIVER": "docker"})

    podman_cfg = podman.get("driver_config") or {}
    docker_cfg = docker.get("driver_config") or {}
    mounts = (podman_cfg.get("podman") or {}).get("mounts") or [{}]
    mount = mounts[0] if mounts else {}
    resolved_project = str(Path(str(podman.get("project_path", project))))

    emit(
        probe_record(
            "C8",
            "t0+t2",
            observations={
                "project_path": resolved_project,
                "mount": mount,
                "source": mount.get("source"),
                "target": mount.get("target"),
                "source_equals_project_exactly": mount.get("source") == resolved_project,
                "source_is_home": mount.get("source") == str(Path.home()),
                "source_is_root": mount.get("source") == "/",
                "source_is_parent_of_project": bool(mount.get("source"))
                and resolved_project.startswith(str(mount.get("source")).rstrip("/") + "/"),
                "note": "rendered half only; confirming the mount landed needs a live sandbox (t2)",
            },
            invocations=[podman_cap],
        )
    )
    emit(
        probe_record(
            "C9",
            "t0",
            observations={
                "podman_gateway_top_level_keys": sorted(podman_cfg),
                "docker_gateway_top_level_keys": sorted(docker_cfg),
                "podman_config": podman_cfg,
                "docker_config": docker_cfg,
            },
            invocations=[podman_cap, docker_cap],
        )
    )

    # ── bind-mount precondition: a real run must refuse ───────────────────────
    # Pointed at a gateway config that has the setting off, because that is the case the criterion
    # is about. `gateway info` does not report `enable_bind_mounts` at all in 0.0.92, so a probe
    # that relies on the live query proves only that the query is silent.
    off_home = Path(tempfile.mkdtemp(prefix="factory-gw-off-"))
    gateway_config = off_home / ".config" / "openshell" / "gateway.toml"
    gateway_config.parent.mkdir(parents=True, exist_ok=True)
    gateway_config.write_text("[openshell.drivers.docker]\nenable_bind_mounts = false\n")
    off_env = dict(BASE_ENV)
    off_env["HOME"] = str(off_home)
    real_cap, _ = _invoke(project, ["--division", "local"], off_env)
    real_out = str(real_cap.get("stderr", "")) + str(real_cap.get("stdout", ""))
    emit(
        probe_record(
            "C10",
            "t1",
            observations={
                "exit_code": real_cap.get("exit_code"),
                "stderr": real_cap.get("stderr"),
                "names_enable_bind_mounts": "enable_bind_mounts" in real_out,
                "provisioned_anything": "sandbox create" in real_out or '"dry_run"' in real_out,
                "openshell_on_path": bool(shutil.which("openshell", path=BASE_ENV["PATH"])),
                "gateway_config": gateway_config.read_text(),
                "note": (
                    "run against a gateway config that sets enable_bind_mounts = false, which is "
                    "the case the criterion names. The setting appears in no API response in "
                    "openshell 0.0.92, so the config file is the only place the answer exists."
                ),
            },
            invocations=[real_cap],
        )
    )

    # ── MCP policy allowlist ──────────────────────────────────────────────────
    endpoints = ((podman.get("division_policy") or {}).get("network_policies") or {}).get(
        "factory_division", {}
    ).get("endpoints") or [{}]
    endpoint = endpoints[0] if endpoints else {}
    rules = endpoint.get("rules") or []
    # Rules are `allow:`-wrapped and key the tool as `tool`. The flat `{method, name}` form the
    # probe used to read is rejected by the gateway at parse time.
    allows = [r.get("allow") or {} for r in rules]
    tool_names = [a.get("tool") for a in allows]
    emit(
        probe_record(
            "C18",
            "t0",
            observations={
                "policy_yaml": podman.get("division_policy_yaml"),
                "endpoint": {k: v for k, v in endpoint.items() if k != "rules"},
                "tool_names": tool_names,
                "expected_tool_names": EXPECTED_TOOLS,
                "extra_tools": sorted(set(tool_names) - set(EXPECTED_TOOLS)),
                "missing_tools": sorted(set(EXPECTED_TOOLS) - set(tool_names)),
                "wildcard_rules": [a for a in allows if "*" in str(a.get("tool", ""))],
                "rules_without_a_tool_name": [r for r in rules if not (r.get("allow") or {}).get("tool")],
                "rules_not_allow_wrapped": [r for r in rules if set(r) != {"allow"}],
                "methods": sorted({str(a.get("method")) for a in allows}),
            },
            invocations=[podman_cap],
        )
    )

    # ── build pod: default, patched, and operator-supplied ────────────────────
    k8s_cap, k8s = _invoke(project, ["--division", "k8s"], dry)
    pod = k8s.get("build_pod") or {}
    pod_blob = json.dumps(pod)
    container = ((pod.get("spec") or {}).get("containers") or [{}])[0]
    env_pairs = {e.get("name"): e.get("value") for e in container.get("env") or []}
    mount_paths = [m.get("mountPath") for m in container.get("volumeMounts") or []]
    volumes = {v.get("name"): sorted(set(v) - {"name"}) for v in (pod.get("spec") or {}).get("volumes") or []}

    emit(
        probe_record(
            "C11",
            "t0",
            observations={
                "audit_findings": k8s.get("build_pod_audit"),
                "security_context": container.get("securityContext"),
                "contains_privileged_true": '"privileged": true' in pod_blob,
                "contains_sys_admin": "SYS_ADMIN" in pod_blob,
                "contains_host_path": "hostPath" in pod_blob,
                "capabilities": (container.get("securityContext") or {}).get("capabilities"),
                "note": "the on-cluster openshift.io/scc annotation assertion needs t3",
            },
            invocations=[k8s_cap],
        )
    )
    emit(
        probe_record(
            "C12",
            "t0",
            observations={
                "buildah_isolation": env_pairs.get("BUILDAH_ISOLATION"),
                "container_storage_mount_present": "/home/build/.local/share/containers" in mount_paths,
                "container_storage_volume_kind": volumes.get("containers"),
                "subuid_mounted": "/etc/subuid" in mount_paths,
                "subgid_mounted": "/etc/subgid" in mount_paths,
                "volume_mounts": mount_paths,
                "volumes": volumes,
            },
            invocations=[k8s_cap],
        )
    )

    patch_file = project / "pod-patch.yaml"
    patch_file.write_text(PATCH_YAML)
    patched_cap, patched = _invoke(
        project, ["--division", "k8s", "--pod-patch", str(patch_file)], dry
    )
    patched_pod = patched.get("build_pod") or {}
    patched_container = ((patched_pod.get("spec") or {}).get("containers") or [{}])[0]
    unchanged = {
        k: (container.get(k) == patched_container.get(k))
        for k in ("image", "command", "env", "securityContext", "volumeMounts", "workingDir")
    }
    emit(
        probe_record(
            "C13",
            "t0",
            observations={
                "patch_applied": PATCH_YAML,
                "patched_resources": patched_container.get("resources"),
                "base_had_resources": container.get("resources"),
                "other_container_fields_unchanged": unchanged,
                "audit_findings_after_patch": patched.get("build_pod_audit"),
                "buildah_isolation_after_patch": {
                    e.get("name"): e.get("value") for e in patched_container.get("env") or []
                }.get("BUILDAH_ISOLATION"),
                "container_count_unchanged": len((patched_pod.get("spec") or {}).get("containers") or [])
                == len((pod.get("spec") or {}).get("containers") or []),
            },
            invocations=[patched_cap],
        )
    )

    manifest_file = project / "pod-manifest.yaml"
    manifest_file.write_text(PRIVILEGED_MANIFEST)
    supplied_cap, supplied = _invoke(
        project, ["--division", "k8s", "--pod-manifest", str(manifest_file)], dry
    )
    supplied_pod = supplied.get("build_pod") or {}
    emit(
        probe_record(
            "C14",
            "t0",
            observations={
                "supplied_manifest": PRIVILEGED_MANIFEST,
                "rendered_pod": supplied_pod,
                "used_verbatim": supplied_pod.get("metadata", {}).get("name") == "operator-supplied",
                "audit_findings": supplied.get("build_pod_audit"),
                "warned_on_stderr": "privilege budget" in str(supplied_cap.get("stderr", "")),
                "exit_code": supplied_cap.get("exit_code"),
                "note": (
                    "a privileged override must warn rather than be silently accepted, and must "
                    "not be silently rewritten either — it is an explicit override"
                ),
            },
            invocations=[supplied_cap],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
