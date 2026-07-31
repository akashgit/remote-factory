"""OpenShell integration — composing the commands that run the factory inside a sandbox.

Everything that knows about the `openshell` CLI lives here. That is deliberate: OpenShell is alpha
with an explicitly unstable surface, so when a flag changes there should be exactly one file to fix.

The module composes command lines and does not execute them; execution and error handling live in
`factory.cli.contained`. Splitting the two is what makes `FACTORY_OPENSHELL_DRY_RUN=1` honest —
dry-run prints the same argv the real path would run, rather than a separate rendering that can
drift from it.

Verified against `openshell 0.0.92`. The flags used below (`--upload`, `--no-git-ignore`, `--env`,
`--label`, `--from`, `--driver-config-json`, `sandbox list --selector`) all exist in that release.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

# Mirrors the existing FACTORY_BOB_DRY_RUN / FACTORY_CODEX_DRY_RUN convention.
DRY_RUN_ENV = "FACTORY_OPENSHELL_DRY_RUN"

# Pinned and recorded in every evidence file so a sandbox failure can be attributed to the runtime
# rather than to the factory (spec §11).
PINNED_VERSION = "0.0.92"

IMAGE_ENV = "FACTORY_SANDBOX_IMAGE"
DEFAULT_IMAGE = "ghcr.io/akashgit/remote-factory/factory-sandbox:latest"

# The workspace root OpenShell guarantees is writable. Uploads are confined to it.
SANDBOX_WORKSPACE = "/sandbox"

# Where user-local configuration is staged before the in-sandbox command copies it into $HOME.
# $HOME is not knowable at compose time — it belongs to the image's identity — so the copy happens
# inside the sandbox where the shell can expand it.
#
# Staged under /tmp rather than the workspace because an upload keeps its own directory name: the
# host's `~/.factory` always arrives as `.factory` under whatever parent it is given, and the one
# parent it must not be given is the workspace, which is also $HOME on this image — the staging copy
# would then be its own destination.
USER_CONFIG_STAGE = "/tmp/.factory"

LABEL_PROJECT = "factory.project"
LABEL_NAME = "factory.name"

# The two variables that feed growth dimensions. They merge 50/50 into the composite score, so their
# absence does not break a run — it silently makes the run's scores incomparable to host scores.
GROWTH_CONTEXT_VARS = ("FACTORY_MANAGED_DIRS", "FACTORY_VAULT_PATH")


def dry_run_enabled(env: dict[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(DRY_RUN_ENV, "").strip().lower() in ("1", "true", "yes")


def project_hash(project_path: Path) -> str:
    """Stable identifier for a project path, used as a sandbox label value."""
    return hashlib.sha1(str(project_path).encode()).hexdigest()[:12]


# The gateway rejects longer names outright: `name exceeds maximum length (26 > 19)`, at create
# time, after the plan is built and before anything exists. 19 is OpenShell's limit, not ours.
MAX_SANDBOX_NAME = 19
_HASH_SUFFIX = 6


def sandbox_name(project_path: Path) -> str:
    """Derive a sandbox name from a project path, within OpenShell's 19-character limit.

    The hash suffix is what keeps two same-named projects in different directories apart, so it is
    never the part that gets truncated — the readable stem is. Identity for lookup lives in the
    labels (`factory.project`, `factory.name`), which have no length limit, so a truncated stem
    costs nothing but legibility.
    """
    digest = project_hash(project_path)[:_HASH_SUFFIX]
    stem = "".join(c if c.isalnum() else "-" for c in project_path.name.lower()).strip("-")
    stem = stem[: MAX_SANDBOX_NAME - _HASH_SUFFIX - 1].strip("-") or "factory"
    return f"{stem}-{digest}"


def sandbox_project_path(project_path: Path) -> str:
    """Where the project tree lands inside the sandbox."""
    return f"{SANDBOX_WORKSPACE}/{project_path.name}"


def resolve_image(env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    return source.get(IMAGE_ENV) or DEFAULT_IMAGE


@dataclass(frozen=True)
class Upload:
    """One transfer into the sandbox.

    `dest` is the **parent directory**, not the resulting path: OpenShell places the uploaded item
    inside `dest` under its own basename. Passing the intended full path instead produces a doubled
    leaf — `--upload /host/proj:/sandbox/proj` lands the tree at `/sandbox/proj/proj`, where nothing
    that later `cd`s to `/sandbox/proj` can see it. Verified against a live gateway.

    `respect_gitignore` is the whole reason this is a type rather than a tuple. OpenShell applies
    `.gitignore` filtering to uploads by default, and this project's own convention is to gitignore
    `.factory/` — so the naive single upload silently drops config.json, eval_profile.json, and the
    entire experiment history, and the factory boots into a fresh-project state and re-runs
    discovery. Nothing errors. The scores just start from zero.
    """

    local: Path
    dest: str
    respect_gitignore: bool = True
    description: str = ""


@dataclass(frozen=True)
class SandboxPlan:
    """Everything needed to provision one sandbox, in the order it must happen."""

    name: str
    image: str
    project_path: Path
    sandbox_path: str
    env: dict[str, str]
    labels: dict[str, str]
    uploads: tuple[Upload, ...]
    run_command: str
    gateway: str | None = None
    driver_config: dict[str, object] | None = None
    division_policy: dict[str, object] | None = None
    policy_path: str | None = None
    warnings: tuple[str, ...] = field(default=())
    # False unless the host actually has `.factory/config.json` to lose. The check exists to catch a
    # transfer that *silently* dropped state that did exist; running it otherwise reports a file
    # that was never written as the .gitignore trap, which sends the reader looking at uploads
    # instead of at the project.
    assert_factory_state: bool = True
    # Set when the project reaches the sandbox as a bind mount rather than a copy. The mount carries
    # the host's ownership through unchanged, so the sandbox identity may see the directory and
    # still not be able to write it — which the division needs, and which fails as a bare
    # `Permission denied` on a path that is plainly there.
    probe_writable: bool = False


def _gateway_flags(gateway: str | None) -> list[str]:
    return ["--gateway", gateway] if gateway else []


# `sandbox create` runs its command and blocks until it exits, so the sandbox is created with a
# no-op and the factory is started afterwards with `sandbox exec`. Creating it with the real command
# would mean the uploads that follow never run — `create` would still be streaming the factory's
# output when the sandbox needed `.factory/` to already be there. Without `--no-keep` the sandbox
# outlives this command, which is what makes the split possible.
BOOTSTRAP_COMMAND = "true"


def build_create_command(plan: SandboxPlan) -> list[str]:
    """Compose `openshell sandbox create`.

    Only the project tree is uploaded here. Transfers that need `--no-git-ignore` cannot ride along:
    the flag applies to every `--upload` on the command, so using it here would also drag in
    everything the project's own `.gitignore` excludes — build outputs, virtualenvs, caches. They
    are issued as separate `sandbox upload` calls instead.
    """
    cmd = ["openshell", "sandbox", "create", "--name", plan.name, "--from", plan.image]
    cmd += _gateway_flags(plan.gateway)
    for key, value in sorted(plan.labels.items()):
        cmd += ["--label", f"{key}={value}"]
    for key, value in sorted(plan.env.items()):
        cmd += ["--env", f"{key}={value}"]
    for upload in plan.uploads:
        if upload.respect_gitignore:
            cmd += ["--upload", f"{upload.local}:{upload.dest}"]
    if plan.driver_config is not None:
        cmd += ["--driver-config-json", json.dumps(plan.driver_config, sort_keys=True)]
    if plan.policy_path is not None:
        cmd += ["--policy", plan.policy_path]
    cmd += ["--no-tty", "--", "sh", "-lc", BOOTSTRAP_COMMAND]
    return cmd


def build_exec_command(plan: SandboxPlan) -> list[str]:
    """Compose the `openshell sandbox exec` that actually starts the factory.

    `--name` is a flag, not a positional. `openshell sandbox exec <name> -- ...` parses <name> as the
    first word of the command and silently targets the *last-used* sandbox instead, which fails as
    `<name>: command not found` — a message that names the sandbox and looks like a sandbox problem.
    """
    cmd = ["openshell", "sandbox", "exec", "--name", plan.name]
    cmd += _gateway_flags(plan.gateway)
    cmd += ["--", "sh", "-lc", plan.run_command]
    return cmd


def build_upload_commands(plan: SandboxPlan) -> list[list[str]]:
    """Compose the `openshell sandbox upload` calls that must bypass `.gitignore`."""
    commands: list[list[str]] = []
    for upload in plan.uploads:
        if upload.respect_gitignore:
            continue
        cmd = ["openshell", "sandbox", "upload"]
        cmd += _gateway_flags(plan.gateway)
        cmd += [plan.name, str(upload.local), upload.dest, "--no-git-ignore"]
        commands.append(cmd)
    return commands


def build_assert_command(plan: SandboxPlan, relative_path: str) -> list[str]:
    """Compose the check that a transferred file actually arrived.

    A transfer that silently drops `.factory/` is indistinguishable from a fresh project, so the
    transfer is not trusted — it is verified.
    """
    cmd = ["openshell", "sandbox", "exec", "--name", plan.name]
    cmd += _gateway_flags(plan.gateway)
    cmd += ["--", "test", "-f", f"{plan.sandbox_path}/{relative_path}"]
    return cmd


def build_writable_probe_command(plan: SandboxPlan) -> list[str]:
    """Compose the check that the sandbox identity can actually write the bind-mounted project."""
    cmd = ["openshell", "sandbox", "exec", "--name", plan.name]
    cmd += _gateway_flags(plan.gateway)
    cmd += ["--", "test", "-w", plan.sandbox_path]
    return cmd


def build_list_command(project_path: Path, gateway: str | None = None) -> list[str]:
    """Compose the label-selector lookup that replaces a session-mapping file.

    Sandboxes are found through OpenShell's own labels rather than a `tmux_sessions.json` analogue,
    so `openshell sandbox list/delete/logs` keep working unmodified.
    """
    cmd = ["openshell", "sandbox", "list"]
    cmd += _gateway_flags(gateway)
    cmd += ["--selector", f"{LABEL_PROJECT}={project_hash(project_path)}", "-o", "json"]
    return cmd


def growth_context_warning(env: dict[str, str] | None = None) -> str | None:
    """Return a warning when growth-dimension context is missing, or None when it is present.

    Never an error. A sandbox without this context still runs; its eval scores are simply not
    comparable to host scores, and the operator needs to know that before comparing them.
    """
    source = os.environ if env is None else env
    missing = [name for name in GROWTH_CONTEXT_VARS if not source.get(name, "").strip()]
    if not missing:
        return None
    return (
        f"Growth context not configured: {', '.join(missing)} "
        f"{'is' if len(missing) == 1 else 'are'} unset. Growth dimensions merge 50/50 into the "
        "composite score, so eval scores computed in this sandbox are NOT comparable to host "
        "scores. Continuing anyway."
    )


def build_run_command(
    sandbox_path: str,
    factory_args: str,
    *,
    stage_user_config: bool,
    mcp_config: dict[str, object] | None = None,
    files: dict[str, str] | None = None,
) -> str:
    """Compose the shell command the sandbox runs.

    The user-config copy happens inside the sandbox because `$HOME` belongs to the image's identity
    and is not knowable when the command is composed. The MCP registration is written the same way
    and for the same reason — it belongs next to the project, inside. `files` carries anything else
    the division needs to hand the agent, such as the k8s division's rendered Build manifest.
    """
    parts: list[str] = []
    if stage_user_config:
        parts.append(
            f'if [ -d {USER_CONFIG_STAGE} ]; then '
            f'mkdir -p "$HOME/.factory" && cp -a {USER_CONFIG_STAGE}/. "$HOME/.factory/"; fi'
        )
    parts.append(f"cd {sandbox_path}")
    if mcp_config is not None:
        payload = shlex.quote(json.dumps(mcp_config, sort_keys=True))
        parts.append(f"printf '%s' {payload} > .mcp.json")
    for relative, content in sorted((files or {}).items()):
        directory = str(Path(relative).parent)
        if directory not in (".", ""):
            parts.append(f"mkdir -p {shlex.quote(directory)}")
        parts.append(f"printf '%s' {shlex.quote(content)} > {shlex.quote(relative)}")
    parts.append(f"exec {factory_args}")
    return " && ".join(parts)


@dataclass(frozen=True)
class Step:
    """One provisioning command, named so a failure can say which stage broke."""

    name: str
    argv: list[str]


def plan_steps(plan: SandboxPlan) -> list[Step]:
    """The full provisioning sequence as ordered, named steps.

    This is what dry-run prints and what the real path executes, so the two cannot drift — a
    dry-run that renders a command the real path does not run is worse than no dry-run at all.
    """
    steps = [Step("create", build_create_command(plan))]
    if plan.probe_writable:
        steps.append(Step("probe_writable", build_writable_probe_command(plan)))
    steps += [Step("upload", cmd) for cmd in build_upload_commands(plan)]
    if plan.assert_factory_state:
        steps.append(Step("assert_factory_state", build_assert_command(plan, ".factory/config.json")))
    steps.append(Step("list", build_list_command(plan.project_path, plan.gateway)))
    steps.append(Step("run", build_exec_command(plan)))
    return steps
