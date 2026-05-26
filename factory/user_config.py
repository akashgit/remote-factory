"""Unified configuration — ~/.factory/config.toml with credential profiles.

Five-tier precedence: CLI flag > env var > profile credential > config.toml default > hardcoded.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

CONFIG_PATH: Path = Path("~/.factory/config.toml").expanduser()

_PROFILE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_CREDENTIAL_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

_SENSITIVE_FRAGMENTS = ("key", "token", "secret", "password", "api_key")

_cached_config: dict | None = None

_CONFIG_TEMPLATE = """\
# Factory configuration — ~/.factory/config.toml
#
# Values here are defaults. Environment variables and CLI flags take precedence.
# See: factory config show

[defaults]
# runner = "claude"                    # CLI backend: "claude", "bob", or "codex"
# model = ""                           # Claude model for agent subprocesses
# projects_dir = "~/factory-projects"  # Root for factory-managed projects
# tmux_persist = false                 # Launch agents in tmux windows

# [credentials.vertex]
# FACTORY_RUNNER = "claude"
# ANTHROPIC_API_KEY = "sk-ant-..."
#
# [credentials.bob]
# FACTORY_RUNNER = "bob"
# BOBSHELL_API_KEY = "..."
#
# [credentials.codex]
# FACTORY_RUNNER = "codex"
# CODEX_API_KEY = "..."
"""


def _validate_profile_name(name: str) -> None:
    if not _PROFILE_NAME_RE.match(name):
        raise ValueError(
            f"Invalid profile name {name!r}: must match [a-zA-Z0-9_-]+"
        )


def _validate_credential_keys(keys: dict[str, Any]) -> None:
    for k in keys:
        if not _CREDENTIAL_KEY_RE.match(k):
            raise ValueError(
                f"Invalid credential key {k!r}: must match [A-Z_][A-Z0-9_]*"
            )


def ensure_config_file() -> Path:
    """Create config file with secure permissions if it doesn't exist. Returns path."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(CONFIG_PATH), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return CONFIG_PATH
    try:
        os.write(fd, _CONFIG_TEMPLATE.encode())
    finally:
        os.close(fd)
    log.info("config_created", path=str(CONFIG_PATH))
    return CONFIG_PATH


def load_config(profile: str | None = None) -> dict:
    """Read ~/.factory/config.toml; apply credential profile overlay if given.

    Returns the parsed TOML dict. If the file doesn't exist, returns an empty dict.
    When a profile is specified, its ``[credentials.<name>]`` keys are injected
    into ``os.environ`` so normal env-var precedence resolves them.
    """
    if not CONFIG_PATH.exists():
        if profile:
            raise FileNotFoundError(
                f"Config file {CONFIG_PATH} not found — cannot load profile {profile!r}"
            )
        return {}

    with open(CONFIG_PATH, "rb") as f:
        data = tomllib.load(f)

    if profile:
        _validate_profile_name(profile)
        creds = data.get("credentials", {}).get(profile)
        if creds is None:
            available = list(data.get("credentials", {}).keys())
            raise KeyError(
                f"Profile {profile!r} not found in config.toml. "
                f"Available: {available}"
            )
        _validate_credential_keys(creds)
        for k, v in creds.items():
            os.environ.setdefault(k, str(v))
        log.info("profile_loaded", profile=profile, keys=list(creds.keys()))

    global _cached_config  # noqa: PLW0603
    _cached_config = data
    return data


def _get_cached_config() -> dict:
    """Return the cached config, loading from disk on first call."""
    global _cached_config  # noqa: PLW0603
    if _cached_config is None:
        _cached_config = load_config()
    return _cached_config


def resolve(
    key: str,
    *,
    cli_value: str | None = None,
    env_var: str | None = None,
    config: dict | None = None,
    default: str | None = None,
) -> str | None:
    """Five-tier precedence resolution.

    1. CLI flag (cli_value)
    2. Environment variable (env_var name looked up in os.environ)
    3. Profile credential — already injected into os.environ by load_config()
    4. config.toml [defaults] section (auto-loaded if config not passed)
    5. Hardcoded default

    Since profile credentials are injected into os.environ by load_config(),
    tiers 2 and 3 collapse into a single os.environ lookup.
    """
    if cli_value is not None:
        v = cli_value.strip()
        if v:
            return v

    if env_var:
        env_val = os.environ.get(env_var, "").strip()
        if env_val:
            return env_val

    effective_config = config if config is not None else _get_cached_config()
    defaults = effective_config.get("defaults", {})
    toml_val = defaults.get(key)
    if toml_val is not None:
        v = str(toml_val).strip()
        if v:
            return v

    return default


def is_sensitive(key: str) -> bool:
    """Return True if a config key name looks like it holds a secret."""
    lower = key.lower()
    return any(frag in lower for frag in _SENSITIVE_FRAGMENTS)


def mask_value(value: str) -> str:
    """Mask a secret value, showing only the last 4 chars."""
    if len(value) <= 4:
        return "****"
    return "*" * (len(value) - 4) + value[-4:]


def show_config(*, reveal: bool = False) -> str:
    """Return a human-readable view of the on-disk config with secrets masked."""
    if not CONFIG_PATH.exists():
        return f"No config file at {CONFIG_PATH}\nRun: factory config edit"

    with open(CONFIG_PATH, "rb") as f:
        data = tomllib.load(f)

    lines: list[str] = [f"# {CONFIG_PATH}", ""]

    defaults = data.get("defaults", {})
    if defaults:
        lines.append("[defaults]")
        for k, v in defaults.items():
            display = str(v)
            if not reveal and is_sensitive(k):
                display = mask_value(display)
            lines.append(f"  {k} = {display}")
        lines.append("")

    credentials = data.get("credentials", {})
    for profile_name, creds in credentials.items():
        lines.append(f"[credentials.{profile_name}]")
        for k, v in creds.items():
            display = str(v)
            if not reveal and is_sensitive(k):
                display = mask_value(display)
            lines.append(f"  {k} = {display}")
        lines.append("")

    for section_name, section_data in data.items():
        if section_name in ("defaults", "credentials"):
            continue
        if isinstance(section_data, dict):
            lines.append(f"[{section_name}]")
            for k, v in section_data.items():
                display = str(v)
                if not reveal and is_sensitive(k):
                    display = mask_value(display)
                lines.append(f"  {k} = {display}")
            lines.append("")

    return "\n".join(lines).rstrip()


def migrate_env_to_config() -> str:
    """Read current FACTORY_* env vars and write a starter config.toml.

    Requires tomli_w for TOML writing.
    """
    try:
        import tomli_w  # type: ignore[import-untyped,import-not-found]
    except ImportError:
        raise ImportError(
            "tomli_w is required for migration: pip install tomli_w"
        ) from None

    env_map = {
        "FACTORY_RUNNER": "runner",
        "FACTORY_MODEL": "model",
        "FACTORY_PROJECTS_DIR": "projects_dir",
        "FACTORY_VAULT_PATH": "vault_path",
        "FACTORY_PLAYBOOKS_DIR": "playbooks_dir",
        "FACTORY_REGISTRY_DIR": "registry_dir",
        "FACTORY_MANAGED_DIRS": "managed_dirs",
        "FACTORY_RUNNER_QUIET": "runner_quiet",
        "FACTORY_BOB_DRY_RUN": "bob_dry_run",
        "FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE": "bob_max_invocations_per_cycle",
        "FACTORY_CEO_RESPAWN_DISABLED": "ceo_respawn_disabled",
        "FACTORY_CEO_MAX_RESPAWNS": "ceo_max_respawns",
    }

    defaults: dict[str, str] = {}
    for env_key, toml_key in env_map.items():
        val = os.environ.get(env_key, "").strip()
        if val:
            defaults[toml_key] = val

    data: dict[str, dict] = {}
    if defaults:
        data["defaults"] = defaults

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(CONFIG_PATH), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise FileExistsError(
            f"Config file already exists at {CONFIG_PATH}. "
            "Remove it first or edit manually."
        ) from None
    try:
        content = tomli_w.dumps(data)
        os.write(fd, content.encode())
    finally:
        os.close(fd)

    count = len(defaults)
    return f"Migrated {count} env var(s) to {CONFIG_PATH}"
