"""Cluster prerequisites: `verify` reports, `setup` fixes (spec §4.0, §4.0a).

**Every failed check carries its fix.** `verify` never reports a bare failure: each one names the
exact command that resolves it — `factory contained bundle | oc apply -f -` for a missing object,
the `oc create secret` line for a missing Secret, `oc project` for a missing context. Where the fix
is not a single command (the cluster has no OpenShift Build API), it says what that means for the
run rather than leaving the user to infer it.

`setup` does not stop at printing the bundle. It resolves the namespace, prints the **full**
manifest it intends to apply, asks, applies it with the user's own `oc` credentials, and re-runs
`verify`. If the user lacks permission to create any object, it degrades to printing the manifest
and the `oc apply` line to hand to whoever owns the namespace — it never partially applies and
reports success.

The credentials Secret stays outside that flow. `setup` prints the `oc create secret` command and
never handles the material.
"""

from __future__ import annotations

import subprocess
import sys

import structlog

from factory.contained.bundle import ROLE_NAME, SCC_ROLEBINDING, render_bundle
from factory.contained.k8s import (
    PVC_NAME,
    SECRET_NAME,
    SERVICE_ACCOUNT,
    ClusterError,
    build_api_resources_argv,
    build_can_i_argv,
    cli_binary,
    resolve_namespace,
)
from factory.contained.prereq import Check, render_checks
from factory.contained.secrets import gitleaks_available
from factory.podman import resolve_image

log = structlog.get_logger()

# The keys a credentials Secret must carry for at least one supported backend.
ANTHROPIC_KEYS = ("ANTHROPIC_API_KEY",)
VERTEX_KEYS = ("CLAUDE_CODE_USE_VERTEX", "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID")

# The verbs the pod's ServiceAccount needs. Checked as the ServiceAccount, not as the user: a
# namespace where *you* can create pods but the pod cannot read its own logs fails on the agent's
# first cluster call, several steps from anything this would otherwise have reported.
REQUIRED_SA_VERBS = (
    ("create", "pods"),
    ("get", "pods"),
    ("delete", "pods"),
    ("get", "pods/log"),
)
DIVISION_SA_VERBS = (
    ("create", "builds.build.openshift.io"),
    ("get", "builds.build.openshift.io"),
    ("get", "imagestreams.image.openshift.io"),
)


def _run(argv: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        return None


def verify_k8s(*, namespace: str | None = None, division: bool = False) -> list[Check]:
    """The cluster prerequisite checks, in the order a user would fix them.

    Nothing here raises: a machine with no `oc` at all must get a list of what is missing, not a
    traceback, exactly as the local checks do.
    """
    checks: list[Check] = []
    try:
        binary = cli_binary()
    except ClusterError as exc:
        return [
            Check(
                name="cluster_cli",
                ok=False,
                detail=str(exc),
                fix="brew install openshift-cli   # or kubectl",
            )
        ]

    context = _run([binary, "config", "current-context"])
    context_name = (
        context.stdout.strip() if context is not None and context.returncode == 0 else ""
    )
    has_context = bool(context_name)
    checks.append(
        Check(
            name="cluster_cli",
            ok=has_context,
            detail=(
                f"{binary}, context {context_name}" if has_context
                else f"{binary} is installed but no current context is selected"
            ),
            fix=None if has_context else f"{binary} login ...  # then `{binary} project <namespace>`",
        )
    )
    if not has_context:
        # Everything below needs a reachable cluster. Reporting eight further failures that all mean
        # "no context" buries the one that matters.
        return checks

    try:
        target = resolve_namespace(namespace)
    except ClusterError as exc:
        checks.append(
            Check(name="namespace", ok=False, detail=str(exc), fix=f"{binary} project <namespace>")
        )
        return checks

    checks.append(_namespace_check(binary, target))
    checks.extend(_object_checks(binary, target, division))
    checks.extend(_verb_checks(target, division))
    checks.append(_secret_check(binary, target))
    checks.append(_image_check())
    checks.append(_gitleaks_check())
    if division:
        checks.extend(_division_checks(target))
    return checks


def _namespace_check(binary: str, namespace: str) -> Check:
    result = _run([binary, "get", "namespace", namespace, "-o", "name"])
    ok = result is not None and result.returncode == 0
    return Check(
        name="namespace",
        ok=ok,
        detail=(
            f"{namespace} exists and is accessible" if ok
            else f"namespace {namespace} does not exist or is not accessible"
        ),
        fix=None if ok else f"{binary} new-project {namespace}   # or ask its owner for access",
    )


_BUNDLE_OBJECTS = (
    ("serviceaccount", SERVICE_ACCOUNT),
    ("role", ROLE_NAME),
    ("rolebinding", ROLE_NAME),
    ("rolebinding", SCC_ROLEBINDING),
    ("pvc", PVC_NAME),
)


def _object_checks(binary: str, namespace: str, division: bool) -> list[Check]:
    checks = []
    for kind, name in _BUNDLE_OBJECTS:
        result = _run([binary, "get", kind, name, "-n", namespace, "-o", "name"])
        ok = result is not None and result.returncode == 0
        checks.append(
            Check(
                name=f"bundle:{kind}/{name}",
                ok=ok,
                detail=f"{kind}/{name} present" if ok else f"{kind}/{name} is missing",
                fix=(
                    None if ok else
                    f"factory contained bundle --namespace {namespace}"
                    f"{' --division' if division else ''} | {binary} apply -f -"
                ),
            )
        )
    return checks


def _verb_checks(namespace: str, division: bool) -> list[Check]:
    """SelfSubjectAccessReview for each verb the run needs, asked as the ServiceAccount."""
    wanted = REQUIRED_SA_VERBS + (DIVISION_SA_VERBS if division else ())
    missing = []
    unknown = False
    for verb, resource in wanted:
        result = _run(build_can_i_argv(verb, resource, namespace, as_service_account=SERVICE_ACCOUNT))
        if result is None:
            unknown = True
            continue
        if result.returncode != 0:
            missing.append(f"{verb} {resource}")
    if unknown:
        return [
            Check(
                name="permissions",
                ok=False,
                detail="the access review could not be run, so permissions are unknown",
                fix=f"check that you can run `oc auth can-i --list -n {namespace}`",
            )
        ]
    ok = not missing
    return [
        Check(
            name="permissions",
            ok=ok,
            detail=(
                f"serviceaccount/{SERVICE_ACCOUNT} has every verb the run needs" if ok
                else f"serviceaccount/{SERVICE_ACCOUNT} cannot: {', '.join(missing)}"
            ),
            fix=(
                None if ok else
                f"factory contained bundle --namespace {namespace}"
                f"{' --division' if division else ''} | oc apply -f -"
            ),
        ),
        _no_exec_check(namespace),
    ]


def _no_exec_check(namespace: str) -> Check:
    """`pods/exec` must be **absent** from the ServiceAccount (spec §6.3, §11).

    This is the one check that fails when something *succeeds*. The build sidecar holds `oc` and the
    ServiceAccount token and the agent's container holds neither — but that is only a boundary
    because the agent cannot exec into the sidecar. With this verb granted, it can, and the
    separation the whole k8s division rests on is decoration.

    Attaching does not need it: `factory contained attach` runs as *you*, with your kubeconfig.
    """
    result = _run(build_can_i_argv("create", "pods/exec", namespace,
                                   as_service_account=SERVICE_ACCOUNT))
    if result is None:
        return Check(
            name="no_pods_exec",
            ok=False,
            detail="could not check whether the ServiceAccount has pods/exec",
            fix=f"oc auth can-i create pods/exec -n {namespace} --as system:serviceaccount:"
                f"{namespace}:{SERVICE_ACCOUNT}",
        )
    granted = result.returncode == 0
    return Check(
        name="no_pods_exec",
        ok=not granted,
        detail=(
            f"serviceaccount/{SERVICE_ACCOUNT} cannot exec into pods, which is what makes the "
            "build sidecar a boundary" if not granted
            else f"serviceaccount/{SERVICE_ACCOUNT} CAN exec into pods. The agent can exec into the "
                 "build sidecar and recover a shell path to the cluster"
        ),
        fix=(
            None if not granted else
            f"remove the pods/exec grant from the roles bound to serviceaccount/{SERVICE_ACCOUNT} "
            f"in {namespace}; the factory's own bundle never grants it"
        ),
    )


def _secret_check(binary: str, namespace: str) -> Check:
    """The Secret must exist and carry a usable backend's keys — its *keys*, never its values."""
    result = _run([binary, "get", "secret", SECRET_NAME, "-n", namespace,
                   "-o", "jsonpath={.data}"])
    create_line = (
        f"{binary} create secret generic {SECRET_NAME} -n {namespace} "
        "--from-literal=ANTHROPIC_API_KEY=..."
    )
    if result is None or result.returncode != 0:
        return Check(
            name="credentials_secret",
            ok=False,
            detail=f"secret/{SECRET_NAME} is missing from {namespace}",
            fix=create_line,
        )
    keys = _keys_of(result.stdout)
    if set(ANTHROPIC_KEYS) <= keys:
        return Check(name="credentials_secret", ok=True,
                     detail=f"secret/{SECRET_NAME} carries the Anthropic API key")
    if set(VERTEX_KEYS) <= keys:
        return Check(name="credentials_secret", ok=True,
                     detail=f"secret/{SECRET_NAME} carries the Vertex configuration")
    return Check(
        name="credentials_secret",
        ok=False,
        detail=(
            f"secret/{SECRET_NAME} exists but carries none of the supported backends' keys "
            f"(has: {', '.join(sorted(keys)) or 'nothing'})"
        ),
        fix=create_line,
    )


def _keys_of(raw: str) -> set[str]:
    import json

    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return set()
    return set(data) if isinstance(data, dict) else set()


def _image_check() -> Check:
    """The image is a *reference* check here, not a presence one.

    Whether the cluster can pull it is answered by the pod, and answered properly: a host-side
    `podman pull` proves nothing about a cluster's registry access, and reporting it as if it did is
    worse than not checking.
    """
    reference = resolve_image()
    return Check(
        name="runtime_image",
        ok=True,
        detail=f"{reference} (multi-arch; the cluster pulls the amd64 manifest, this laptop arm64)",
    )


def _gitleaks_check() -> Check:
    available = gitleaks_available()
    return Check(
        name="secret_scanner",
        ok=available,
        detail=(
            "gitleaks present; workspaces are scanned before they leave this machine" if available
            else "gitleaks is not installed, so uploads will proceed UNSCANNED with a warning"
        ),
        fix=None if available else "brew install gitleaks",
    )


def _division_checks(namespace: str) -> list[Check]:
    """The k8s division is OpenShift-only, detected by API presence rather than by `oc` (spec §6)."""
    result = _run(build_api_resources_argv("build.openshift.io"))
    present = result is not None and result.returncode == 0 and "builds" in result.stdout
    return [
        Check(
            name="build_api",
            ok=present,
            detail=(
                "build.openshift.io is served by this cluster" if present
                else "this cluster does not serve build.openshift.io, so --target k8s --division "
                     "cannot work here"
            ),
            fix=(
                None if present else
                "run without --division (the factory still runs; it just cannot build images), or "
                "use an OpenShift cluster. Plain-Kubernetes builds are out of scope by decision: "
                "rootless buildah, kaniko and buildkit all need a uid_map write these nodes deny."
            ),
        )
    ]


def setup_k8s(
    *,
    namespace: str | None,
    division: bool,
    interactive: bool,
    assume_yes: bool = False,
) -> int:
    """Leave the namespace able to run factory pods, or say exactly what is missing."""
    try:
        binary = cli_binary()
        target = resolve_namespace(namespace)
    except ClusterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    manifest = render_bundle(namespace=target, division=division, image=resolve_image())
    print(f"About to apply the following to namespace {target} with your own {binary} credentials:")
    print()
    print(manifest)
    print()

    if not (assume_yes or _confirm(interactive)):
        print("Nothing was applied. Hand the manifest above to whoever owns the namespace:")
        print(f"  factory contained bundle --namespace {target}"
              f"{' --division' if division else ''} | {binary} apply -f -")
        return 1

    try:
        result = subprocess.run(
            [binary, "apply", "-n", target, "-f", "-"],
            input=manifest, capture_output=True, text=True, timeout=180,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"Error: applying the bundle failed: {exc}", file=sys.stderr)
        return 1
    print(result.stdout.strip())
    if result.returncode != 0:
        # Never "partially applied and reported success": the verify below is what the user reads,
        # and it is run either way so a partial apply is visible as the objects that are missing.
        print(f"Applying the bundle failed: {result.stderr.strip()}", file=sys.stderr)
        print(
            "If this is a permissions problem, hand the manifest above to whoever owns the "
            f"namespace:\n  factory contained bundle --namespace {target}"
            f"{' --division' if division else ''} | {binary} apply -f -",
            file=sys.stderr,
        )

    print(
        f"\nThe credentials Secret is yours to create — the factory never handles the material:\n"
        f"  {binary} create secret generic {SECRET_NAME} -n {target} "
        "--from-literal=ANTHROPIC_API_KEY=...\n"
    )
    checks = verify_k8s(namespace=target, division=division)
    print(render_checks(
        checks,
        ready_command=f"factory contained --target k8s --namespace {target} -- ceo <path>",
    ))
    return 0 if all(c.ok for c in checks) else 1


def _confirm(interactive: bool) -> bool:
    if not interactive:
        print(
            "Not a terminal, and --yes was not given: nothing will be applied.",
            file=sys.stderr,
        )
        return False
    return input("Apply it? [y/N] ").strip().lower() in ("y", "yes")
