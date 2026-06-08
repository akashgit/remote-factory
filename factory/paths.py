"""Canonical path resolver for user-level disk artifacts under ~/.factory/.

All user-home paths should go through this module. Override the root
with FACTORY_HOME for testing or custom installs.
"""

from __future__ import annotations

import os
from pathlib import Path


def factory_home() -> Path:
    """Return the root of user-level factory artifacts (~/.factory/)."""
    override = os.environ.get("FACTORY_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".factory"


def registry_path() -> Path:
    return factory_home() / "registry.json"


def config_path() -> Path:
    return factory_home() / "config.toml"


def playbooks_dir() -> Path:
    return factory_home() / "playbooks"


def profile_path() -> Path:
    return factory_home() / "profile.md"
