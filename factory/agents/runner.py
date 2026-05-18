"""Agent runner — load prompts and invoke Claude Code instances."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal

from factory.ace.injector import inject_playbook, load_playbook
from factory.runners import get_runner

logger = logging.getLogger(__name__)

AgentRole = Literal[
    "researcher", "strategist", "builder", "reviewer", "evaluator",
    "archivist", "distiller", "ceo", "failure_analyst",
]

# Consecutive failure tracking
_consecutive_failures: int = 0
_FAILURE_ABORT_THRESHOLD: int = 2


class ConsecutiveAgentFailureError(Exception):
    """Raised when too many consecutive agent spawns fail.

    This prevents the CEO from falling back to doing work itself when subagent
    infrastructure is broken. Instead, the cycle should abort with a clear error.
    """

    def __init__(self, failure_count: int, last_agent: str) -> None:
        self.failure_count = failure_count
        self.last_agent = last_agent
        super().__init__(
            f"Aborting after {failure_count} consecutive agent spawn failures. "
            f"Last failed agent: {last_agent}. "
            "Check .factory/events.jsonl for details. "
            "This usually means BOBSHELL_API_KEY is not being propagated to subprocesses."
        )


def reset_failure_counter() -> None:
    """Reset the consecutive failure counter. Call at start of a cycle."""
    global _consecutive_failures
    _consecutive_failures = 0

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
    model: str | None = None,
    runner_name: str | None = None,
    _track_failures: bool = True,
) -> tuple[str, int]:
    """Invoke a Claude Code agent with the resolved prompt + task.

    Args:
        role: The agent role to invoke.
        task: The task description.
        project_path: Path to the project.
        timeout: Maximum execution time in seconds.
        dangerously_skip_permissions: If True, skip permission prompts.
        model: Optional model override.
        runner_name: CLI backend to use ("claude" or "bob"). Defaults to FACTORY_RUNNER env var.
        _track_failures: If True (default), track consecutive failures globally.
            Set to False when called from invoke_agents_parallel to avoid race conditions.

    Returns (stdout, return_code).

    Raises:
        ConsecutiveAgentFailureError: If too many consecutive agent spawns fail
            (only when _track_failures=True).
    """
    global _consecutive_failures

    prompt = resolve_prompt(role, project_path)

    logger.info("Invoking %s agent for %s", role, project_path.name)

    _emit_safe(project_path, "agent.started", agent=role, data={"task": task[:200]})

    runner = get_runner(runner_name, project_path=project_path)

    try:
        stdout, return_code = await runner.headless(
            prompt=prompt,
            task=task,
            cwd=project_path,
            timeout=timeout,
            model=model,
            dangerously_skip_permissions=dangerously_skip_permissions,
            role=role,
        )
    except Exception as e:
        logger.error("%s agent failed: %s", role, e)
        _emit_safe(project_path, "agent.failed", agent=role, data={"error": str(e)[:200]})
        if _track_failures:
            _consecutive_failures += 1
            _check_failure_threshold(project_path, role)
        return f"Error: {e}", 1

    if return_code != 0:
        logger.warning("%s agent exited with code %d", role, return_code)
        _emit_safe(
            project_path, "agent.failed", agent=role,
            data={"return_code": return_code, "stderr": stdout[:200] if stdout else ""},
        )
        if _track_failures:
            _consecutive_failures += 1
            _check_failure_threshold(project_path, role)
    else:
        _emit_safe(
            project_path, "agent.completed", agent=role,
            data={"return_code": 0},
        )
        if _track_failures:
            # Reset counter on success
            _consecutive_failures = 0

    _save_review(project_path, role, stdout, return_code)

    return stdout, return_code


def _check_failure_threshold(project_path: Path, last_agent: str) -> None:
    """Check if consecutive failures have exceeded the threshold and abort if so."""
    global _consecutive_failures

    if _consecutive_failures >= _FAILURE_ABORT_THRESHOLD:
        # Emit cycle.aborted event before raising
        _emit_safe(
            project_path,
            "cycle.aborted",
            data={
                "reason": "consecutive_agent_failures",
                "failure_count": _consecutive_failures,
                "last_agent": last_agent,
            },
        )
        raise ConsecutiveAgentFailureError(_consecutive_failures, last_agent)


def _emit_safe(project_path: Path, event_type: str, **kwargs: object) -> None:
    """Emit an event, swallowing errors so agent invocation is never blocked."""
    try:
        from factory.events import emit_event

        emit_event(project_path, event_type, **kwargs)  # type: ignore[arg-type]
    except Exception:
        logger.debug("Failed to emit event %s", event_type, exc_info=True)


def _save_review(project_path: Path, role: str, output: str, return_code: int) -> None:
    """Save agent output to .factory/reviews/<role>-latest.md for CEO review.

    Creates the reviews directory if needed. Errors are swallowed so they
    never block agent execution.
    """
    try:
        reviews_dir = project_path / ".factory" / "reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        review_path = reviews_dir / f"{role}-latest.md"
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        header = f"# {role.title()} Agent Output\n\n- **timestamp:** {ts}\n- **exit_code:** {return_code}\n\n---\n\n"
        review_path.write_text(header + output)
        logger.debug("Saved review output for %s to %s", role, review_path)
    except Exception:
        logger.debug("Failed to save review for %s", role, exc_info=True)


async def invoke_agents_parallel(
    tasks: list[tuple[AgentRole, str]],
    project_path: Path,
    *,
    timeout: float = 600.0,
    dangerously_skip_permissions: bool = True,
    model: str | None = None,
    runner_name: str | None = None,
) -> list[tuple[str, int]]:
    """Invoke multiple agents concurrently. Returns list of (output, return_code).

    Raises:
        ConsecutiveAgentFailureError: If all agents in the batch fail, indicating
            infrastructure problems (e.g., API key not propagating to subprocesses).
    """
    coros = [
        invoke_agent(
            role,
            task,
            project_path,
            timeout=timeout,
            dangerously_skip_permissions=dangerously_skip_permissions,
            model=model,
            runner_name=runner_name,
            _track_failures=False,  # Avoid race condition; track locally below
        )
        for role, task in tasks
    ]
    results = list(await asyncio.gather(*coros))

    # Track failures locally to avoid race condition with global counter
    failure_count = sum(1 for _, code in results if code != 0)
    if failure_count >= _FAILURE_ABORT_THRESHOLD and failure_count == len(results):
        # All agents failed — likely infrastructure issue
        _emit_safe(
            project_path,
            "cycle.aborted",
            data={
                "reason": "consecutive_agent_failures",
                "failure_count": failure_count,
                "last_agent": "parallel_batch",
            },
        )
        raise ConsecutiveAgentFailureError(failure_count, "parallel_batch")

    return results
