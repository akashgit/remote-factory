"""Named Claude instance profiles — per-account env var sets."""

from __future__ import annotations

import json
import os
from pathlib import Path

PROFILES_DIR = Path.home() / ".factory" / "profiles"


def _profile_path(name: str) -> Path:
    return PROFILES_DIR / f"{name}.json"


def load_profile(name: str) -> dict[str, str]:
    """Load a profile by name and return its env vars dict.

    Raises FileNotFoundError if the profile doesn't exist.
    Raises ValueError if the profile is malformed.
    """
    path = _profile_path(name)
    if not path.exists():
        available = list_profiles()
        hint = f" Available: {', '.join(available)}" if available else " No profiles found."
        raise FileNotFoundError(f"Profile '{name}' not found at {path}.{hint}")

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Profile '{name}' is not valid JSON: {e}") from e

    env = data.get("env", {})
    if not isinstance(env, dict):
        raise ValueError(f"Profile '{name}': 'env' must be a dict of string key-value pairs")

    return {str(k): str(v) for k, v in env.items()}


def apply_profile(name: str) -> dict[str, str]:
    """Load a profile and apply its env vars to os.environ.

    Returns the raw env vars that were applied.
    """
    env_vars = load_profile(name)
    for key, value in env_vars.items():
        os.environ[key] = value
    return env_vars


def list_profiles() -> list[str]:
    """Return sorted list of available profile names."""
    if not PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))


def create_profile(name: str, env: dict[str, str]) -> Path:
    """Create or update a profile with the given env vars.

    Returns the path to the created profile file.
    """
    PROFILES_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Ensure existing dirs also have the right permissions
    PROFILES_DIR.chmod(0o700)
    path = _profile_path(name)
    data = {"env": env}
    path.write_text(json.dumps(data, indent=2) + "\n")
    # Restrict to owner read/write only — profiles may contain API keys
    path.chmod(0o600)
    return path


def delete_profile(name: str) -> None:
    """Delete a profile by name.

    Raises FileNotFoundError if the profile doesn't exist.
    """
    path = _profile_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Profile '{name}' not found at {path}")
    path.unlink()


def _mask(value: str) -> str:
    """Mask a secret value, showing only the first 4 and last 4 chars."""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def format_profile(name: str, *, reveal: bool = False) -> str:
    """Return a human-readable representation of a profile."""
    env_vars = load_profile(name)
    lines = [f"Profile: {name}"]
    secret_keys = {"key", "token", "secret", "password", "passwd", "credential"}
    for key, value in sorted(env_vars.items()):
        is_secret = any(s in key.lower() for s in secret_keys)
        display = value if (reveal or not is_secret) else _mask(value)
        lines.append(f"  {key}={display}")
    return "\n".join(lines)
