"""Agent runner — load prompts and invoke Claude Code instances."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Literal

from factory.ace.injector import inject_playbook, load_playbook

logger = logging.getLogger(__name__)

AgentRole = Literal["researcher", "strategist", "builder", "reviewer", "evaluator", "archivist", "ceo"]

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
            prompt = override_path.read_text()
            # Auto-inject evolved playbook even with project overrides
            playbook = load_playbook(role)
            if playbook:
                prompt = inject_playbook(prompt, playbook)
                logger.info("Injected playbook for %s (project override)", role)
            return prompt

    # Fall back to factory default
    default_path = _PROMPTS_DIR / f"{role}.md"
    if not default_path.exists():
        override_hint = f" or {project_path / '.factory' / 'agents' / f'{role}.md'}" if project_path else ""
        raise FileNotFoundError(
            f"No prompt found for agent role '{role}'. "
            f"Expected at {default_path}{override_hint}"
        )

    prompt = default_path.read_text()

    # Auto-inject evolved playbook if one exists for this role
    playbook = load_playbook(role)
    if playbook:
        prompt = inject_playbook(prompt, playbook)
        logger.info("Injected playbook for %s", role)

    return prompt


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

    # Emit agent started event
    _emit_safe(project_path, "agent.started", agent=role, data={"task": task[:200]})

    # Clean environment: remove VIRTUAL_ENV so the target project's own
    # venv is used (prevents mypy/pytest from checking wrong packages).
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_path,
            env=env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()  # type: ignore[union-attr]
        await proc.wait()  # type: ignore[union-attr]
        logger.error("%s agent timed out after %ss", role, timeout)
        _emit_safe(project_path, "agent.timeout", agent=role, data={"timeout": timeout})
        return f"Agent timed out after {timeout}s", 1
    except FileNotFoundError:
        logger.error("'claude' CLI not found on PATH")
        _emit_safe(project_path, "agent.failed", agent=role, data={"error": "claude CLI not found"})
        return "Error: 'claude' CLI not found on PATH", 1

    stdout = stdout_bytes.decode()
    stderr = stderr_bytes.decode()

    if proc.returncode != 0:
        logger.warning("%s agent exited with code %d: %s", role, proc.returncode, stderr[:200])
        _emit_safe(
            project_path, "agent.failed", agent=role,
            data={"return_code": proc.returncode, "stderr": stderr[:200]},
        )
    else:
        _emit_safe(
            project_path, "agent.completed", agent=role,
            data={"return_code": 0},
        )

    return stdout, proc.returncode or 0


def _emit_safe(project_path: Path, event_type: str, **kwargs: object) -> None:
    """Emit an event, swallowing errors so agent invocation is never blocked."""
    try:
        from factory.events import emit_event

        emit_event(project_path, event_type, **kwargs)  # type: ignore[arg-type]
    except Exception:
        logger.debug("Failed to emit event %s", event_type, exc_info=True)


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
