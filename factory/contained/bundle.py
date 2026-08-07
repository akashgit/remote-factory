"""The namespace prerequisite bundle — plain YAML the user applies.

`factory contained bundle` prints it and never applies it. `factory contained --target k8s setup`
prints it, asks, and then applies it *with the user's own credentials*. `factory contained verify`
checks each object and each required verb. That split is what keeps "the factory does not mutate
RBAC on its own" intact while still ending in a namespace that works.

Everything is namespace-scoped. RoleBindings to pre-existing cluster SCCs are allowed; creating an
SCC or a ClusterRole is not — a tool that needs cluster-admin to run a build is a tool nobody can
run on a cluster they share.

Per-cluster variation — namespace, storage class, image reference — is a parameter on the generator
rather than a value the user is expected to find and edit in the output.
"""

from __future__ import annotations

from dataclasses import dataclass

from factory.contained.errors import ContainedError
from factory.contained.k8s import (
    ADC_SECRET_KEY,
    PVC_NAME,
    SECRET_NAME,
    SERVICE_ACCOUNT,
    render_pvc,
)

ROLE_NAME = "factory-runtime"
SCC_ROLEBINDING = "factory-scc"


@dataclass(frozen=True)
class BundleObject:
    """One object in the bundle, addressable on its own.

    The bundle exists as a list before it exists as a blob. `setup` walks it object by object —
    checking each against the cluster and explaining it before asking — and a single rendered
    string cannot be walked. `render_bundle` joins these back together for `factory contained
    bundle`, so the two can never describe different sets of objects.

    `purpose` is written for someone deciding whether to allow this in *their* namespace, which is
    a different question from what the YAML says. The YAML already says a Role has these verbs; the
    purpose says why a run needs them.
    """

    kind: str
    """The lowercase form `oc get` accepts — `serviceaccount`, `rolebinding`, `pvc`."""

    name: str
    purpose: str
    manifest: str
    """This object's YAML alone, with no leading separator."""

    @property
    def ref(self) -> str:
        return f"{self.kind}/{self.name}"

# The verbs the *pod's* ServiceAccount needs, and no more.
#
# `pods/exec` is absent on purpose and its absence is load-bearing: the build
# sidecar is a boundary only because the agent cannot exec into it. Adding this verb — for any
# reason, including "attach would be easier" — hands the agent the shell path the sidecar exists to
# close. Attach does not need it here: `factory contained attach` runs as *you*, with your
# kubeconfig, not as this ServiceAccount.
BASE_RULES = """\
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["create", "get", "list", "watch", "delete"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
"""

# With --division only. `builds`/`buildconfigs` let the sidecar submit and poll a Build;
# `imagestreams` is where the result lands.
# `patch`/`update` on buildconfigs is not a widening: `create` + `delete` already give the same
# power by a longer route, so withholding it only costs the sidecar an extra round trip and a race.
# The sidecar needs it to set `dockerfilePath` per build, because the agent may name a different
# Containerfile on the next iteration.
DIVISION_RULES = """\
  - apiGroups: ["build.openshift.io"]
    resources: ["builds", "buildconfigs"]
    verbs: ["create", "get", "list", "watch", "delete", "patch", "update"]
  - apiGroups: ["build.openshift.io"]
    resources: ["builds/log"]
    verbs: ["get"]
  - apiGroups: ["build.openshift.io"]
    resources: ["buildconfigs/instantiatebinary"]
    verbs: ["create"]
  - apiGroups: ["image.openshift.io"]
    resources: ["imagestreams", "imagestreamtags"]
    verbs: ["create", "get", "list", "watch"]
"""


def resolve_target(namespace: str | None) -> str:
    """The namespace to generate for, or an error naming both ways to supply one.

    A namespace is never invented. Emitting cluster YAML pinned to a guessed name invites the user
    to apply it somewhere they did not intend, and "it defaulted to `factory`" is not something they
    would think to check.
    """
    target = namespace or _safe_current_namespace()
    if not target:
        raise ContainedError(
            "no namespace to generate the bundle for. Pass --namespace <name> before the "
            "subcommand:\n"
            "  factory contained --target k8s --namespace <name> bundle\n"
            "or select one first with `oc project <name>`."
        )
    return target


def bundle_objects(
    *,
    namespace: str | None = None,
    storage_class: str | None = None,
    division: bool = False,
    storage_size: str = "10Gi",
) -> list[BundleObject]:
    """The bundle as a list, in the order a reader should meet it.

    Identity first, then what that identity may do, then what grants it, then storage — so each
    object's explanation can refer to the one before it rather than forward to one not yet seen.

    The Secret is deliberately absent. It carries credential material, and the factory never reads
    or writes that — it references the Secret by name and `verify` checks it exists and carries the
    expected keys.
    """
    target = resolve_target(namespace)
    rules = BASE_RULES + (DIVISION_RULES if division else "")
    build_note = (
        " With --division it also carries the OpenShift build verbs, so the sidecar can submit a "
        "Build and read its log."
        if division else ""
    )
    return [
        BundleObject(
            kind="serviceaccount",
            name=SERVICE_ACCOUNT,
            purpose=(
                "The identity the factory's pod runs as. It holds no permissions by itself — "
                "everything below grants to this account and to nothing else, which is what makes "
                "the rest of the bundle bounded."
            ),
            manifest=f"""\
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {SERVICE_ACCOUNT}
  namespace: {target}
""",
        ),
        BundleObject(
            kind="role",
            name=ROLE_NAME,
            purpose=(
                "What that identity may do, and the whole of it: create, watch and delete pods in "
                "this namespace, and read their logs. That is what a run needs to launch a "
                "validation pod and see why it failed. `pods/exec` is absent on purpose — the "
                f"build sidecar is a boundary only because the agent cannot exec into it.{build_note}"
            ),
            manifest=f"""\
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: {ROLE_NAME}
  namespace: {target}
rules:
{rules}""",
        ),
        BundleObject(
            kind="rolebinding",
            name=ROLE_NAME,
            purpose=(
                "Grants the Role above to the ServiceAccount above. Without it the Role exists and "
                "applies to nobody, and the run fails on its first cluster call."
            ),
            manifest=f"""\
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {ROLE_NAME}
  namespace: {target}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: {ROLE_NAME}
subjects:
  - kind: ServiceAccount
    name: {SERVICE_ACCOUNT}
    namespace: {target}
""",
        ),
        BundleObject(
            kind="rolebinding",
            name=SCC_ROLEBINDING,
            purpose=(
                "Lets the pod run under the cluster's existing `restricted-v2` security context "
                "constraint, which admission requires before it will schedule the pod at all. It "
                "binds to an SCC that already exists — it does not create one, which would need "
                "cluster-admin and is out of bounds for this tool."
            ),
            manifest=f"""\
# Binds the ServiceAccount to the cluster's *existing* restricted SCC. Binding to a pre-existing
# SCC is namespace-scoped; creating one is not, and is out of bounds.
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {SCC_ROLEBINDING}
  namespace: {target}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: system:openshift:scc:restricted-v2
subjects:
  - kind: ServiceAccount
    name: {SERVICE_ACCOUNT}
    namespace: {target}
""",
        ),
        BundleObject(
            kind="pvc",
            name=PVC_NAME,
            purpose=(
                f"{storage_size} of storage holding the run's workspace. It outlives the pod on "
                "purpose: a long unattended run's work survives the pod being deleted, and "
                "`factory contained sync` fetches the result from here. Deleting a run never "
                "deletes this claim."
            ),
            manifest=render_pvc(target, storage_class, storage_size),
        ),
    ]


def render_bundle(
    *,
    namespace: str | None = None,
    storage_class: str | None = None,
    division: bool = False,
    image: str = "",
    storage_size: str = "10Gi",
) -> str:
    """Emit the whole bundle for one namespace, as `factory contained bundle` prints it.

    Composed from `bundle_objects` rather than written out again, so the blob and the walkthrough
    can never come to describe different sets of objects.
    """
    target = resolve_target(namespace)
    objects = bundle_objects(
        namespace=target, storage_class=storage_class, division=division,
        storage_size=storage_size,
    )
    image_note = f"#   runtime image: {image}\n" if image else ""
    body = "".join(f"---\n{obj.manifest}" for obj in objects)

    return f"""\
# factory contained — namespace prerequisites for {target}
#
# Apply with your own credentials:
#     factory contained --namespace {target}{' --division' if division else ''} bundle | oc apply -f -
#
# Then create the inference credentials Secret yourself — the factory never handles the material:
#     oc create secret generic {SECRET_NAME} -n {target} \\
#         --from-literal=ANTHROPIC_API_KEY=...
#   or, for Vertex — the credential is a *file*, so it is `--from-file` under this exact key,
#   which the pod mounts and points GOOGLE_APPLICATION_CREDENTIALS at:
#     oc create secret generic {SECRET_NAME} -n {target} \\
#         --from-literal=CLAUDE_CODE_USE_VERTEX=1 \\
#         --from-literal=CLOUD_ML_REGION=us-east5 \\
#         --from-literal=ANTHROPIC_VERTEX_PROJECT_ID=... \\
#         --from-file={ADC_SECRET_KEY}=$HOME/.config/gcloud/application_default_credentials.json
#
{image_note}# Everything below is namespace-scoped. Nothing here creates an SCC or a ClusterRole.
{body}"""


def _safe_current_namespace() -> str | None:
    """The current context's namespace, or None — `bundle` must work with no cluster reachable."""
    try:
        from factory.contained.k8s import current_namespace

        return current_namespace()
    except Exception:                                        # noqa: BLE001 — see docstring
        return None
