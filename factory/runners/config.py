"""AgentLaunchConfig — immutable config bundle for agent invocations."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


PermissionMode = Literal["permissionless", "auto-edit", "suggest"]
LaunchMode = Literal["headless", "interactive"]


class AgentLaunchConfig(BaseModel):
    """Immutable configuration for launching an agent invocation.

    Bundles all parameters needed to build a CLI command, construct
    the subprocess environment, and identify the session.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    session_id: str = ""
    project_path: Path
    prompt: str
    task: str
    role: str = "unknown"
    model: str | None = None
    timeout: float = 600.0
    permissions: PermissionMode = "permissionless"
    session_name: str | None = None
    mode: LaunchMode = "headless"
