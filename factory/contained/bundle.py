"""The namespace prerequisite bundle — plain YAML the user applies (spec §8).

`factory contained bundle` prints it and never applies it. `factory contained setup --target k8s`
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

from factory.contained.k8s import SECRET_NAME, SERVICE_ACCOUNT, render_pvc

ROLE_NAME = "factory-runtime"
SCC_ROLEBINDING = "factory-scc"

# The verbs the *pod's* ServiceAccount needs, and no more.
#
# `pods/exec` is absent on purpose and its absence is load-bearing (spec §6.3, §11): the build
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
DIVISION_RULES = """\
  - apiGroups: ["build.openshift.io"]
    resources: ["builds", "buildconfigs"]
    verbs: ["create", "get", "list", "watch", "delete"]
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


def render_bundle(
    *,
    namespace: str | None = None,
    storage_class: str | None = None,
    division: bool = False,
    image: str = "",
    storage_size: str = "10Gi",
) -> str:
    """Emit the bundle for one namespace.

    The Secret is deliberately *not* in here. It carries credential material, and the factory never
    reads or writes that — it references the Secret by name and `verify` checks it exists and
    carries the expected keys (spec §4.5). The command to create it is printed as a comment so the
    user has it in front of them without the factory ever touching the value.
    """
    target = namespace or _safe_current_namespace() or "factory"
    rules = BASE_RULES + (DIVISION_RULES if division else "")
    image_note = f"#   runtime image: {image}\n" if image else ""

    return f"""\
# factory contained — namespace prerequisites for {target}
#
# Apply with your own credentials:
#     factory contained bundle --namespace {target}{' --division' if division else ''} | oc apply -f -
#
# Then create the inference credentials Secret yourself — the factory never handles the material:
#     oc create secret generic {SECRET_NAME} -n {target} \\
#         --from-literal=ANTHROPIC_API_KEY=...
#   or, for Vertex:
#     oc create secret generic {SECRET_NAME} -n {target} \\
#         --from-literal=CLAUDE_CODE_USE_VERTEX=1 \\
#         --from-literal=CLOUD_ML_REGION=us-east5 \\
#         --from-literal=ANTHROPIC_VERTEX_PROJECT_ID=... \\
#         --from-file=GOOGLE_APPLICATION_CREDENTIALS=...
#
{image_note}# Everything below is namespace-scoped. Nothing here creates an SCC or a ClusterRole.
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {SERVICE_ACCOUNT}
  namespace: {target}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: {ROLE_NAME}
  namespace: {target}
rules:
{rules}---
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
---
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
---
{render_pvc(target, storage_class, storage_size)}"""


def _safe_current_namespace() -> str | None:
    """The current context's namespace, or None — `bundle` must work with no cluster reachable."""
    try:
        from factory.contained.k8s import current_namespace

        return current_namespace()
    except Exception:                                        # noqa: BLE001 — see docstring
        return None
