"""Agent runner — load prompts and invoke Claude Code instances."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

AgentRole = Literal["researcher", "strategist", "builder", "reviewer", "evaluator", "archivist"]

# Directory containing base agent prompts (shipped with the factory)
_PROMPTS_DIR = Path(__file__).parent / "prompts"


def resolve_prompt(role: AgentRole, project_path: Path | None = None) -> str:
    """Resolve the prompt for an agent role.

    Resolution order:
    1. Project-specific override: <project>/.factory/agents/<role>.md
    2. Factory default: factory/agents/prompts/<role>.md

    Returns the prompt content as a string.
    """
    # Check for project-specific override
    if project_path is not None:
        override_path = project_path / ".factory" / "agents" / f"{role}.md"
        if override_path.exists():
            logger.info("Using project-specific prompt for %s: %s", role, override_path)
            return override_path.read_text()

    # Fall back to factory default
    default_path = _PROMPTS_DIR / f"{role}.md"
    if default_path.exists():
        return default_path.read_text()

    override_hint = f" or {project_path / '.factory' / 'agents' / f'{role}.md'}" if project_path else ""
    raise FileNotFoundError(
        f"No prompt found for agent role '{role}'. "
        f"Expected at {default_path}{override_hint}"
    )


async def invoke_agent(
    role: AgentRole,
    task: str,
    project_path: Path,
    *,
    timeout: float = 600.0,
    dangerously_skip_permissions: bool = True,
) -> tuple[str, int]:
    """Invoke a Claude Code agent with the resolved prompt + task.

    Returns (stdout, return_code).
    """
    prompt = resolve_prompt(role, project_path)
    full_prompt = f"{prompt}\n\n---\n\n## Current Task\n\n{task}"

    cmd = ["claude", "-p", full_prompt]
    if dangerously_skip_permissions:
        cmd.append("--dangerously-skip-permissions")

    logger.info("Invoking %s agent for %s", role, project_path.name)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_path,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()  # type: ignore[union-attr]
        await proc.wait()  # type: ignore[union-attr]
        logger.error("%s agent timed out after %ss", role, timeout)
        return f"Agent timed out after {timeout}s", 1
    except FileNotFoundError:
        logger.error("'claude' CLI not found on PATH")
        return "Error: 'claude' CLI not found on PATH", 1

    stdout = stdout_bytes.decode()
    stderr = stderr_bytes.decode()

    if proc.returncode != 0:
        logger.warning("%s agent exited with code %d: %s", role, proc.returncode, stderr[:200])

    return stdout, proc.returncode or 0


async def invoke_agents_parallel(
    tasks: list[tuple[AgentRole, str]],
    project_path: Path,
    *,
    timeout: float = 600.0,
    dangerously_skip_permissions: bool = True,
) -> list[tuple[str, int]]:
    """Invoke multiple agents concurrently. Returns list of (output, return_code)."""
    coros = [
        invoke_agent(
            role,
            task,
            project_path,
            timeout=timeout,
            dangerously_skip_permissions=dangerously_skip_permissions,
        )
        for role, task in tasks
    ]
    return list(await asyncio.gather(*coros))
