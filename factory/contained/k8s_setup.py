"""Cluster prerequisites: `verify` reports, `setup` fixes.

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

import hashlib
import json
import subprocess
import sys

import structlog

from factory.contained.bundle import ROLE_NAME, SCC_ROLEBINDING, render_bundle
from factory.contained.k8s import (
    LABEL_CONTAINED,
    PVC_NAME,
    SECRET_NAME,
    SERVICE_ACCOUNT,
    ClusterError,
    access_review,
    build_api_resources_argv,
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
# (verb, resource, subresource, apiGroup). The subresource is a field of its own rather than a
# "pods/log" string, because that is precisely the distinction `oc auth can-i` loses — see
# `k8s.render_access_review`. The group is explicit for the same class of reason: omitted means the
# *core* group, so a review for `builds` with no group asks about a core resource that does not
# exist and comes back denied, reporting a correct division namespace as missing its permissions.
REQUIRED_SA_VERBS = (
    ("create", "pods", "", ""),
    ("get", "pods", "", ""),
    ("delete", "pods", "", ""),
    ("get", "pods", "log", ""),
)
DIVISION_SA_VERBS = (
    ("create", "builds", "", "build.openshift.io"),
    ("get", "builds", "", "build.openshift.io"),
    ("create", "buildconfigs", "", "build.openshift.io"),
    ("get", "imagestreams", "", "image.openshift.io"),
)


def _run(argv: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        return None


def verify_k8s(
    *, namespace: str | None = None, division: bool = False, probe_inference: bool = True
) -> list[Check]:
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
    if probe_inference:
        checks.append(_inference_check(binary, target, resolve_image()))
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
                    f"factory contained --namespace {namespace}"
                    f"{' --division' if division else ''} bundle | {binary} apply -f -"
                ),
            )
        )
    return checks


def _verb_checks(namespace: str, division: bool) -> list[Check]:
    """SelfSubjectAccessReview for each verb the run needs, asked as the ServiceAccount."""
    wanted = REQUIRED_SA_VERBS + (DIVISION_SA_VERBS if division else ())
    missing = []
    unknown = False
    for verb, resource, subresource, group in wanted:
        allowed = access_review(
            verb, resource, namespace, subresource=subresource, group=group,
            as_service_account=SERVICE_ACCOUNT,
        )
        if allowed is None:
            unknown = True
            continue
        if not allowed:
            missing.append(f"{verb} {resource}{'/' + subresource if subresource else ''}")
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
                f"factory contained --namespace {namespace}"
                f"{' --division' if division else ''} bundle | oc apply -f -"
            ),
        ),
        _no_exec_check(namespace),
    ]


def _no_exec_check(namespace: str) -> Check:
    """`pods/exec` must be **absent** from the ServiceAccount.

    This is the one check that fails when something *succeeds*. The build sidecar holds `oc` and the
    ServiceAccount token and the agent's container holds neither — but that is only a boundary
    because the agent cannot exec into the sidecar. With this verb granted, it can, and the
    separation the whole k8s division rests on is decoration.

    Attaching does not need it: `factory contained attach` runs as *you*, with your kubeconfig.
    """
    granted = access_review(
        "create", "pods", namespace, subresource="exec", as_service_account=SERVICE_ACCOUNT
    )
    if granted is None:
        return Check(
            name="no_pods_exec",
            ok=False,
            detail="could not check whether the ServiceAccount has pods/exec",
            fix=(
                "check that the cluster is reachable and that you may post a SubjectAccessReview: "
                f"oc auth can-i create subjectaccessreviews -n {namespace}"
            ),
        )
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


def _inference_check(binary: str, namespace: str, image: str) -> Check:
    """Can a pod in this namespace actually reach inference? (spec.0 check 6)

    **From inside the cluster, not from here.** A host-side check proves nothing about the pod's
    egress: the laptop has a proxy, a VPN and a working DNS resolver that the namespace may not, and
    a NetworkPolicy the laptop never sees. So this runs one short-lived pod, with the same image and
    the same Secret a real run would use, and asks it to make a single request.

    It is the one check that creates something, and it removes what it creates. That is the trade
    the design makes deliberately: a credentials problem found here fails at launch with a named
    cause, and found any other way it fails inside an agent call, minutes in, looking like a model
    outage.
    """
    # A hash rather than a slice of the namespace: a truncated name can end in a hyphen, which
    # RFC 1123 rejects and which the API server reports as an invalid *value* rather than as a
    # naming mistake. Hashing also keeps two namespaces' probes from colliding.
    pod = f"factory-inference-probe-{hashlib.sha1(namespace.encode()).hexdigest()[:8]}"
    manifest = _probe_pod_manifest(pod, namespace, image)
    try:
        subprocess.run([binary, "delete", "pod", pod, "-n", namespace, "--ignore-not-found"],
                       capture_output=True, text=True, timeout=60)
        created = subprocess.run([binary, "apply", "-n", namespace, "-f", "-"],
                                 input=manifest, capture_output=True, text=True, timeout=60)
        if created.returncode != 0:
            return Check(
                name="inference_from_cluster",
                ok=False,
                detail=f"the probe pod could not be created: {created.stderr.strip()[:160]}",
                fix=f"factory contained --namespace {namespace} bundle | {binary} apply -f -",
            )
        waited = subprocess.run(
            [binary, "wait", f"pod/{pod}", "-n", namespace,
             "--for=jsonpath={.status.phase}=Succeeded", "--timeout=180s"],
            capture_output=True, text=True, timeout=240,
        )
        logs = subprocess.run([binary, "logs", pod, "-n", namespace],
                              capture_output=True, text=True, timeout=60)
        output = (logs.stdout or "").strip()
        ok = waited.returncode == 0 and "PROBE_OK" in output
        return Check(
            name="inference_from_cluster",
            ok=ok,
            detail=(
                "a pod in this namespace reached the configured inference backend"
                if ok
                else "a pod in this namespace could NOT reach inference: "
                     + (output.splitlines()[-1][:200] if output else "the probe produced no output")
            ),
            fix=(
                None if ok else
                f"check the Secret's contents and the namespace's egress. The probe pod's own words "
                f"are the best evidence: {binary} logs {pod} -n {namespace}"
            ),
        )
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired) as exc:
        return Check(
            name="inference_from_cluster",
            ok=False,
            detail=f"the in-cluster inference probe could not be run: {exc}",
            fix=None,
        )
    finally:
        subprocess.run([binary, "delete", "pod", pod, "-n", namespace, "--ignore-not-found",
                        "--wait=false"], capture_output=True, text=True, timeout=60)


def _probe_pod_manifest(name: str, namespace: str, image: str) -> str:
    """One pod, one request, no workspace, no PVC — it must not depend on anything under test.

    The probe deliberately does not use the factory: it curls the backend the Secret configures, so
    a failure means "this namespace cannot reach inference" rather than "something in the factory
    broke". Both matter, and this check owns the first.
    """
    script = (
        'set -e; '
        'if [ -n "$CLAUDE_CODE_USE_VERTEX" ]; then '
        '  url="https://${CLOUD_ML_REGION}-aiplatform.googleapis.com/generateContent"; '
        'else '
        '  url="https://api.anthropic.com/v1/messages"; '
        'fi; '
        'echo "probing $url"; '
        'code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 "$url" || echo 000); '
        'echo "http $code"; '
        # Any HTTP status proves the request left the namespace and was answered. 000 is the one
        # that means it did not — DNS, egress policy, or a proxy the laptop has and the pod lacks.
        '[ "$code" != "000" ] && echo PROBE_OK || { echo "no response — DNS, egress or proxy"; exit 1; }'
    )
    return f"""\
apiVersion: v1
kind: Pod
metadata:
  name: {name}
  namespace: {namespace}
  labels:
    {LABEL_CONTAINED}: "true"
spec:
  restartPolicy: Never
  serviceAccountName: {SERVICE_ACCOUNT}
  securityContext:
    runAsNonRoot: true
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: probe
      image: {image}
      command: ["sh", "-c", {json.dumps(script)}]
      envFrom:
        - secretRef:
            name: {SECRET_NAME}
            optional: true
      securityContext:
        allowPrivilegeEscalation: false
        capabilities:
          drop: ["ALL"]
"""


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
    """The k8s division is OpenShift-only, detected by API presence rather than by `oc`."""
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
    apply_line = (f"  factory contained --namespace {target}"
                  f"{' --division' if division else ''} bundle | {binary} apply -f -")

    # Say the outcome before printing 80 lines of YAML that would otherwise bury it — and check the
    # blocker the user actually has. With no cluster reachable, nothing could be applied whatever
    # they answer, and "About to apply..." would be untrue.
    reachable = _run([binary, "config", "current-context"])
    if reachable is None or reachable.returncode != 0 or not reachable.stdout.strip():
        print(
            f"No cluster is selected, so nothing can be applied to {target} from here.\n"
            f"Log in first (`{binary} login ...`), then re-run. The manifest you will need is "
            "below; you can also hand it to whoever owns the namespace:\n"
            f"{apply_line}\n",
            file=sys.stderr,
        )
        print(manifest)
        return 1

    if not (assume_yes or _confirm_first(interactive, target, binary)):
        print(
            f"Nothing was applied. Apply it yourself, or hand it to whoever owns {target}:\n"
            f"{apply_line}\n",
            file=sys.stderr,
        )
        print(manifest)
        return 1

    print(f"Applying to namespace {target} with your own {binary} credentials:")
    print()
    print(manifest)
    print()

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
            f"namespace:\n{apply_line}",
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
        setup_command=None,
    ))
    return 0 if all(c.ok for c in checks) else 1


def _confirm_first(interactive: bool, namespace: str, binary: str) -> bool:
    """Ask before applying — and when there is nobody to ask, say so once, plainly."""
    if not interactive:
        print(
            "Not a terminal and --yes was not given, so nothing will be applied.",
            file=sys.stderr,
        )
        return False
    print(f"This will create the objects below in namespace {namespace} using your {binary} login.")
    return input("Apply them? [y/N] ").strip().lower() in ("y", "yes")
