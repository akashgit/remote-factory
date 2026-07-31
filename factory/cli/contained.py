"""`factory contained` — run the factory inside an NVIDIA OpenShell sandbox."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import replace
from functools import lru_cache
from datetime import UTC, datetime
from pathlib import Path

import yaml

import structlog

from factory.cli._mode_handlers import _resolve_model
from factory.cli._run_args import (
    SANDBOX_ENV_POLICY,
    build_run_args,
    redact_argv,
    redact_env,
)
from factory.division import (
    DRIVER_ENV,
    K8S_DIVISION_PORT,
    BuildObjectsSpec,
    DIVISION_BRIEF_PATH,
    MCP_BRIDGE_PATH,
    division_brief,
    mcp_bridge_source,
    render_image_stream,
    render_build,
    internal_image_ref,
    build_k8s_policy,
    current_namespace,
    mcp_client_config_k8s,
    BuildPodSpec,
    DivisionError,
    audit_pod,
    bind_mount_config,
    build_local_policy,
    check_bind_mounts_enabled,
    check_division_endpoint,
    detect_compute_driver,
    mcp_client_config,
    load_pod_manifest,
    render_build_pod,
    render_policy_yaml,
    strategic_merge,
)
from factory.openshell import (
    DRY_RUN_ENV,
    SANDBOX_WORKSPACE,
    LABEL_NAME,
    LABEL_PROJECT,
    USER_CONFIG_STAGE,
    SandboxPlan,
    Step,
    Upload,
    build_run_command,
    dry_run_enabled,
    growth_context_warning,
    plan_steps,
    project_hash,
    resolve_image,
    sandbox_name,
    sandbox_project_path,
)

log = structlog.get_logger()


class _RejectTmuxPersist(argparse.Action):
    """Reject `--tmux-persist` while parsing rather than at runtime.

    tmux is not in OpenShell's base image, and `factory/runners/_tmux_persist.py` has 23 call sites
    that would each fail somewhere inside a provisioned sandbox. Failing here costs nothing; failing
    there costs a sandbox, a transfer, and a confusing traceback.
    """

    def __init__(self, option_strings: list[str], dest: str, **kwargs: object) -> None:
        super().__init__(option_strings, dest, nargs=0, default=False, **kwargs)  # type: ignore[arg-type]

    def __call__(self, parser: argparse.ArgumentParser, *_args: object, **_kw: object) -> None:
        parser.error(
            "--tmux-persist is not supported under `factory contained`: tmux is not present in "
            "the OpenShell sandbox base image, so the persistent-session runner cannot start. "
            "Drop the flag to run headless inside the sandbox, or use `factory tmux` to run "
            "outside one."
        )


def build_contained_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the `contained` subcommand."""
    p = sub.add_parser(
        "contained",
        help="Run the factory inside an NVIDIA OpenShell sandbox",
    )
    p.add_argument("path", help="Project path, GitHub URL, idea file path, or prompt")
    p.add_argument(
        "--target",
        choices=["local", "k8s"],
        default="local",
        help="Which OpenShell gateway the sandbox is created on (default: local)",
    )
    # No `nargs="?"`, and no default derived from --target: an explicit value is required so the
    # flag's meaning is never ambiguous, and a bare `--division` is a parse error.
    p.add_argument(
        "--division",
        choices=["local", "k8s"],
        default=None,
        help="Where the container-manufacturing plane lives. Requires an explicit value; "
             "it is never inherited from --target. Omit to disable builds entirely.",
    )
    p.add_argument(
        "--namespace",
        default=None,
        help="Namespace for division resources (default: the current kube context's namespace)",
    )
    p.add_argument(
        "--image-name",
        default=None,
        help="Name of the image the k8s division builds (default: the project directory's name). "
             "The tag is always a UTC build timestamp.",
    )
    p.add_argument("--pod-manifest", default=None, help="Replace the build pod manifest wholesale")
    p.add_argument("--pod-patch", default=None, help="Strategic-merge fragment over the bundled manifest")
    p.add_argument("--gateway", default=None, help="OpenShell gateway name (default: the selected one)")
    p.add_argument("--image", default=None, help="Sandbox image reference (default: the published one)")
    # The env policy forwards FACTORY_ only and pins the inference variables, which is right for
    # credentials and wrong for the handful of backend quirks an operator has to work around —
    # MAX_THINKING_TOKENS=0 against a Vertex model that rejects adaptive thinking, for one. Naming
    # each variable on the command line keeps that explicit instead of widening the policy for
    # everyone.
    p.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        dest="extra_env",
        help="Extra environment variable for the sandbox. Repeatable. Applied after the env policy.",
    )
    p.add_argument("--tmux-persist", action=_RejectTmuxPersist, help=argparse.SUPPRESS)

    # The `factory ceo` flags that are re-serialized into the in-sandbox invocation.
    p.add_argument("--mode", default="auto", help="Run mode passed through to the CEO")
    p.add_argument("--model", default=None, help="Model for agent subprocesses")
    p.add_argument("--runner", default=None, help="CLI backend to use")
    p.add_argument("--profile", default=None, help="Credential profile from ~/.factory/config.toml")
    p.add_argument("--focus", default=None, help="Target a specific item")
    p.add_argument("--refine", default=None, metavar="REQUEST", help="Refinement mode")
    p.add_argument("--prompt", default=None, help="Path to a prompt/spec file")
    p.add_argument("--branch", default=None, help="Target branch for PRs")
    p.add_argument("--no-github", action="store_true", default=False, help="Disable GitHub operations")
    clean_pr = p.add_mutually_exclusive_group()
    clean_pr.add_argument("--clean-pr", action="store_true", default=None, dest="clean_pr")
    clean_pr.add_argument("--no-clean-pr", action="store_false", dest="clean_pr")
    p.add_argument("--min-growth", type=int, default=None, help="Minimum guaranteed growth hypotheses")
    p.add_argument("--max-new", type=int, default=None, help="Max new backlog items per cycle")
    p.add_argument("--discover-only", action="store_true", default=False)
    p.add_argument("--bg-agents", action="store_true", default=False)
    p.add_argument("--use-profile", action="store_true", default=False)
    return p


def _unsupported(feature: str, phase: int, extra: str = "") -> str:
    message = f"{feature} is not implemented yet (arrives in phase {phase})."
    return f"{message} {extra}".strip()


def _check_scope(args: argparse.Namespace) -> str | None:
    """Reject combinations that are specified but not yet built, and one that cannot work.

    Dry-run is exempt from the k8s rejections. `--division k8s` under dry-run renders the build pod
    and stops, which is how the manifest can be reviewed — and audited for privilege — without a
    cluster. Nothing is provisioned either way, so refusing to render would only make the manifest
    harder to inspect than to run.
    """
    # Checked before the dry-run exemption: the mode is wrong under `contained` whether or not
    # anything is provisioned, and rendering a plan for it would suggest otherwise.
    if args.mode in ("design", "interactive"):
        return (
            f"--mode {args.mode} cannot run under `factory contained`. It starts an interactive "
            "CEO session that presents findings and waits for the operator, and the sandbox is "
            "driven over a pipe with no terminal attached — the session's output never reaches "
            "you, so the run is invisible while real agents run, and interrupting the client "
            "orphans it rather than stopping it. Use `factory ceo --mode design` on the host, or "
            "run contained without --mode."
        )
    if dry_run_enabled():
        return None
    if args.target == "k8s":
        return _unsupported(
            "--target k8s",
            4,
            "It needs an OpenShell gateway deployed into the cluster. Use --target local.",
        )
    if args.division == "k8s" and not (args.namespace or current_namespace()):
        # The namespace is never defaulted. `default` exists on every cluster, and creating build
        # objects there because the current context could not be read is a mistake someone else
        # discovers.
        return (
            "--division k8s needs a namespace and the current kube context does not name one. "
            "Pass --namespace, or switch to a context with a namespace set."
        )
    return None


def _render_build_pod(args: argparse.Namespace, project_path: Path) -> dict[str, object] | None:
    """Render the k8s division's build pod, applying `--pod-manifest` or `--pod-patch`."""
    if args.division != "k8s":
        return None
    namespace = args.namespace or current_namespace() or "default"
    if args.pod_manifest:
        # Used verbatim. An override is an override; silently normalising it would defeat the point.
        return load_pod_manifest(Path(args.pod_manifest).expanduser())
    spec = BuildPodSpec(
        name=f"{sandbox_name(project_path)}-build",
        namespace=namespace,
        image_ref=f"{project_path.name}:factory",
    )
    manifest = render_build_pod(spec)
    if args.pod_patch:
        patch = load_pod_manifest(Path(args.pod_patch).expanduser())
        manifest = strategic_merge(manifest, patch)
    return manifest


def _division_config(
    args: argparse.Namespace, project_path: Path
) -> tuple[dict[str, object] | None, dict[str, object] | None, list[str]]:
    """Resolve a division's driver config and MCP policy.

    Returns (driver_config, policy, warnings). Raises DivisionError when a precondition fails —
    the division must not be half-configured, because a sandbox with an allowlisted MCP endpoint
    but no bind mount looks healthy and fails deep inside the first build.
    """
    if args.division is None:
        return None, None, []

    if args.division == "k8s":
        namespace = args.namespace or current_namespace() or "default"
        k8s_warnings = [
            f"--division k8s is enabled: build and validation pods run in namespace {namespace} "
            "and are NOT confined by OpenShell. The cluster credentials stay on the host with "
            "kubernetes-mcp-server; the sandbox reaches it through the allowlisted MCP endpoint "
            "only."
        ]
        if not dry_run_enabled():
            check_division_endpoint(port=K8S_DIVISION_PORT)
        # No driver config: nothing from the host is mounted. The build context reaches the cluster
        # as a binary upload from here, and the loop's iterations are patches to the BuildConfig.
        return None, build_k8s_policy(), k8s_warnings

    warnings: list[str] = []
    dry_run = dry_run_enabled()

    # Dry-run renders what would be sent; it does not gate on a live gateway, because the point is
    # to inspect the configuration on a machine that may not have one. The gateway preconditions
    # still apply in full to a real run — which is where provisioning on an unconfirmed setting
    # would actually cost something.
    if dry_run:
        warnings.append(
            "dry-run: gateway preconditions (enable_bind_mounts, compute driver) were not checked. "
            "A real run refuses to provision unless both are confirmed."
        )
    else:
        check_bind_mounts_enabled(args.gateway)
        # The policy permits the tool calls; the server has to actually be there to answer them.
        check_division_endpoint()

    driver = detect_compute_driver(args.gateway)
    if driver is None:
        if not dry_run:
            raise DivisionError(
                "could not determine the gateway's compute driver (podman or docker). The "
                "driver-config JSON is keyed by driver name, so a guess would be silently ignored "
                "and the sandbox would come up with no bind mount at all."
            )
        driver = "podman"
        warnings.append(
            f"dry-run: compute driver assumed to be {driver}; set {DRIVER_ENV} to render the other."
        )

    return bind_mount_config(driver, project_path), build_local_policy(project_path), warnings


def _parse_extra_env(pairs: list[str]) -> dict[str, str]:
    """Parse repeated `--env KEY=VALUE` into a mapping, rejecting anything malformed."""
    parsed: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise DivisionError(
                f"--env {pair!r} is not KEY=VALUE. Each --env takes one variable, and the value may "
                "be empty but the '=' may not be omitted."
            )
        parsed[key.strip()] = value
    return parsed


DIVISION_BUILD_MANIFEST = ".factory/division/build.yaml"

# etcd caps an object at ~1MiB and a ConfigMap stores its binary payload base64-encoded, so the
# usable ceiling for the encoded context is a little under that.
CONTEXT_LIMIT_BYTES = 900_000

# Stands in for the real payload name while rendering a dry-run plan, where nothing is built.
DRY_RUN_PAYLOAD = "context.tar.gz"


def _k8s_build_spec(args: argparse.Namespace, project_path: Path) -> BuildObjectsSpec:
    return BuildObjectsSpec(
        name=getattr(args, "image_name", None) or project_path.name.replace("_", "-").lower(),
        namespace=args.namespace or current_namespace() or "default",
        tag=datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
        # Dry-run must not build anything. Naming the payload here would run `uv build` (or tar the
        # tree) just to render a plan, and would fail outright on a project that is not a git
        # repository — which is exactly what a rendering-only mode has to cope with.
        dockerfile=_default_dockerfile(
            DRY_RUN_PAYLOAD if dry_run_enabled() else _k8s_context_payload(project_path)[1]
        ),
    )


def _default_dockerfile(payload: str) -> str:
    """The Dockerfile the k8s division starts from, carried in the Build object itself.

    Inline rather than read from the project so the agent can fix it: a failed build is retried by
    creating another Build with a corrected `spec.source.dockerfile`, which is a call it can make.
    A Dockerfile that lives only in the uploaded context would need a fresh upload from the host
    for every iteration, and the agent has no way to perform one.
    """
    if payload.endswith(".whl"):
        # COPY first: the ConfigMap's files land in the build *context*, not in the image, so
        # without this pip fails with `No such file or directory` on a path that is plainly in the
        # context directory.
        install = f"COPY {payload} .\nRUN pip install --no-cache-dir ./{payload}\n"
    else:
        # ADD unpacks a .tar.gz; COPY would leave the archive sitting there.
        install = f"ADD {payload} .\nRUN pip install --no-cache-dir .\n"
    return (
        "FROM registry.access.redhat.com/ubi9/python-312:latest\n"
        "WORKDIR /opt/app-root/src\n"
        "USER 0\n"
        f"{install}"
        "RUN chown -R 1001:0 /opt/app-root\n"
        "USER 1001\n"
        'ENTRYPOINT ["factory"]\n'
        'CMD ["--help"]\n'
    )


@lru_cache(maxsize=4)
def _k8s_context_payload(project_path: Path) -> tuple[Path, str]:
    """Produce the smallest artefact the image actually needs, and say what it is.

    A ConfigMap is stored base64-encoded and etcd caps an object at ~1MiB, so the whole repository
    does not fit — this project's own tree is 2.3MB gzipped, more than twice the ceiling. What the
    image needs is not the repository but the installable package, and a wheel of the same project
    is 507KB. So a Python project ships a wheel; anything else ships a tarball of its tracked and
    untracked-but-not-ignored files, and is size-checked against the same ceiling.
    """
    if (project_path / "pyproject.toml").is_file() and shutil.which("uv"):
        out = Path(tempfile.mkdtemp(prefix="factory-k8s-wheel-"))
        result = subprocess.run(
            ["uv", "build", "--wheel", "-o", str(out)],
            cwd=project_path,
            capture_output=True,
            text=True,
            check=False,
        )
        wheels = sorted(out.glob("*.whl"))
        if result.returncode == 0 and wheels:
            return wheels[0], wheels[0].name
        log.info("k8s_wheel_build_failed", stderr=result.stderr.strip()[:400])

    tarball = Path(tempfile.mkdtemp(prefix="factory-k8s-context-")) / "context.tar.gz"
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=project_path,
        capture_output=True,
        text=True,
        check=False,
    )
    members = [line for line in listing.stdout.splitlines() if line.strip()]
    if not members:
        raise DivisionError(
            f"no files to send as a build context from {project_path} — `git ls-files` returned "
            "nothing. The k8s division builds from the project's tracked and untracked-but-not-"
            "ignored files, so an empty listing means there is nothing to build."
        )
    with tarfile.open(tarball, "w:gz") as archive:
        for member in members:
            source = project_path / member
            if source.is_file():
                info = archive.gettarinfo(str(source), arcname=member)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with source.open("rb") as handle:
                    archive.addfile(info, handle)
    return tarball, tarball.name


def _k8s_bootstrap(spec: BuildObjectsSpec, project_path: Path) -> list[Step]:
    """Host-side steps that put the build context and its ImageStream in the namespace.

    The context is uploaded once, from here, because the sandbox has no path to the cluster except
    the MCP allowlist. Everything after this — creating Builds, reading their logs, running the
    validation pod — the agent does itself.
    """
    binary = "oc" if shutil.which("oc") else "kubectl"
    payload, payload_name = _k8s_context_payload(project_path)

    # Checked here because the API's own refusal names a byte count and not the reason, and because
    # the fix — a narrower context — is the operator's to make.
    encoded = payload.stat().st_size * 4 // 3
    if encoded > CONTEXT_LIMIT_BYTES:
        raise DivisionError(
            f"the build context is {payload.stat().st_size // 1024}KB "
            f"({encoded // 1024}KB once base64-encoded), over the ~{CONTEXT_LIMIT_BYTES // 1024}KB "
            "a ConfigMap can hold. The k8s division carries the context in a ConfigMap because it "
            "is the only transport the agent can re-drive without help from this host. Narrow the "
            "context by gitignoring what the image does not need, or build this project with "
            "--division local instead."
        )

    manifest = Path(tempfile.mkdtemp(prefix="factory-k8s-is-")) / "imagestream.yaml"
    manifest.write_text(yaml.safe_dump(render_image_stream(spec), sort_keys=False))
    return [
        Step("k8s_imagestream", [binary, "apply", "-n", spec.namespace, "-f", str(manifest)]),
        # Replaced rather than patched: a ConfigMap carrying a build artefact has no meaningful
        # merge, and a stale context is the failure that looks like a working build of the wrong
        # tree.
        Step(
            "k8s_context_clear",
            [binary, "delete", "configmap", spec.context_configmap, "-n", spec.namespace,
             "--ignore-not-found"],
        ),
        Step(
            "k8s_context",
            [binary, "create", "configmap", spec.context_configmap, "-n", spec.namespace,
             f"--from-file={payload_name}={payload}"],
        ),
    ]


def _mcp_config_for(division: str | None, sandbox_path: str = "") -> dict[str, object] | None:
    """The MCP registration the agent needs for the division it was given, if any.

    The policy permits the tool calls; it does not advertise them. Without this the agent never
    learns the server exists, and the division is allowed but unused — which looks like the agent
    choosing not to build.
    """
    if division == "local":
        return mcp_client_config()
    if division == "k8s":
        # Absolute, because agents run inside `.factory-worktrees/run-<id>/` and a relative command
        # path would be resolved against a directory the bridge is not in.
        return mcp_client_config_k8s(bridge=f"{sandbox_path}/{MCP_BRIDGE_PATH}")
    return None


def _build_plan(args: argparse.Namespace, project_path: Path) -> SandboxPlan:
    """Assemble the provisioning plan, including the transfers and the composed sandbox env."""
    name = sandbox_name(project_path)
    # Under a local division the project is not copied in — it is bind-mounted at its own absolute
    # path, and the factory has to work in *that* directory. `image_build` resolves `containerFile`
    # on the host, so a Containerfile the agent writes under /sandbox/<name> names a path the host
    # does not have, and the build fails on a missing file with no hint that the copy was the
    # problem. Same path inside and out is the whole point of the mount.
    bind_mounted = args.division == "local"
    sbx_path = str(project_path) if bind_mounted else sandbox_project_path(project_path)

    env = SANDBOX_ENV_POLICY.resolve(dict(os.environ))
    env.update(_parse_extra_env(getattr(args, "extra_env", []) or []))

    uploads: list[Upload] = []
    warnings: list[str] = []
    factory_dir = project_path / ".factory"
    git_dir = project_path / ".git"
    if not bind_mounted:
        uploads.append(
            Upload(
                local=project_path,
                # The workspace, not the project's path inside it: an upload keeps its own basename,
                # so naming the full destination lands the tree at <path>/<name> instead.
                dest=SANDBOX_WORKSPACE,
                respect_gitignore=True,
                description="project tree (honours the project's .gitignore)",
            )
        )
        if git_dir.is_dir():
            # `.git` does not survive the filtered upload — verified: a repo uploaded this way
            # arrives without it and `git status` reports "not a git repository". The factory's
            # first act is state detection, which reads git; without this the CEO sees `no_repo`,
            # falls back to build mode, and refuses flags that only exist in improve mode. The
            # symptom names the flag, not the missing directory.
            uploads.append(
                Upload(
                    local=git_dir,
                    dest=sbx_path,
                    respect_gitignore=False,
                    description="git metadata (.gitignore filtering disabled)",
                )
            )
        else:
            warnings.append(
                f"No .git/ directory at {git_dir} — the factory will detect this as a "
                "fresh, unversioned project and run its build path."
            )

    if bind_mounted:
        # Nothing to transfer and nothing to assert: the mount is the host's own directory, so
        # .factory/ is whatever the host has, live.
        pass
    elif factory_dir.is_dir():
        # Separate, and with .gitignore filtering off. This project's own convention is to gitignore
        # .factory/, so folding it into the tree upload drops config.json, eval_profile.json, and
        # results.tsv without a word.
        uploads.append(
            Upload(
                local=factory_dir,
                dest=sbx_path,
                respect_gitignore=False,
                description="experiment state (.gitignore filtering disabled)",
            )
        )
    else:
        warnings.append(
            f"No .factory/ directory at {factory_dir} — the sandbox will boot into a "
            "fresh-project state and run discovery."
        )

    user_config = Path("~/.factory").expanduser()
    stage_user_config = user_config.is_dir()
    if stage_user_config:
        uploads.append(
            Upload(
                local=user_config,
                # The staging directory's parent. `~/.factory` arrives as `.factory` inside it, so
                # USER_CONFIG_STAGE is where it lands and this is one level up.
                dest=str(Path(USER_CONFIG_STAGE).parent),
                respect_gitignore=False,
                description="user-local config and ACE playbooks",
            )
        )

    growth_warning = growth_context_warning()
    if growth_warning:
        warnings.append(growth_warning)

    driver_config, division_policy, division_warnings = _division_config(args, project_path)
    warnings.extend(division_warnings)
    if args.division == "local":
        # The division opens the isolation boundary by design (spec §5). Saying so at launch is the
        # mitigation that goes with being opt-in — an operator should never discover after the fact
        # that a container escaped the sandbox's confinement.
        warnings.append(
            "--division local is enabled: containers it launches run on the host's podman/docker "
            "and are NOT confined by OpenShell. The project directory is bind-mounted read-write "
            f"at {project_path}."
        )

    model = _resolve_model(args)
    factory_args = build_run_args(args, Path(sbx_path), model, headless=True)
    files: dict[str, str] = {}
    if args.division == "k8s":
        # The stdio bridge the MCP registration points at. Written next to the manifest so both
        # arrive with the sandbox rather than needing a second transfer.
        files[MCP_BRIDGE_PATH] = mcp_bridge_source()
        # The manifest the agent submits, rendered here so the tag, namespace, and image stream all
        # agree with the objects the bootstrap just created. The agent edits its dockerfile and
        # resubmits; it does not have to invent the shape.
        spec = _k8s_build_spec(args, project_path)
        files[DIVISION_BUILD_MANIFEST] = yaml.safe_dump(
            render_build(spec, build_name=f"{spec.name}-{spec.tag}"), sort_keys=False
        )
        files[DIVISION_BRIEF_PATH] = division_brief(
            "k8s", manifest=DIVISION_BUILD_MANIFEST, image_ref=internal_image_ref(spec)
        )
        warnings.append(
            f"k8s division: submit {DIVISION_BUILD_MANIFEST} with resources_create_or_update to "
            f"build, then validate with a pod on {internal_image_ref(spec)}."
        )
    elif args.division == "local":
        files[DIVISION_BRIEF_PATH] = division_brief("local")
    run_command = build_run_command(
        sbx_path,
        factory_args,
        stage_user_config=stage_user_config,
        mcp_config=_mcp_config_for(args.division, sbx_path),
        files=files or None,
    )

    return SandboxPlan(
        name=name,
        image=args.image or resolve_image(),
        project_path=project_path,
        sandbox_path=sbx_path,
        env=env,
        labels={LABEL_PROJECT: project_hash(project_path), LABEL_NAME: project_path.name},
        uploads=tuple(uploads),
        run_command=run_command,
        gateway=args.gateway,
        driver_config=driver_config,
        division_policy=division_policy,
        warnings=tuple(warnings),
        assert_factory_state=(factory_dir / "config.json").is_file() and not bind_mounted,
        probe_writable=bind_mounted,
    )


def _write_policy(plan: SandboxPlan) -> SandboxPlan:
    """Materialise the division policy so `--policy` points at a file that exists.

    Written even in dry-run: an argv naming a path that was never created is a rendering of a
    command that would not have worked, which is the drift dry-run exists to prevent.
    """
    if plan.division_policy is None:
        return plan
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="factory-division-policy-", delete=False
    )
    handle.write(render_policy_yaml(dict(plan.division_policy)))
    handle.close()
    return replace(plan, policy_path=handle.name)


def _emit_dry_run(plan: SandboxPlan, build_pod: dict[str, object] | None) -> int:
    """Print the exact commands the real path would run, then exit without provisioning.

    Secret-looking values are masked. Dry-run output is captured into evidence files that are
    retained per run and read by an evaluator, so anything printed here outlives the invocation.
    """
    steps = [
        {"step": step.name, "argv": redact_argv(step.argv, SANDBOX_ENV_POLICY)}
        for step in plan_steps(plan)
    ]
    payload = {
        "dry_run": True,
        "sandbox": plan.name,
        "image": plan.image,
        "project_path": str(plan.project_path),
        "sandbox_path": plan.sandbox_path,
        "labels": plan.labels,
        "env": redact_env(plan.env, SANDBOX_ENV_POLICY),
        "uploads": [
            {
                "local": str(u.local),
                "dest": u.dest,
                "respect_gitignore": u.respect_gitignore,
                "description": u.description,
            }
            for u in plan.uploads
        ],
        "run_command": plan.run_command,
        "steps": steps,
        "driver_config": plan.driver_config,
        "division_policy": plan.division_policy,
        "division_policy_yaml": (
            render_policy_yaml(dict(plan.division_policy)) if plan.division_policy else None
        ),
        "build_pod": build_pod,
        "build_pod_audit": audit_pod(build_pod) if build_pod else None,
        "warnings": list(plan.warnings),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _run_step(step: Step) -> subprocess.CompletedProcess[str]:
    log.info("openshell_step", step=step.name, argv=redact_argv(step.argv, SANDBOX_ENV_POLICY))
    if step.name == "run":
        # Inherited stdio, deliberately. This step is the factory itself running inside the sandbox;
        # capturing it would hold every line until the process exits and leave the operator watching
        # a blank terminal for the length of a cycle.
        completed = subprocess.run(step.argv, check=False)
        return subprocess.CompletedProcess(step.argv, completed.returncode, "", "")
    if step.name == "assert_factory_state":
        # Longer than a shell round trip should need, but the first exec into a freshly created
        # sandbox waits on the supervisor relay.
        return subprocess.run(step.argv, capture_output=True, text=True, timeout=120, check=False)
    return subprocess.run(step.argv, capture_output=True, text=True, check=False)


def cmd_contained(args: argparse.Namespace) -> int:
    """Run the factory inside an OpenShell sandbox."""
    scope_error = _check_scope(args)
    if scope_error:
        print(f"Error: {scope_error}", file=sys.stderr)
        return 2

    project_path = Path(args.path).expanduser().resolve()
    if not project_path.is_dir():
        print(
            f"Error: {project_path} is not a directory. `factory contained` currently requires an "
            "existing project; ideas and GitHub URLs arrive with the transport work.",
            file=sys.stderr,
        )
        return 2

    try:
        plan = _write_policy(_build_plan(args, project_path))
        build_pod = _render_build_pod(args, project_path)
    except DivisionError as exc:
        # A half-configured division is worse than none: a sandbox with an allowlisted MCP endpoint
        # but no bind mount looks healthy and fails deep inside the first build.
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    # Warnings go to stderr and never change the exit code. Growth context in particular is a
    # "your numbers are not comparable" problem, not a "this cannot run" problem.
    for warning in plan.warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    if build_pod is not None:
        for finding in audit_pod(build_pod):
            # A supplied manifest is used verbatim, but never silently: an override that asks for
            # privilege is exactly what an operator needs told, and refusing it outright would make
            # the override useless.
            print(f"Warning: build pod exceeds the design's privilege budget — {finding}", file=sys.stderr)

    if dry_run_enabled():
        return _emit_dry_run(plan, build_pod)

    if shutil.which("openshell") is None:
        print(
            "Error: `openshell` is not installed. Install it with `uv tool install openshell`, "
            f"or set {DRY_RUN_ENV}=1 to compose the commands without running them.",
            file=sys.stderr,
        )
        return 1

    steps = list(plan_steps(plan))
    if args.division == "k8s":
        # Before the sandbox: the context upload and the ImageStream are the two things the agent
        # cannot do for itself, and a sandbox that comes up without them fails on its first build
        # with an output reference that cannot be resolved.
        try:
            steps = _k8s_bootstrap(_k8s_build_spec(args, project_path), project_path) + steps
        except DivisionError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    for step in steps:
        if step.name == "run":
            print(f"Factory running in OpenShell sandbox: {plan.name}")
            print(f"  openshell logs {plan.name}")
            print(f"  openshell sandbox delete {plan.name}")
        try:
            result = _run_step(step)
        except KeyboardInterrupt:
            # Interrupting here kills the local client, not the sandbox: the factory keeps running
            # inside it, keeps calling agents, and keeps spending tokens with nobody watching.
            # Saying so is the difference between a stopped run and a silently orphaned one.
            print(
                f"\nInterrupted. The sandbox {plan.name} is still running the factory — the "
                "interrupt reached this client, not the sandbox. Stop it with:\n"
                f"  openshell sandbox delete {plan.name}\n"
                f"Or watch what it is doing with:\n  openshell logs {plan.name}",
                file=sys.stderr,
            )
            return 130
        if result.returncode == 0:
            continue
        if step.name == "run":
            # The factory's own exit code, not a provisioning failure. Pass it through unchanged and
            # do not dress it up as an OpenShell error — the sandbox did its job.
            return result.returncode
        if step.name == "create" and "already exists" in (result.stderr + result.stdout):
            # Names the sandbox and the one command that clears it. Reusing it silently would run
            # the factory against whatever the previous run left in /sandbox, which is the kind of
            # stale-state bug that costs an afternoon to notice.
            print(
                f"Error: sandbox {plan.name} already exists — a previous run left it behind. "
                f"Inspect it with `openshell sandbox exec --name {plan.name} -- sh -lc 'ls'`, or "
                f"remove it with `openshell sandbox delete {plan.name}` and retry. Refusing to "
                "reuse it: its /sandbox tree is from the earlier run, not this one.",
                file=sys.stderr,
            )
            return result.returncode
        if step.name == "probe_writable":
            print(
                f"Error: the sandbox cannot write {plan.sandbox_path}. The bind mount is there, "
                "but it carries the host's ownership through unchanged and the sandbox identity "
                "is a different UID — so the division is read-only: the agent cannot write "
                ".mcp.json, cannot edit a Containerfile, and cannot run the build-fix-rebuild "
                "loop. Rebuild the sandbox image with the UID that owns the project on the host:\n"
                "  podman build --build-arg SANDBOX_UID=$(id -u) "
                "-f containers/sandbox/Containerfile -t <image> .\n"
                f"and pass it with --image. Refusing to continue: every write would fail.",
                file=sys.stderr,
            )
            return 1
        if step.name == "assert_factory_state":
            # The failure this whole design exists to make loud. A silent miss here means the
            # factory boots into a fresh-project state and starts scoring from zero.
            print(
                f"Error: .factory/ did not survive transfer — {plan.sandbox_path}/.factory/"
                "config.json is missing inside the sandbox. This is the .gitignore trap: "
                "OpenShell applies .gitignore filtering to uploads by default and this project "
                "gitignores .factory/. Refusing to continue into a fresh-project state.",
                file=sys.stderr,
            )
            return 1
        print(
            f"Error: `openshell` step '{step.name}' failed with exit code {result.returncode}.\n"
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
        return result.returncode

    print(f"Sandbox {plan.name} is still up: `openshell sandbox delete {plan.name}` to remove it.")
    return 0
