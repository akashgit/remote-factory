"""Resolving how a contained run reaches inference — by shape, never by material.

**This section of the design reverses the previous one (spec §3.5).** There is no gateway to
terminate inference, so the container holds credential material directly. Pretending otherwise
would leave the runtime unable to make a single agent call, so the honest thing is to name exactly
what crosses and refuse to guess anything else.

Three supported shapes, all explicit:

| Backend       | What crosses the boundary                                             |
|---------------|-----------------------------------------------------------------------|
| Anthropic API | `ANTHROPIC_API_KEY`                                                   |
| Vertex        | `CLAUDE_CODE_USE_VERTEX`, `CLOUD_ML_REGION`, `ANTHROPIC_VERTEX_PROJECT_ID`, plus ADC by mounting `~/.config/gcloud` read-only |
| Profile       | nothing — a `[credentials.<name>]` section in the mounted `~/.factory/config.toml` is already inside |

A shape's `detail` reports which backend, which model, and which variable or file supplied it. It
never prints material: a check whose purpose is configuration must not become a way to print a key.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Vertex needs all three to reach the endpoint; the ADC file supplies the actual credential.
VERTEX_VARS = ("CLAUDE_CODE_USE_VERTEX", "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID")
ADC_DIR = Path("~/.config/gcloud").expanduser()
ADC_HOME_RELATIVE = ".config/gcloud"
ADC_FILE = "application_default_credentials.json"

# Two settings that are required against the Vertex backend specifically and are not optional.
# On the project this was developed against, `claude-sonnet-5` has a per-minute token quota of zero
# and every call 429s; `claude-sonnet-4-5` in `us-east5` is the working combination. These are
# properties of that Vertex project rather than of the runtime, which is why the model is a warning
# naming the symptom and not a hardcoded substitution.
VERTEX_PINNED_ENV = {"MAX_THINKING_TOKENS": "0"}

FACTORY_CONFIG = Path("~/.factory/config.toml").expanduser()


@dataclass(frozen=True)
class CredentialShape:
    """How a run reaches inference, described without naming any material.

    `home_mounts` pairs a host path with a path *relative to the container's home directory*, not
    an absolute one. The workspace is mounted path-preservingly (spec §2.5) but a credential store
    is not: gcloud looks under `$HOME/.config/gcloud` inside the container, and the container's home
    is not the host's. Leaving the destination home-relative keeps that mapping in one place — the
    caller, which is the only thing that knows the container's home.
    """

    backend: str
    ok: bool
    detail: str
    env: dict[str, str] = field(default_factory=dict)
    home_mounts: tuple[tuple[Path, str], ...] = field(default=())
    warnings: tuple[str, ...] = field(default=())
    fix: str | None = None


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes")


def resolve_credentials(
    environ: dict[str, str] | None = None, *, config_path: Path | None = None
) -> CredentialShape:
    """Determine which backend a contained run would use, and what has to cross for it to work.

    Checked in the order a user would expect to win: an explicitly configured Vertex setup, then a
    direct API key, then a credential profile already sitting in the mounted `~/.factory/`. The
    profile case comes last because it needs `--profile` in the payload to take effect, which the
    host cannot see — the payload after `--` is opaque by design (spec §2.4).
    """
    env = dict(os.environ if environ is None else environ)
    config = config_path or FACTORY_CONFIG

    if _truthy(env.get("CLAUDE_CODE_USE_VERTEX")):
        return _vertex_shape(env)
    if env.get("ANTHROPIC_API_KEY", "").strip():
        return CredentialShape(
            backend="anthropic",
            ok=True,
            detail=(
                f"Anthropic API, key from ANTHROPIC_API_KEY, model {_model(env)}. The key crosses "
                "into the container (spec §3.5)."
            ),
            env={"ANTHROPIC_API_KEY": env["ANTHROPIC_API_KEY"]},
        )
    profiles = _credential_profiles(config)
    if profiles:
        return CredentialShape(
            backend="profile",
            ok=True,
            detail=(
                f"no backend variable set, but {config} defines credential profile(s): "
                f"{', '.join(profiles)}. `~/.factory/` is mounted read-write, so `--profile <name>` "
                "in the payload resolves inside the container with nothing forwarded."
            ),
        )
    return CredentialShape(
        backend="none",
        ok=False,
        detail=(
            "no inference configuration found: CLAUDE_CODE_USE_VERTEX is unset, ANTHROPIC_API_KEY "
            f"is unset, and {config} defines no credential profiles"
        ),
        fix=(
            "export ANTHROPIC_API_KEY=... and re-run with --forward ANTHROPIC_API_KEY, or "
            "configure Vertex (CLAUDE_CODE_USE_VERTEX=1 CLOUD_ML_REGION=... "
            "ANTHROPIC_VERTEX_PROJECT_ID=... plus `gcloud auth application-default login`), or add "
            f"a [credentials.<name>] section to {config}"
        ),
    )


def _vertex_shape(env: dict[str, str]) -> CredentialShape:
    missing = [name for name in VERTEX_VARS if not env.get(name, "").strip()]
    adc = ADC_DIR / ADC_FILE
    if not adc.exists():
        missing.append(str(adc))
    forwarded = {name: env[name] for name in VERTEX_VARS if env.get(name, "").strip()}
    forwarded.update(VERTEX_PINNED_ENV)
    detail = (
        f"Vertex, project {env.get('ANTHROPIC_VERTEX_PROJECT_ID', '<unset>')} in "
        f"{env.get('CLOUD_ML_REGION', '<unset>')}, model {_model(env)}, credential from "
        f"Application Default Credentials at {ADC_DIR}"
    )
    if missing:
        return CredentialShape(
            backend="vertex",
            ok=False,
            detail=f"{detail} — missing: {', '.join(missing)}",
            env=forwarded,
            home_mounts=((ADC_DIR, ADC_HOME_RELATIVE),) if ADC_DIR.is_dir() else (),
            fix=(
                "set CLAUDE_CODE_USE_VERTEX=1, CLOUD_ML_REGION and ANTHROPIC_VERTEX_PROJECT_ID, "
                "then `gcloud auth application-default login`"
            ),
        )
    return CredentialShape(
        backend="vertex",
        ok=True,
        detail=detail,
        env=forwarded,
        home_mounts=((ADC_DIR, ADC_HOME_RELATIVE),),
        warnings=(
            "Vertex: MAX_THINKING_TOKENS=0 is pinned into the container, and an explicit --model is "
            "required. Without one, a model whose per-minute token quota is zero 429s every call "
            "and the run looks like a network fault.",
        ),
    )


def _model(env: dict[str, str]) -> str:
    """Which model the run would use, and where that came from. Never a credential."""
    for name in ("FACTORY_MODEL", "ANTHROPIC_MODEL"):
        value = env.get(name, "").strip()
        if value:
            return f"{value} (from {name})"
    try:
        with FACTORY_CONFIG.open("rb") as handle:
            configured = str(tomllib.load(handle).get("defaults", {}).get("model", "")).strip()
    except (OSError, tomllib.TOMLDecodeError):
        configured = ""
    if configured:
        return f"{configured} (from {FACTORY_CONFIG} [defaults])"
    return "<unset — pass --model in the payload>"


def _credential_profiles(config_path: Path) -> list[str]:
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    credentials = data.get("credentials")
    return sorted(credentials) if isinstance(credentials, dict) else []


def vertex_model_warning(shape: CredentialShape, factory_args: list[str]) -> str | None:
    """Warn when a Vertex run carries no explicit `--model`.

    A warning rather than an error, and the one place the host looks inside the payload beyond path
    rewriting: it inspects for the presence of a token, never its meaning, so it cannot break when
    the CLI grows a subcommand (spec §2.4). Left unwarned, the failure arrives as a 429 storm from
    a model whose quota is zero, which reads like a network fault.
    """
    if shape.backend != "vertex":
        return None
    if any(token == "--model" or token.startswith("--model=") for token in factory_args):
        return None
    return (
        "Vertex backend with no --model in the payload. On a project whose default model has a "
        "zero per-minute token quota, every call 429s and the run reads as a network failure. "
        "Pass --model explicitly, for example: -- ceo <path> --model claude-sonnet-4-5"
    )
