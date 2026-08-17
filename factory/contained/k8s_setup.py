"""Cluster prerequisites: `verify` reports, `setup` fixes.

**Every failed check carries its fix.** `verify` never reports a bare failure: each one names the
exact command that resolves it — `factory contained bundle | oc apply -f -` for a missing object,
the `oc create secret` line for a missing Secret, `oc project` for a missing context. Where the fix
is not a single command (the cluster has no OpenShift Build API), it says what that means for the
run rather than leaving the user to infer it.

`setup` does not stop at printing the bundle. It settles the namespace — creating it if you ask —
establishes what is already in it, then walks the objects that are missing or wrong one at a time,
applying each **at the moment you accept it** with your own `oc` credentials, and ends in `verify`.

Applying per object rather than batching at the end is what keeps the report honest: stopping
halfway leaves the cluster genuinely changed, and the summary says how much. If a permission is
missing, the object that failed is named and the walk carries on — `verify` then reports exactly
what is absent, so a partial apply is never dressed up as success.

The credentials Secret stays outside that flow. `setup` prints the `oc create secret` command and
never handles the material.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable

import structlog

from factory.contained import style
from factory.contained.bundle import BundleObject, bundle_objects, render_bundle
from factory.contained.k8s import (
    ADC_SECRET_KEY,
    LABEL_CONTAINED,
    SECRET_NAME,
    SERVICE_ACCOUNT,
    ClusterContext,
    ClusterError,
    access_review,
    build_api_resources_argv,
    cli,
    cli_binary,
    active_context,
    cluster_context,
    current_namespace,
    list_contexts,
    resolve_namespace,
    set_active_context,
    use_context,
)
from factory.contained.k8s_review import inspect_objects, render_summary, walk
from factory.contained.prereq import Check, format_check, summary_line
from factory.contained.secrets import gitleaks_available
from factory.podman import resolve_image

log = structlog.get_logger()

# The cluster half of `setup`: choose a namespace, review-and-apply object by object, verify.
# Three rather than four because applying is no longer a step of its own — each object is applied
# at the moment it is accepted, so there is nothing left to batch afterwards.
_K8S_STEPS = 3

# The keys a credentials Secret must carry for at least one supported backend.
ANTHROPIC_KEYS = ("ANTHROPIC_API_KEY",)
# The three configuration variables *and* the credential file. The credential is the point: the
# first three only say which endpoint to talk to, so a Secret carrying just those was reported as
# "carries the Vertex configuration" while holding nothing that could authenticate.
VERTEX_KEYS = (
    "CLAUDE_CODE_USE_VERTEX", "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID", ADC_SECRET_KEY,
)

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
    *,
    namespace: str | None = None,
    division: bool = False,
    probe_inference: bool = True,
    on_check: Callable[[Check], None] | None = None,
) -> list[Check]:
    """The cluster prerequisite checks, in the order a user would fix them.

    Nothing here raises: a machine with no `oc` at all must get a list of what is missing, not a
    traceback, exactly as the local checks do.

    `on_check` is called with each result the moment it is known. Some of these are slow — the
    access reviews are a round trip each and the inference probe launches a pod and waits on it for
    up to three minutes — so a caller that only prints at the end shows a blank screen for the
    duration, which is indistinguishable from a hang. Passing `on_check` is how the caller streams.
    """
    checks: list[Check] = []

    def record(*new: Check) -> None:
        for check in new:
            checks.append(check)
            if on_check is not None:
                on_check(check)

    try:
        binary = cli_binary()
    except ClusterError as exc:
        # Through `record` like every other result: a streaming caller prints only the summary at
        # the end, so a check that skips this is a check the user never sees.
        record(
            Check(
                name="cluster_cli",
                ok=False,
                detail=str(exc),
                fix="brew install openshift-cli   # or kubectl",
            )
        )
        return checks

    context = _context_check(binary)
    record(context)
    if not context.ok:
        # Everything below needs a reachable cluster. Reporting eight further failures that all mean
        # "no context" buries the one that matters.
        return checks

    try:
        target = resolve_namespace(namespace)
    except ClusterError as exc:
        record(
            Check(name="namespace", ok=False, detail=str(exc), fix=f"{binary} project <namespace>")
        )
        return checks

    record(_namespace_check(binary, target))
    record(*_object_checks(binary, target, division))
    record(*_verb_checks(target, division))
    secret = _secret_check(binary, target)
    record(secret)
    record(_image_check())
    if probe_inference:
        record(_inference_result(binary, target, secret, announce=on_check is not None))
    record(_gitleaks_check())
    if division:
        record(*_division_checks(target))
    return checks


def _context_check(binary: str) -> Check:
    """Is a cluster selected, and which one? The first check, because everything else needs it."""
    context = _run(cli(binary, "config", "current-context"))
    name = context.stdout.strip() if context is not None and context.returncode == 0 else ""
    if not name:
        return Check(
            name="cluster_cli",
            ok=False,
            detail=f"{binary} is installed but no current context is selected",
            fix=f"{binary} login ...  # then `{binary} project <namespace>`",
        )
    # The server, not just the context name. A context called `dev` says nothing about which
    # cluster it reaches, and this check is where a user confirms they are pointed at the right one.
    # Read only once a context exists: with none selected there is nothing for it to report, and
    # asking costs a second `config view` to be told so.
    server = cluster_context().server
    return Check(
        name="cluster_cli",
        ok=True,
        detail=f"{binary}, context {name}" + (f", server {server}" if server else ""),
    )


def _inference_result(binary: str, namespace: str, secret: Check, *, announce: bool) -> Check:
    """The in-cluster probe, or the reason it was not worth running."""
    if not secret.ok:
        # The probe pod mounts that Secret to authenticate. Without it the pod cannot succeed, and
        # running it anyway means waiting the full 180-second timeout to be told what the check
        # above already said — which is exactly what a freshly prepared namespace hits, because
        # creating the Secret is the step deliberately left to the user.
        return Check(
            name="inference_from_cluster",
            ok=False,
            detail=(
                "not attempted — the credentials Secret is missing, so a probe pod could not "
                "authenticate. Create it, then re-run verify."
            ),
            fix=secret.fix,
        )
    if announce:
        # Announced rather than merely slow: this one creates a pod and waits on it, and
        # "nothing on screen for three minutes" is the report people read as a crash.
        print(style.note(
            "Checking inference from inside the namespace — this launches a short-lived pod "
            "and waits for it, up to three minutes."
        ))
    return _inference_check(binary, namespace, resolve_image())


def _namespace_check(binary: str, namespace: str) -> Check:
    result = _run(cli(binary, "get", "namespace", namespace, "-o", "name"))
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


def _object_checks(binary: str, namespace: str, division: bool) -> list[Check]:
    """One check per bundle object, taken from the bundle itself.

    Derived rather than listed again: a second hardcoded list is how `verify` comes to check four
    objects while `setup` applies five, and the missing one is only found by a run that fails.
    """
    checks = []
    for obj in bundle_objects(namespace=namespace, division=division):
        kind, name = obj.kind, obj.name
        result = _run(cli(binary, "get", kind, name, "-n", namespace, "-o", "name"))
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
    result = _run(cli(binary, "get", "secret", SECRET_NAME, "-n", namespace,
                      "-o", "jsonpath={.data}"))
    create_line = (
        f"{binary} create secret generic {SECRET_NAME} -n {namespace} \\\n"
        f"      --from-literal=ANTHROPIC_API_KEY=...\n"
        f"  or, for Vertex:\n"
        f"      {binary} create secret generic {SECRET_NAME} -n {namespace} \\\n"
        f"      --from-literal=CLAUDE_CODE_USE_VERTEX=1 \\\n"
        f"      --from-literal=CLOUD_ML_REGION=<region> \\\n"
        f"      --from-literal=ANTHROPIC_VERTEX_PROJECT_ID=<project> \\\n"
        f"      --from-file={ADC_SECRET_KEY}=$HOME/.config/gcloud/"
        f"application_default_credentials.json"
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
        subprocess.run(cli(binary, "delete", "pod", pod, "-n", namespace, "--ignore-not-found"),
                       capture_output=True, text=True, timeout=60)
        created = subprocess.run(cli(binary, "apply", "-n", namespace, "-f", "-"),
                                 input=manifest, capture_output=True, text=True, timeout=60)
        if created.returncode != 0:
            return Check(
                name="inference_from_cluster",
                ok=False,
                detail=f"the probe pod could not be created: {created.stderr.strip()[:160]}",
                fix=f"factory contained --namespace {namespace} bundle | {binary} apply -f -",
            )
        waited = subprocess.run(
            cli(binary, "wait", f"pod/{pod}", "-n", namespace,
                "--for=jsonpath={.status.phase}=Succeeded", "--timeout=180s"),
            capture_output=True, text=True, timeout=240,
        )
        logs = subprocess.run(cli(binary, "logs", pod, "-n", namespace),
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
        subprocess.run(cli(binary, "delete", "pod", pod, "-n", namespace, "--ignore-not-found",
                           "--wait=false"), capture_output=True, text=True, timeout=60)


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
    except ClusterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(style.section("Cluster and namespace", step=1, total=_K8S_STEPS))
    chosen_context = _choose_context(interactive)
    if chosen_context is _ABORT:
        print("\nStopped. Nothing was applied.", file=sys.stderr)
        return 1
    if isinstance(chosen_context, str):
        # Pin every later cluster command to it. Nothing about the user's kubeconfig changes.
        set_active_context(chosen_context)

    try:
        target = _choose_namespace(
            namespace, interactive=interactive, binary=binary, assume_yes=assume_yes
        )
    except ClusterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if target is None:
        print("\nStopped. Nothing was applied.", file=sys.stderr)
        return 1

    manifest = render_bundle(namespace=target, division=division, image=resolve_image())
    apply_line = (f"  factory contained --namespace {target}"
                  f"{' --division' if division else ''} bundle | {binary} apply -f -")

    # Say the outcome before printing 80 lines of YAML that would otherwise bury it — and check the
    # blocker the user actually has. With no cluster reachable, nothing could be applied whatever
    # they answer, and "About to apply..." would be untrue.
    reachable = _run(cli(binary, "config", "current-context"))
    if reachable is None or reachable.returncode != 0 or not reachable.stdout.strip():
        print(
            f"No cluster is selected, so nothing can be applied to namespace {target} from here.\n"
            f"Log in first (`{binary} login ...`), then re-run. The manifest you will need is "
            "below; you can also hand it to whoever owns the namespace:\n"
            f"{apply_line}\n",
            file=sys.stderr,
        )
        print(manifest)
        return 1

    # Establish the current state before asking anything. A wall of YAML the user cannot relate to
    # their own namespace offers a choice between "yes" and "no" to an unknown — what they need to
    # decide is, per object that is not already right, what it is for and what would change.
    print(style.section("Review and apply", step=2, total=_K8S_STEPS))
    objects = bundle_objects(namespace=target, division=division)
    states = inspect_objects(objects, target, binary)
    print(render_summary(states, target, cluster_context().server))

    # There is no separate apply step: each object is applied the moment it is accepted. Batching
    # them until the end would mean a user who answers yes twice and then stops is told nothing was
    # applied, which is false — and the immediate `role/... configured` is also the feedback that
    # makes the next decision an informed one.
    if not (interactive or assume_yes):
        print(
            "Not a terminal and --yes was not given, so nothing was applied.\n"
            f"Apply it yourself, or hand it to whoever owns {target}:\n"
            f"{apply_line}\n",
            file=sys.stderr,
        )
        return 1

    outcome = walk(
        states, target, binary,
        interactive=interactive,
        assume_yes=assume_yes,
        apply=lambda obj: _apply_object(obj, target, binary),
    )
    if outcome.failed:
        print(
            "If this is a permissions problem, hand the bundle to whoever owns the namespace:\n"
            f"{apply_line}",
            file=sys.stderr,
        )

    if outcome.aborted:
        # Stopping means stopping. Following `q` with a ten-check verification sweep against the
        # cluster is both slow and the opposite of what the key was pressed for; the command that
        # does it is named instead. Non-zero either way, because the namespace is deliberately
        # half-prepared and a script must not read that as success.
        print(style.line(
            "Run "
            + style.bold(f"factory contained --target k8s --namespace {target} verify")
            + " when you want the full picture."
        ))
        return 1

    return _finish(binary, target, division, interactive)


def _apply_object(obj: BundleObject, namespace: str, binary: str) -> tuple[bool, str]:
    """Apply one object with the user's own credentials. Never raises.

    One `apply` per object rather than one for the batch: the walk needs to report each result
    beside the decision that caused it, and a single combined apply can only report a total.
    """
    argv = cli(binary, "apply", "-n", namespace, "-f", "-")
    try:
        result = subprocess.run(
            argv, input=obj.manifest, capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode == 0:
        return True, (result.stdout or "").strip()
    detail = (result.stderr or "").strip().splitlines()
    return False, detail[0][:200] if detail else "no detail given"


def _finish(binary: str, target: str, division: bool, interactive: bool = False) -> int:
    """The Secret reminder and the verify pass — reached whether or not anything was applied.

    A run where every object was already correct still has to end in `verify`'s two states, because
    "nothing to apply" is not the same claim as "this namespace is ready".
    """
    print(
        f"\nThe credentials Secret is yours to create — the factory never handles the material:\n"
        f"  {binary} create secret generic {SECRET_NAME} -n {target} "
        "--from-literal=ANTHROPIC_API_KEY=...\n"
    )
    print(style.section("Verify", step=_K8S_STEPS, total=_K8S_STEPS))
    # Streamed, not collected: the access reviews and the in-cluster inference probe take minutes
    # between them, and a step that prints nothing until they all finish is read as a hang — which
    # is exactly how it was reported.
    checks = verify_k8s(
        namespace=target, division=division, on_check=lambda c: print(format_check(c), flush=True)
    )
    print()
    pinned = active_context()
    ready = f"factory contained --target k8s --namespace {target}"
    if pinned:
        # The ready-to-run command carries the context, so copying it reaches the cluster that was
        # just prepared rather than whichever one happens to be current later.
        ready += f" --context {pinned}"
    print(summary_line(checks, ready_command=f"{ready} -- ceo <path>", setup_command=None))
    if pinned:
        _offer_default_switch(pinned, interactive)
    return 0 if all(c.ok for c in checks) else 1


def _choose_context(interactive: bool) -> str | None | object:
    """Which cluster to prepare. Returns a context name, None to keep the current one, or ABORT.

    A kubeconfig routinely holds several clusters and `oc config use-context` is the only way most
    people know to move between them — which means picking the wrong one here is a `Ctrl-C`, a
    context switch, and a restart. Offering the list costs one question and removes that loop.

    Whatever is chosen is applied with `--context` on every later command rather than by rewriting
    the kubeconfig: choosing where *this* run goes must not silently change where the user's next
    unrelated `oc get pods` goes. Switching the default is offered separately, afterwards.
    """
    contexts = list_contexts()
    current = cluster_context().context
    if not interactive or len(contexts) < 2:
        # Nothing to choose between — and on a machine with one context, asking is noise.
        return None
    _print_contexts(contexts, current)
    return _ask_context(contexts, current)


def _print_contexts(contexts: list[ClusterContext], current: str | None) -> None:
    """The numbered list, with the server under each name and the current one marked.

    The server is what distinguishes them: context names are local labels a person chose, and two
    of them saying `dev` and `dev-2` do not say which cluster either one reaches.
    """
    print()
    print(style.line("Clusters in your kubeconfig:"))
    print()
    for index, entry in enumerate(contexts, start=1):
        marker = style.paint(" (current)", "green") if entry.context == current else ""
        print(f"   {style.bold(str(index))}) {style.value(entry.context or '?')}{marker}")
        if entry.server:
            print(f"      {style.dim(entry.server)}")
    print()


def _ask_context(contexts: list[ClusterContext], current: str | None) -> str | None | object:
    """Ask until an answer names one of `contexts`. A context name, or ABORT for Escape."""
    default = str(next(
        (i for i, e in enumerate(contexts, start=1) if e.context == current), 1
    ))
    while True:
        answer = style.read_line("Which cluster?", default)
        if answer is None:
            return _ABORT
        choice = answer or default
        if choice.isdigit() and 1 <= int(choice) <= len(contexts):
            return contexts[int(choice) - 1].context
        # A name is accepted as well as a number: people paste context names.
        named = next((e for e in contexts if e.context == choice), None)
        if named is not None:
            return named.context
        print(f"Pick a number between 1 and {len(contexts)}, or type a context name.",
              file=sys.stderr)


def _offer_default_switch(name: str, interactive: bool) -> None:
    """After a run against a non-default context, offer to make it the default — or say how.

    Not done implicitly. Every later `factory contained --target k8s` command resolves the cluster
    the same way, so a namespace prepared here and a run started tomorrow would go to different
    clusters unless one of the two happens; being told which is the point.
    """
    if cluster_context().context == name:
        return
    binary = cli_binary()
    switch = f"{binary} config use-context {name}"
    print()
    print(style.line(
        f"This prepared {style.value(name)}, which is not your current context. Later "
        "`factory contained` commands use your current one unless you pass --context."
    ))
    if not interactive:
        print(style.line(f"Switch with:  {style.bold(switch)}"))
        return
    answer = style.confirm(f"Make {style.value(name)} your default context now?", default=False)
    if not answer:
        print(style.line(f"Left alone. Switch later with:  {style.bold(switch)}"))
        return
    switched, detail = use_context(name)
    if switched:
        print(style.line(style.paint(detail or f"Now using {name}.", "green")))
    else:
        print(style.line(style.paint(f"Could not switch: {detail}", "red")))
        print(style.line(f"Do it yourself with:  {style.bold(switch)}"))


def _print_context(current: str | None) -> None:
    """Say which cluster, as whom, before asking anything about it.

    A namespace name alone identifies nothing — `default` exists on every cluster anyone has ever
    logged into — so the API server URL is the field that actually answers "am I about to apply
    RBAC to the right place?". Degrades one field at a time: an unreadable kubeconfig prints the
    namespace it already knows rather than nothing at all.
    """
    context = cluster_context()
    if context.server:
        print(style.field("Cluster", context.server))
    if context.user:
        print(style.field("User", context.user))
    if context.context:
        print(style.field("Context", context.context))
    if current:
        print(style.field("Namespace", f"{style.value(current)}  {style.dim('(the default below)')}"))
    else:
        print(style.field("Namespace", style.dim("none — your context selects no namespace")))


PRESENT, ABSENT, UNREADABLE = "present", "absent", "unreadable"

# Distinct from both `None` ("keep the current context") and a name. Three outcomes, three values —
# collapsing "the user pressed Escape" into "keep the default" would carry on against a cluster
# they were trying to get away from.
_ABORT = object()


def _namespace_status(name: str, binary: str) -> str:
    """Whether the namespace exists — and honestly `unreadable` when that cannot be established.

    Two kinds try, not one. On OpenShift a regular user is routinely denied `get namespaces`
    cluster-wide even for a project they own, so a Forbidden on the Namespace says nothing about
    whether it exists; `get project` is the question the same user is allowed to ask.
    """
    kinds = ("namespace", "project") if binary == "oc" else ("namespace",)
    for kind in kinds:
        result = _run(cli(binary, "get", kind, name, "-o", "name"), timeout=30)
        if result is None:
            return UNREADABLE
        if result.returncode == 0:
            return PRESENT
        if "not found" in (result.stderr or "").lower():
            return ABSENT
    return UNREADABLE


def _create_namespace(name: str, binary: str) -> tuple[bool, str]:
    """Create it, by the route the user is actually likely to be allowed to take.

    `oc new-project` rather than `create namespace`: on OpenShift a regular user is usually denied
    creating a bare Namespace but permitted to request a Project, and the project request is what
    succeeds without cluster-admin. It also makes the new project current, which is a change to the
    user's kubeconfig and is therefore said out loud rather than left to be discovered.
    """
    argv = (
        cli(binary, "new-project", name) if binary == "oc"
        else cli(binary, "create", "namespace", name)
    )
    print(style.line(style.dim(f"$ {' '.join(argv)}")))
    result = _run(argv, timeout=120)
    if result is None:
        return False, f"could not run `{' '.join(argv)}`"
    if result.returncode == 0:
        return True, (result.stdout or "").strip()
    detail = (result.stderr or "").strip().splitlines()
    return False, detail[0][:200] if detail else "no detail given"


def _resolve_existing(name: str, binary: str, *, interactive: bool, assume_yes: bool) -> str:
    """Settle whether `name` is usable. Returns "ok", "retry" (ask for another), or "abort"."""
    status = _namespace_status(name, binary)
    if status == PRESENT:
        print(style.line(f"Namespace {style.value(name)} exists."))
        return "ok"
    if status == UNREADABLE:
        # Not an error and not a reason to stop: the review below compares every object against
        # this namespace and will show the truth in a moment either way.
        print(style.line(style.paint(
            f"Could not confirm whether namespace {name} exists — this cluster may not let you "
            "read namespaces. Carrying on.", "yellow"
        )))
        return "ok"

    print(style.line(style.paint(
        f"Namespace {style.value(name)} does not exist on this cluster.", "yellow"
    )))
    if not interactive and not assume_yes:
        print(
            f"Create it first (`{binary} new-project {name}`), or pass --yes to have this create "
            "it for you.",
            file=sys.stderr,
        )
        return "abort"
    if not assume_yes:
        answer = style.confirm(f"Create namespace {style.value(name)} now?", default=False)
        if answer is None:
            return "abort"
        if not answer:
            return "retry"
    created, detail = _create_namespace(name, binary)
    if created:
        print(style.line(style.paint(f"Created {name}. {detail}".strip(), "green")))
        if binary == "oc":
            print(style.note("`oc new-project` also made it your current project."))
        return "ok"
    print(style.line(style.paint(f"Could not create {name}: {detail}", "red")))
    print(style.note("Ask whoever administers this cluster, or choose a namespace you can use."))
    return "abort" if not interactive else "retry"


def _choose_namespace(
    explicit: str | None, *, interactive: bool, binary: str, assume_yes: bool = False
) -> str | None:
    """Which namespace to prepare — asked, not assumed, and confirmed to exist.

    `--namespace` always wins as a *name*, but is still checked: applying a bundle to a namespace
    that is not there fails five times over with five separate NotFound errors, which is a poor way
    to learn you made a typo. Otherwise the current context supplies the *default*, not the answer:
    landing silently on whatever `oc project` happens to be set to is how a shared `default`
    acquires a ServiceAccount, a Role and a 10Gi PVC that nobody asked for.

    Returns None when the user backs out — Escape, a refusal to create, or end of input.
    """
    if explicit:
        print(style.line(f"Using the namespace you passed: {style.value(explicit)}"))
        outcome = _resolve_existing(
            explicit, binary, interactive=interactive, assume_yes=assume_yes
        )
        # There is no prompt loop on this path: the user named it on the command line, so "retry"
        # can only mean "run it again with a different --namespace".
        return explicit if outcome == "ok" else None

    current = current_namespace()
    if not interactive:
        # No one to ask. `resolve_namespace` supplies both the current-context fallback and the
        # message naming the two ways to set it when there is none.
        target = resolve_namespace(None)
        print(style.line(
            f"Not a terminal; using the current context's namespace {style.value(target)}."
        ))
        outcome = _resolve_existing(target, binary, interactive=False, assume_yes=assume_yes)
        return target if outcome == "ok" else None

    print(style.note(
        "This is where the factory's ServiceAccount, Role, RoleBinding and workspace PVC will "
        "live. If it does not exist yet, you will be offered the chance to create it."
    ))
    print()
    _print_context(current)
    print()

    while True:
        # `read_line`, not `input`: Escape has to cancel the moment it is pressed rather than
        # insert `^[` into the line and do nothing until Enter.
        raw = style.read_line("Namespace to prepare", current)
        if raw is None:
            return None
        chosen = raw.strip() or current or ""
        if not chosen:
            print(
                "A namespace is required, and your current context does not supply one. "
                f"Select one with `{binary} project <name>`, or type it here.",
                file=sys.stderr,
            )
            continue
        outcome = _resolve_existing(
            chosen, binary, interactive=interactive, assume_yes=assume_yes
        )
        if outcome == "ok":
            return chosen
        if outcome == "abort":
            return None
