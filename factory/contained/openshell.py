"""OpenShell integration — running the factory inside a policy-governed sandbox.

Everything that knows about OpenShell lives here: the Python SDK (lifecycle, exec, listing) and
the `openshell` CLI (file transfer, interactive attach — the SDK has neither). The module
**composes** and does not execute; the run path is `factory.cli.contained_openshell`.

Why this target exists when `local` and `k8s` already do: those bound accidents and give a run a
reproducible environment, and say so. This one additionally confines agent-authored code at the
kernel level — Landlock filesystem allowlists, seccomp on escape vectors, and deny-by-default
network through a supervisor proxy — which is what running the factory on untrusted input
(arbitrary issues, stranger's codebases) requires. The two consequences worth holding onto:

- **Credentials never enter the sandbox.** They attach as a gateway-held provider; the sandbox
  sees an opaque placeholder the supervisor resolves only for requests to the provider's
  endpoints. There is deliberately no env-var fallback — a fallback would make the insecure
  route the path of least resistance.
- **The SDK is an optional dependency** (`contained-openshell` extra; grpc/protobuf do not
  belong in every install), so importing it raises a `ContainedError` carrying the fix rather
  than a bare `ImportError` at CLI startup.

Two shapes differ from the podman/k8s modules and deserve explanation up front.

**The main process is an idle command, not the run.** The provenance assertions have to run
after the workspace is in place and *before* the first agent call — the same ordering both other
targets enforce by launching the run in tmux after `podman exec`/`oc exec` probes. So the
sandbox's canonical main process is `sleep infinity` (it must outlive the run so a failed run
stays inspectable) and the run itself starts in a detached tmux session, exactly the local
target's structure with the SDK's `exec()` as the transport instead of `podman exec`.

**Dry-run cannot print argv for SDK calls.** The gateway API is gRPC, not a command line, so the
plan carries the SDK operations as *described* steps alongside the exact argv for the CLI parts.
`FACTORY_CONTAINED_DRY_RUN=1` prints that plan — the same ordered list the real path executes —
rather than a fabricated transcript. The CLI argv are still composed-only and printed verbatim,
so the parts that *are* commands keep the stronger guarantee.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from factory.contained.errors import ContainedError
from factory.contained.provenance import Probe
from factory.podman import LABEL_CONTAINED, LABEL_PROJECT, LABEL_SOURCE

# The OpenShell logical workspace sandboxes live in. The CLI targets `default` unless told
# otherwise, so a factory-created sandbox is visible to plain `openshell sandbox list` calls —
# the same discoverability rule the podman target gets by using podman's own default store.
SANDBOX_WORKSPACE = "default"

# Where the project lands inside the sandbox. The OpenShell docker/podman drivers derive the
# workspace root from the image's OCI WorkingDir (`resolve_oci_workspace_root`), and the
# factory-runtime image sets `/workspace` — so this matches the k8s target's constant rather
# than inventing a third location. A driver that ignored the image WorkingDir would fail the
# `workdir` provenance probe below loudly rather than extracting to the wrong place.
WORKSPACE_ROOT = "/workspace"

# What the sandbox's canonical main process runs. It has to outlive the factory (a failed run is
# exactly when the sandbox's state is worth reading) and die cleanly on stop; `sleep infinity`
# is what the local target's PID-1 payload runs for the same reasons.
IDLE_COMMAND: tuple[str, ...] = ("sleep", "infinity")

# One well-known session name, because `attach` has to find it without being told (podman.py
# owns the convention; the same name is reused so the tmux knowledge stays in one place).
TMUX_SESSION = "factory"

PROVIDER_FIX = (
    "curl -fsSL https://raw.githubusercontent.com/NVIDIA/OpenShell/main/providers/"
    "claude-code.yaml -o /tmp/claude-code.yaml\n"
    "  openshell profile import -f /tmp/claude-code.yaml --global\n"
    "  openshell provider create --name claude-code --type claude-code --from-existing"
)
SDK_FIX = "uv sync --extra contained-openshell   # or: uv pip install openshell"

# The gateway enforces sandbox names itself and says so only as an INVALID_ARGUMENT at create
# time: at most 19 characters, lowercase alphanumeric and hyphens. 19 is tight, so the readable
# stem gives up most of podman's 32-char budget and the hash suffix is what keeps two
# same-named projects apart — it is never the part that is truncated.
MAX_SANDBOX_NAME = 19


def sandbox_name(project_path: Path) -> str:
    """A `container_name` sibling that fits the gateway's sandbox-name rules.

    Same shape (slugified stem + project-hash suffix) as `factory.podman.container_name`, with a
    tighter budget: underscores and dots are not legal either, so non-alphanumerics collapse to
    hyphens like the podman slugs already do.
    """
    from factory.podman import project_hash

    digest = project_hash(project_path)[:6]
    stem = "".join(c if c.isalnum() else "-" for c in project_path.name.lower()).strip("-")
    stem = stem[: MAX_SANDBOX_NAME - 7].strip("-") or "factory"
    return f"{stem}-{digest}"


def validate_sandbox_name(name: str) -> str:
    """Reject a user-supplied `--name` the gateway would reject, naming the rule.

    Validating here turns a create-time INVALID_ARGUMENT (a gRPC traceback about a sandbox that
    never existed) into a parse-time error the user can fix before any workspace copy is made.
    """
    if not name:
        return name
    if len(name) > MAX_SANDBOX_NAME:
        raise ContainedError(
            f"--name {name!r} is {len(name)} characters; a sandbox name is at most "
            f"{MAX_SANDBOX_NAME} (lowercase alphanumeric and hyphens)"
        )
    illegal = {c for c in name if not (c.islower() and c.isalnum() or c == "-")}
    if illegal:
        raise ContainedError(
            f"--name {name!r} contains {sorted(illegal)!r}; a sandbox name is lowercase "
            "alphanumeric and hyphens only"
        )
    return name

# Exit code `exec` reports when the command could not run at all.
_EXEC_LAUNCH_FAILURE = -1


def import_sdk():
    """Import the OpenShell SDK, raising a fix-carrying error when the extra is absent.

    The SDK is optional (grpc/protobuf/cloudpickle in every install is too heavy for a target
    most users never touch), so a missing import is a *prerequisite* to report, not a crash.
    Deferred to call time rather than module import so `factory contained --help` works on a
    machine that has never installed the extra.
    """
    try:
        import openshell  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ContainedError(
            "the openshell Python SDK is not installed; `factory contained --target openshell` "
            f"needs it.\n  {SDK_FIX}"
        ) from exc
    return openshell


def import_protos():
    """Import the generated protobuf modules the SDK's spec/policy messages live in."""
    try:
        from openshell._proto import (  # type: ignore[import-not-found]
            openshell_pb2,
            sandbox_pb2,
        )
    except ImportError as exc:
        raise ContainedError(
            "the openshell Python SDK is not installed; `factory contained --target openshell` "
            f"needs it.\n  {SDK_FIX}"
        ) from exc
    return openshell_pb2, sandbox_pb2


def connect(gateway: str | None = None):
    """Build a `SandboxClient` from the CLI's registered gateway state.

    `from_active_cluster` reads exactly what `openshell gateway select` wrote
    (`$OPENSHELL_GATEWAY` or `~/.config/openshell/active_gateway`), so the factory and the CLI
    always agree on which gateway is in play without the factory growing its own gateway
    configuration. Never called in dry-run: it opens a connection, and a promise to provision
    nothing is not kept by a network round trip.
    """
    sdk = import_sdk()
    try:
        return sdk.SandboxClient.from_active_cluster(cluster=gateway)
    except sdk.SandboxError as exc:
        raise ContainedError(
            f"cannot connect to an OpenShell gateway: {exc}\n"
            "  Register and select one first:\n"
            "    openshell gateway add --name local --local\n"
            "    openshell gateway select local"
        ) from exc


# --- plan ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenShellPlan:
    """Everything needed to provision one sandbox and start the run in it, in order.

    The environment here is configuration only — never credentials. The provider carries
    inference credentials, and a secret-looking key in this dict is a bug, not an escape hatch
    (the run path warns about `--forward`/`--env` the way k8s does, more loudly).
    """

    name: str
    image: str
    project_dir: str
    env: dict[str, str]
    labels: dict[str, str]
    provider: str
    policy_path: Path | None
    run_command: str
    factory_command: str
    warnings: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Step:
    """One provisioning action, named so a failure can say which stage broke.

    SDK operations have no argv to print, so a step carries either a description (executed
    through the SDK by the run path) or exact argv (executed as a subprocess). Dry-run prints
    the same list the real path walks, in the same order.
    """

    name: str
    description: str | None = None
    argv: list[str] | None = None

    def render(self) -> str:
        import shlex

        if self.argv is not None:
            return shlex.join(self.argv)
        return self.description or self.name


def plan_steps(plan: OpenShellPlan, tarball: Path, probes: list[Probe]) -> list[Step]:
    """The full provisioning sequence as ordered, named steps.

    Mirrors `podman.plan_steps`: create, assert each provenance probe, run. The upload and
    extract steps sit between create and the probes because the assertions read the workspace
    (that is the point of them); the workdir probe comes first among the asserts so a driver
    that ignored the image's WorkingDir fails before anything reads `/workspace`.
    """
    steps = [
        Step("create", description=f"sdk: create sandbox {plan.name} from {plan.image}"),
        Step("upload", argv=build_upload_argv(plan.name, tarball)),
        Step(
            "extract",
            description=f"sdk: exec tar xzf {tarball.name} -C {WORKSPACE_ROOT}",
        ),
        Step("assert:workdir", description=f"sdk: exec pwd (expect {WORKSPACE_ROOT})"),
    ]
    for probe in probes:
        steps.append(
            Step(
                f"assert:{probe.name}",
                description=f"sdk: exec {probe.argv!r} in {plan.project_dir}",
            )
        )
    steps.append(Step("run", description=f"sdk: exec tmux launch in {plan.project_dir}"))
    return steps


# --- policy -------------------------------------------------------------------------


def build_default_policy():
    """The base sandbox policy: the security core of this target.

    Rules, in the order a reader should weigh them:

    - The workspace (`/workspace`, which `include_workdir` also covers) is read-write; OpenShell
      adds its baseline read-only system paths on top, so the toolchain works without this
      policy granting system reads itself.
    - Egress is deny-by-default and the allowlist is deliberately small: read-only package
      registries for `pip`/`uv` so eval environments can be built inside the sandbox. The
      defaults start slightly loose on purpose: loosening is a compatible change, silently
      tightening breaks running workflows. Every addition to this allowlist is a code review,
      the same as any other security-sensitive default.
    - **Inference egress is deliberately *absent*.** Attaching the `claude-code` provider (which
      `build_spec` always does) makes the gateway synthesize its own `_provider_claude_code`
      policy covering api/statsig/sentry for the provider's declared binaries — restating those
      endpoints here is not redundancy but a hard create-time failure: the gateway's ambiguity
      validation rejects two rules for the same endpoint whose metadata differs
      (`transparent_tcp_eligible`), and the synthesized rule cannot be matched field-for-field
      from a user policy.
    - Process identity is stated explicitly — `run_as_user`/`run_as_group` 1001 — because an
      omitted identity falls back to the image's OCI `USER` (1001 with primary GID 0), and the
      gateway hard-rejects any workload identity containing GID 0. The runtime image's
      arbitrary-UID recipe (`chgrp 0` + `chmod g=u` + `o=u`) keeps every writable path open to
      this gid too, so the local/k8s targets' conventions are untouched.

    `--policy` replaces this policy *entirely* — never merges (no negation semantics to
    maintain, WYSIWYG, and OpenShell already layers provider rules on top of the base).
    """
    _, sandbox_pb2 = import_protos()

    endpoints = sandbox_pb2.NetworkEndpoint
    binaries = sandbox_pb2.NetworkBinary

    def _rule(name: str, hosts: list[str], binary_paths: list[str]):
        return sandbox_pb2.NetworkPolicyRule(
            name=name,
            endpoints=[
                endpoints(host=host, port=443, protocol="tcp") for host in hosts
            ],
            binaries=[binaries(path=path) for path in binary_paths],
        )

    return sandbox_pb2.SandboxPolicy(
        version=1,
        filesystem=sandbox_pb2.FilesystemPolicy(include_workdir=True),
        process=sandbox_pb2.ProcessPolicy(run_as_user="1001", run_as_group="1001"),
        network_policies={
            "python_packages": _rule(
                "python_packages",
                ["pypi.org", "files.pythonhosted.org"],
                ["/usr/local/bin/pip", "/usr/bin/pip", "/usr/local/bin/uv", "/usr/bin/uv"],
            ),
        },
    )


# The documented YAML policy schema names this field `filesystem_policy`; the proto message
# calls it `filesystem`. This is the one rename between the format users write (what
# `openshell sandbox create --policy` accepts) and the proto the SDK takes.
_YAML_TO_PROTO_KEYS = {"filesystem_policy": "filesystem"}


def load_policy(path: Path):
    """Load a full-replacement policy from a YAML file in OpenShell's documented schema.

    The file is the whole policy — the default is not merged in (see `build_default_policy`).
    A user who needs the default plus one change copies the default and edits it (the default
    is documented in `docs/contained/`); the file they pass is everything they get, and an
    unknown key is an error rather than a silent drop — WYSIWYG cuts both ways.
    """
    import yaml

    _, sandbox_pb2 = import_protos()
    try:
        document = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ContainedError(f"cannot read policy file {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ContainedError(
            f"policy file {path} must be a YAML mapping (an OpenShell sandbox policy)"
        )
    translated = {_YAML_TO_PROTO_KEYS.get(key, key): value for key, value in document.items()}
    from google.protobuf import json_format  # type: ignore[import-untyped]

    policy = sandbox_pb2.SandboxPolicy()
    try:
        json_format.ParseDict(translated, policy)
    except json_format.ParseError as exc:
        raise ContainedError(
            f"policy file {path} is not a valid sandbox policy: {exc}"
        ) from exc
    return policy


def build_spec(plan: OpenShellPlan, policy) -> "object":
    """Compose the `SandboxSpec` the sandbox is created from.

    The command is the idle payload — see the module docstring for why the run starts in tmux
    after the provenance probes rather than as the main process. Providers are attached at
    create time so the placeholder environment exists before any process could ask for it.
    """
    openshell_pb2, _ = import_protos()
    return openshell_pb2.SandboxSpec(
        environment=dict(plan.env),
        template=openshell_pb2.SandboxTemplate(image=plan.image),
        policy=policy,
        providers=[plan.provider],
        command=list(IDLE_COMMAND),
    )


# --- CLI argv (composed, never executed here) ----------------------------------------


def build_upload_argv(name: str, tarball: Path) -> list[str]:
    """Compose the workspace upload. The tarball is a single file, so `.gitignore` filtering
    (which the CLI applies to directory uploads) has nothing to bite on — and the copy must
    carry gitignored state like `.factory/` the way the k8s target's tarball does."""
    return ["openshell", "sandbox", "upload", name, str(tarball)]


def build_download_argv(name: str, sandbox_path: str, dest: Path) -> list[str]:
    return ["openshell", "sandbox", "download", name, sandbox_path, str(dest)]


def build_attach_argv(name: str) -> list[str]:
    """Compose the interactive attach.

    The SDK has no PTY, so the CLI's `exec --tty` is the transport. tmux is what makes
    detaching safe (Ctrl-b d) and keeps the finished run's scrollback — the same reasons the
    podman target routes attach through tmux rather than the container's stdio.
    """
    return [
        "openshell", "sandbox", "exec", "-n", name, "--tty", "--",
        "sh", "-lc",
        f'exec tmux attach -t {TMUX_SESSION} 2>/dev/null || exec sh -i',
    ]


def build_cli_binary_check_argv() -> list[str]:
    return ["openshell", "--version"]


# --- execution (the only place that touches the gateway) ------------------------------


def exec_argv(session, argv: list[str], *, workdir: str | None = None, timeout: int = 300):
    """Run one command in the sandbox through the SDK, mapping failures to `ContainedError`.

    A command that cannot run at all reports exit code -1, which is distinct from a command
    that ran and failed — the caller's message should not claim the probe ran.
    """
    try:
        result = session.exec(list(argv), workdir=workdir, timeout_seconds=timeout)
    except Exception as exc:  # SandboxError and grpc errors both arrive as plain exceptions
        raise ContainedError(f"exec in sandbox failed: {exc}") from exc
    if result.exit_code == _EXEC_LAUNCH_FAILURE:
        raise ContainedError(
            f"could not run {argv[0]!r} in the sandbox (it did not execute at all)"
        )
    return result


def run_probes(session, plan: OpenShellPlan, probes: list[Probe]) -> None:
    """Run the provenance assertions, aborting with the probe's own hint on failure.

    The probes are the same argv lists the other targets run (`factory.contained.provenance`);
    only the transport differs. The workdir assertion runs first so a driver that ignored the
    image's WorkingDir fails before a probe misreads `/workspace` as the project being absent.
    """
    result = exec_argv(session, ["pwd"], timeout=30)
    actual = result.stdout.strip()
    if result.exit_code != 0 or actual != WORKSPACE_ROOT:
        raise ContainedError(
            f"the sandbox's working directory is {actual or 'unknown'}, expected "
            f"{WORKSPACE_ROOT}. The openshell target derives it from the runtime image's "
            f"WORKDIR; a compute driver that ignores it is not supported. "
            f"Override the image with --image or FACTORY_CONTAINED_IMAGE."
        )
    for probe in probes:
        result = exec_argv(session, probe.argv, workdir=plan.project_dir)
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            message = detail[0][:200] if detail else "no output"
            raise ContainedError(
                f"assertion {probe.name} failed in the sandbox: {message}\n  {probe.hint}"
            )


def start_run(session, plan: OpenShellPlan) -> None:
    """Start the run in a detached tmux session, mirroring the local target's launch.

    Reuses `build_tmux_launch` from `factory.podman` so the session conventions —
    remain-on-exit, the pane-died detach hook, the trailing inspectable shell — are defined
    once and shared by all three targets.
    """
    from factory.podman import build_tmux_launch

    script = build_tmux_launch(plan.project_dir, plan.run_command)
    exec_argv(session, ["sh", "-lc", script], timeout=60)


def extract_tarball(session, tarball_name: str) -> None:
    """Unpack the uploaded workspace tarball into the sandbox's workspace root.

    The tarball is packed as `<project>/...` (the k8s `_pack` convention), so it unpacks to
    `{WORKSPACE_ROOT}/<project>` — the path the plan, the rewritten payload and the probes
    already agree on.
    """
    exec_argv(
        session,
        ["tar", "xzf", tarball_name, "-C", WORKSPACE_ROOT],
        timeout=600,
    )


def create_sandbox(client, plan: OpenShellPlan, policy, tarball: Path) -> "object":
    """Create the sandbox and wait until it is ready. Returns the `SandboxSession`.

    Raises `ContainedError` on any gateway refusal (name collisions, provider unknown, image
    unpullable) rather than letting SDK exceptions escape as tracebacks.
    """
    spec = build_spec(plan, policy)
    try:
        client.create(
            workspace=SANDBOX_WORKSPACE,
            spec=spec,
            name=plan.name,
            labels=plan.labels,
        )
        client.wait_ready(name=plan.name, workspace=SANDBOX_WORKSPACE)
        return client.get_session(plan.name, workspace=SANDBOX_WORKSPACE)
    except ContainedError:
        raise
    except Exception as exc:
        raise ContainedError(f"creating sandbox {plan.name} failed: {exc}") from exc


def _phase_name(phase: int) -> str:
    """Map the proto phase number onto the word `ls` prints for the other targets."""
    return {
        0: "unknown",
        1: "provisioning",
        2: "running",       # READY: the idle main process is up; tmux liveness says if the run is
        3: "error",
        4: "deleting",
        5: "unknown",
        6: "stopping",
        7: "stopped",
        8: "starting",
        9: "completed",
    }.get(phase, "unknown")


def list_runtimes(gateway: str | None = None) -> list[dict[str, object]]:
    """Every sandbox the factory created, running or not.

    Selection is the factory's own label — the same rule the podman target applies — so a tool
    that lists resources it did not create never invites the user to assume it manages them.
    """
    client = connect(gateway)
    try:
        pager = client.list(
            workspace=SANDBOX_WORKSPACE,
            label_selector=f"{LABEL_CONTAINED}=true",
        )
        entries = []
        for ref in pager.all():
            labels = dict(ref.labels or {})
            entries.append(
                {
                    "name": ref.name,
                    "phase": _phase_name(ref.phase),
                    "project": labels.get(LABEL_PROJECT, ""),
                    "source": labels.get(LABEL_SOURCE, "") or None,
                    "created": None,  # not carried on SandboxRef; `ls` renders `?`
                    "exit_code": ref.status.exit_code if ref.status else None,
                }
            )
        return entries
    except ContainedError:
        raise
    except Exception as exc:
        raise ContainedError(f"listing sandboxes failed: {exc}") from exc


def remove_runtime(name: str, gateway: str | None = None) -> None:
    """Delete one factory-created sandbox, waiting until the deletion completes."""
    client = connect(gateway)
    try:
        client.delete(name=name, workspace=SANDBOX_WORKSPACE, allow_missing=True)
        client.wait_deleted(name=name, workspace=SANDBOX_WORKSPACE)
    except ContainedError:
        raise
    except Exception as exc:
        raise ContainedError(f"deleting sandbox {name} failed: {exc}") from exc


def run_liveness(client, name: str) -> str:
    """Whether the *run* (not the sandbox) is still alive, for `ls` and `rm`'s prompt.

    The sandbox deliberately outlives its run, so READY says nothing about the factory cycle.
    The tmux pane state is the answer — the same distinction the podman target draws with
    `build_pane_liveness_argv`.
    """
    from factory.podman import TMUX_SESSION as SESSION

    try:
        session = client.get_session(name, workspace=SANDBOX_WORKSPACE)
        result = session.exec(
            ["tmux", "list-panes", "-t", SESSION, "-F", "#{pane_dead}"],
            timeout_seconds=15,
        )
    except Exception:
        return "unknown"
    if result.exit_code != 0:
        return "finished"            # no session left at all
    return "running" if "0" in result.stdout.split() else "finished"


def cli_available() -> bool:
    """Whether the `openshell` CLI is on PATH (a prerequisite check, so never raises)."""
    import shutil

    return shutil.which("openshell") is not None


def run_cli(argv: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    """Run one `openshell` CLI command. Thin on purpose: composers live above, error wording
    lives in the caller, and this exists so tests have one seam to patch."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
