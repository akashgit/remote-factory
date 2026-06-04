"""AgentLaunchConfig — immutable config bundle for agent invocations."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


PermissionMode = Literal["permissionless", "auto-edit", "suggest"]
LaunchMode = Literal["headless", "interactive"]


class AgentLaunchConfig(BaseModel):
    """Immutable configuration for launching an agent invocation.

    Models the **semantic operations** you perform with an agent CLI,
    independent of the specific CLI flags. Each Agent implementation
    maps these to its own CLI arguments.

    Prompt fields:
        system_prompt: Replace the default system prompt entirely.
        append_system_prompt: Append to the default system prompt.
        task: The user-facing task / initial message.

    Tool control:
        allowed_tools: Whitelist of tool names (e.g. ["Bash", "Edit"]).
        disallowed_tools: Blacklist of tool names (e.g. ["WebSearch"]).

    Execution control:
        model: Model override (e.g. "haiku", "o4-mini").
        permissions: Permission/sandbox mode.
        timeout: Max execution time in seconds.
        max_budget_usd: Optional spending cap.

    Session identity:
        session_id: Internal tracking ID.
        session_name: Human-readable name for resume/identification.
        role: Agent role name (for logging/prefixing).
        mode: Whether this is a headless or interactive invocation.

    Workspace:
        project_path: Working directory for the subprocess.
        add_dirs: Additional directories to grant access to.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    # -- Prompt --
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    task: str
    # Legacy alias: callers passing `prompt=` get mapped to append_system_prompt
    # in the compositor (AgentRunner). Not a config field itself.

    # -- Tool control --
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None

    # -- Execution control --
    model: str | None = None
    timeout: float = 600.0
    permissions: PermissionMode = "permissionless"
    max_budget_usd: float | None = None

    # -- Session identity --
    session_id: str = ""
    session_name: str | None = None
    role: str = "unknown"
    mode: LaunchMode = "headless"

    # -- Workspace --
    project_path: Path
    add_dirs: list[Path] | None = None
