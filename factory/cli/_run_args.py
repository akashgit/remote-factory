"""Re-serializing a parsed `factory` invocation for a wrapper to run somewhere else.

Both `factory tmux` and `factory contained` take a parsed argument namespace and rebuild the
`factory ceo ...` command line that a detached shell — a tmux session or an OpenShell sandbox — will
run. The argument re-serialization is identical for both. The environment handling is not: tmux
launches a shell on the same machine, so forwarding the caller's inference configuration is exactly
right, while a sandbox reaches inference through the OpenShell gateway, so forwarding the same
variables actively breaks it.

That divergence is why the environment policy is a parameter instead of a constant.
"""

from __future__ import annotations

import argparse
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from factory.openshell import DRY_RUN_ENV as SANDBOX_DRY_RUN_ENV
from factory.sandbox import (
    SANDBOX_ENV_VAR,
    SANDBOX_INFERENCE_API_KEY,
    SANDBOX_INFERENCE_BASE_URL,
)


@dataclass(frozen=True)
class EnvPolicy:
    """Which environment variables cross into a wrapped invocation, and what replaces them.

    `forward_prefixes` selects variables from the caller's environment by prefix. `drop_prefixes`
    and `drop_keys` then remove variables that a prefix swept in but that must not cross — the
    prefixes are coarse, and a policy needs a way to say "everything under FACTORY_, except this".
    `substitutions` are applied last and always win, so a policy can both refuse to forward a
    variable and pin a different value for it.
    """

    forward_prefixes: tuple[str, ...]
    drop_prefixes: tuple[str, ...] = field(default=())
    drop_keys: tuple[str, ...] = field(default=())
    substitutions: tuple[tuple[str, str], ...] = field(default=())

    def resolve(self, environ: dict[str, str]) -> dict[str, str]:
        """Return the environment the wrapped invocation should see, sorted by key."""
        forwarded = {
            k: v
            for k, v in environ.items()
            if k.startswith(self.forward_prefixes)
            and not (self.drop_prefixes and k.startswith(self.drop_prefixes))
            and k not in self.drop_keys
        }
        forwarded.update(dict(self.substitutions))
        return dict(sorted(forwarded.items()))


# `factory tmux` runs on the caller's machine against the caller's inference setup. Forwarding
# CLAUDE_CODE_* and CLOUD_ML_* is what makes a Vertex-configured host work in a tmux session.
TMUX_ENV_POLICY = EnvPolicy(
    forward_prefixes=(
        "FACTORY_",
        "ANTHROPIC_",
        "BOBSHELL_",
        "OPENAI_",
        "CODEX_",
        "CLAUDE_CODE_",
        "CLOUD_ML_",
    ),
)

# Inside a sandbox the same variables are poison. `CLAUDE_CODE_USE_VERTEX` plus `CLOUD_ML_REGION`
# make Claude Code dial Vertex directly, which the sandbox's deny-by-default egress policy refuses —
# and the resulting error reads like a network policy fault rather than a misconfiguration, so it
# costs a long time to diagnose.
#
# No credential prefix is forwarded at all. Spec §8 is unambiguous that credentials are configured
# on the gateway and never inside the sandbox, and OpenShell providers are the mechanism for every
# backend, not just Vertex. Forwarding `OPENAI_`/`CODEX_`/`BOBSHELL_` "so the other runners still
# work" would put live API keys into a sandbox argv — and into any dry-run output or evidence file
# that captures it. Runners other than Claude need an OpenShell provider attached instead.
#
# `FACTORY_` is forwarded because it carries configuration rather than secrets, minus the host-only
# controls below: dry-run is a decision about *this* invocation, and forwarding it would put the
# sandboxed factory into dry-run too.
SANDBOX_ENV_POLICY = EnvPolicy(
    forward_prefixes=("FACTORY_",),
    drop_prefixes=("FACTORY_EVAL_",),
    drop_keys=(SANDBOX_DRY_RUN_ENV,),
    substitutions=(
        ("ANTHROPIC_BASE_URL", SANDBOX_INFERENCE_BASE_URL),
        ("ANTHROPIC_API_KEY", SANDBOX_INFERENCE_API_KEY),
        (SANDBOX_ENV_VAR, "1"),
    ),
)

# Values under these key fragments are masked wherever a composed environment is printed or logged.
# Substituted values are never masked — `ANTHROPIC_API_KEY=unused` is a placeholder whose presence
# is the thing being verified, and hiding it would defeat the check.
_SECRET_KEY_FRAGMENTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
_REDACTED = "<redacted>"


def redact_env(env: dict[str, str], policy: EnvPolicy) -> dict[str, str]:
    """Mask secret-looking forwarded values so they cannot reach logs or evidence files."""
    pinned = dict(policy.substitutions)
    return {
        key: (
            value
            if key in pinned or not any(f in key.upper() for f in _SECRET_KEY_FRAGMENTS)
            else _REDACTED
        )
        for key, value in env.items()
    }


def redact_argv(argv: list[str], policy: EnvPolicy) -> list[str]:
    """Mask secret-looking `--env KEY=VALUE` pairs in a composed command line."""
    pinned = dict(policy.substitutions)
    out: list[str] = []
    for index, token in enumerate(argv):
        previous = argv[index - 1] if index else ""
        if previous == "--env" and "=" in token:
            key, _, value = token.partition("=")
            if key not in pinned and any(f in key.upper() for f in _SECRET_KEY_FRAGMENTS):
                out.append(f"{key}={_REDACTED}")
                continue
            out.append(f"{key}={value}")
            continue
        out.append(token)
    return out


def build_run_args(
    args: argparse.Namespace,
    project_path: Path,
    model: str | None,
    *,
    headless: bool = False,
) -> str:
    """Build the 'factory ceo ...' command string from parsed args.

    `headless` exists for the sandbox. Without it the CEO starts an interactive `claude` session,
    and the channel `factory contained` runs it over is a pipe with no terminal attached: the
    session's output never reaches the operator, who sees a blank screen while real agents run and
    real tokens burn. Ctrl-C then kills the local client and leaves the sandboxed run going.
    """
    parts = [f"factory ceo {project_path}"]
    if headless:
        parts.append("--headless")
    if args.mode:
        parts.append(f"--mode {args.mode}")
    if model:
        parts.append(f"--model {shlex.quote(model)}")
    if getattr(args, "no_github", False):
        parts.append("--no-github")
    if getattr(args, "profile", None):
        parts.append(f"--profile {shlex.quote(args.profile)}")
    if getattr(args, "focus", None):
        parts.append(f"--focus {shlex.quote(args.focus)}")
    if getattr(args, "refine", None):
        parts.append(f"--refine {shlex.quote(args.refine)}")
    if getattr(args, "clean_pr", None) is True:
        parts.append("--clean-pr")
    elif getattr(args, "clean_pr", None) is False:
        parts.append("--no-clean-pr")
    if getattr(args, "runner", None):
        parts.append(f"--runner {shlex.quote(args.runner)}")
    if getattr(args, "prompt", None):
        parts.append(f"--prompt {shlex.quote(args.prompt)}")
    if getattr(args, "branch", None):
        parts.append(f"--branch {shlex.quote(args.branch)}")
    if getattr(args, "min_growth", None) is not None:
        parts.append(f"--min-growth {args.min_growth}")
    if getattr(args, "max_new", None) is not None:
        parts.append(f"--max-new {args.max_new}")
    if getattr(args, "discover_only", False):
        parts.append("--discover-only")
    if getattr(args, "bg_agents", False):
        parts.append("--bg-agents")
    if getattr(args, "tmux_persist", False):
        parts.append("--tmux-persist")
    if getattr(args, "use_profile", False):
        parts.append("--use-profile")
    return " ".join(parts)


def build_env_exports(environ: dict[str, str], policy: EnvPolicy) -> list[str]:
    """Build `export KEY=value` statements for the variables the policy lets through."""
    return [f"export {key}={shlex.quote(val)}" for key, val in policy.resolve(environ).items()]
