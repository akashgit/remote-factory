"""Agent runner — load prompts and invoke Claude Code instances."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Literal

from factory.ace.injector import inject_playbook, load_playbook
from factory.runners import get_runner

logger = logging.getLogger(__name__)

AgentRole = Literal[
    "researcher", "strategist", "builder", "qa",
    "archivist", "ceo", "failure_analyst", "refiner", "profiler",
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

IDENTITY_REANCHOR = """\

---

> **⚠ CEO IDENTITY RE-ANCHOR (Sacred Rule 8)**
> You are the Factory CEO. You orchestrate, delegate, and decide. You do NOT implement.
> If you are about to write code, run tests, do research, or fix bugs — STOP and spawn the appropriate agent.
> Re-read your Permitted/Forbidden Actions lists in the Identity section above.
"""

# Directory containing base agent prompts (shipped with the factory)
_PROMPTS_DIR = Path(__file__).parent / "prompts"


def resolve_prompt(
    role: AgentRole,
    project_path: Path | None = None,
    *,
    use_profile: bool = False,
) -> str:
    """Resolve the prompt for an agent role.

    Resolution order:
    1. Project-specific override: <project>/.factory/agents/<role>.md
    2. Factory default: factory/agents/prompts/<role>.md

    When *use_profile* is True, loads ~/.factory/profile.md and appends it
    after the ACE playbook injection.

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
            if use_profile:
                prompt = _maybe_inject_profile(prompt, role)
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

    if use_profile:
        prompt = _maybe_inject_profile(prompt, role)

    return prompt


def _maybe_inject_profile(prompt: str, role: str) -> str:
    """Load and inject user profile if it exists."""
    from factory.profile import inject_profile, load_profile

    profile = load_profile()
    if profile:
        prompt = inject_profile(prompt, profile)
        logger.info("Injected user profile for %s", role)
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
    session_name: str | None = None,
    use_profile: bool = False,
    tmux_persist: bool = False,
    background: bool = False,
    review_tag: str | None = None,
) -> tuple[str, int]:
    """Invoke a Claude Code agent with the resolved prompt + task.

    Returns (stdout, return_code).

    Raises:
        ConsecutiveAgentFailureError: If too many consecutive agent spawns fail
            (only when _track_failures=True).
    """
    global _consecutive_failures

    prompt = resolve_prompt(role, project_path, use_profile=use_profile)

    logger.info("Invoking %s agent for %s", role, project_path.name)

    started_data: dict[str, object] = {"task": task[:200]}
    if review_tag:
        started_data["review_tag"] = review_tag
    _emit_safe(project_path, "agent.started", agent=role, data=started_data)

    sid = _begin_span_safe(project_path, role, model=model, task=task)

    runner = get_runner(runner_name, project_path=project_path)

    agent_session_name = session_name or f"factory: {project_path.resolve().name}/{role}"

    from factory.models import AgentRunRequest

    request = AgentRunRequest(
        prompt=prompt,
        task=task,
        cwd=project_path,
        timeout=timeout,
        model=model,
        skip_permissions=dangerously_skip_permissions,
        role=role,
        session_name=agent_session_name,
        project_path=project_path,
        extras={"tmux_persist": tmux_persist, "background": background},
    )

    old_parent_span = os.environ.get("FACTORY_PARENT_SPAN_ID")
    if sid:
        os.environ["FACTORY_PARENT_SPAN_ID"] = sid
    try:
        try:
            result = await runner.headless(request)
            stdout = result.stdout
            return_code = result.return_code
            usage = result.usage
        except Exception as e:
            logger.error("%s agent failed: %s", role, e)
            _emit_safe(project_path, "agent.failed", agent=role, data={"error": str(e)[:200]})
            _complete_span_safe(project_path, sid, status="failed")
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
            _complete_span_safe(
                project_path, sid, status="failed",
                usage=usage, metadata=result.metadata, output=stdout,
            )
            if _track_failures:
                _consecutive_failures += 1
                _check_failure_threshold(project_path, role)
        else:
            completed_data: dict[str, object] = {"return_code": 0}
            if review_tag:
                completed_data["review_tag"] = review_tag
            if usage is not None:
                completed_data.update({
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": usage.cache_read_tokens,
                    "total_cost_usd": usage.total_cost_usd,
                    "duration_ms": usage.duration_ms,
                    "num_turns": usage.num_turns,
                    "model": usage.model,
                })
            for meta_key in ("session_id", "stop_reason", "terminal_reason"):
                if result.metadata.get(meta_key) is not None:
                    completed_data[meta_key] = result.metadata[meta_key]
            _emit_safe(
                project_path, "agent.completed", agent=role,
                data=completed_data,
            )
            _complete_span_safe(
                project_path, sid, status="completed",
                usage=usage, metadata=result.metadata, output=stdout,
            )
            if _track_failures:
                _consecutive_failures = 0

        _save_review(project_path, role, stdout, return_code, review_tag=review_tag)

        return stdout, return_code
    finally:
        if old_parent_span is not None:
            os.environ["FACTORY_PARENT_SPAN_ID"] = old_parent_span
        elif sid:
            os.environ.pop("FACTORY_PARENT_SPAN_ID", None)


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


def _begin_span_safe(
    project_path: Path,
    role: str,
    *,
    model: str | None = None,
    task: str | None = None,
) -> str | None:
    """Begin a Langfuse span, swallowing errors so agent invocation is never blocked."""
    try:
        from factory.telemetry import begin_span, begin_trace, is_enabled

        if not is_enabled():
            return None
        trace_id = os.environ.get("FACTORY_TRACE_ID")
        parent_span_id = os.environ.get("FACTORY_PARENT_SPAN_ID")
        logger.debug(
            "Langfuse env: FACTORY_TRACE_ID=%s FACTORY_PARENT_SPAN_ID=%s",
            trace_id, parent_span_id,
        )
        if not trace_id:
            result = begin_trace(project_path.name, cycle_id=f"standalone-{role}")
            if result is None:
                return None
            trace_id, root_span_id = result
            os.environ["FACTORY_TRACE_ID"] = trace_id
            os.environ["FACTORY_PARENT_SPAN_ID"] = root_span_id
            parent_span_id = root_span_id
        return begin_span(trace_id, parent_span_id, role, model=model, task=task)
    except Exception:
        logger.debug("Failed to begin span for %s", role, exc_info=True)
        return None


def _complete_span_safe(
    project_path: Path,
    span_id: str | None,
    *,
    status: str = "completed",
    usage: object | None = None,
    metadata: dict[str, object] | None = None,
    output: str | None = None,
) -> None:
    """Complete a Langfuse span, swallowing errors so agent invocation is never blocked."""
    if span_id is None:
        return
    try:
        from factory.telemetry import end_span, ingest_transcript_to_span, is_enabled

        if not is_enabled():
            return
        trace_id = os.environ.get("FACTORY_TRACE_ID")
        if not trace_id:
            return

        usage_dict: dict | None = None
        if usage is not None:
            usage_dict = {}
            for key in ("input_tokens", "output_tokens", "cache_read_tokens",
                        "total_cost_usd", "duration_ms", "num_turns", "model"):
                val = getattr(usage, key, None)
                if val is not None:
                    usage_dict[key] = val

        meta = dict(metadata or {})
        claude_session_id = meta.pop("session_id", None)
        if isinstance(claude_session_id, str) and claude_session_id:
            ingest_transcript_to_span(trace_id, span_id, claude_session_id, project_path)

        end_span(
            trace_id, span_id,
            status=status, usage=usage_dict, metadata=meta or None,
            output=output[:4000] if output else None,
        )
        from factory.telemetry import flush as _flush
        _flush()
    except Exception:
        logger.debug("Failed to complete span %s", span_id, exc_info=True)


def _save_review(
    project_path: Path, role: str, output: str, return_code: int,
    review_tag: str | None = None,
) -> None:
    """Save agent output to .factory/reviews/<role>-latest.md for CEO review.

    When *review_tag* is provided the file is written as
    ``<role>-<tag>-latest.md`` instead, allowing multiple concurrent agents
    with the same role to produce distinct review files.

    Creates the reviews directory if needed. Errors are swallowed so they
    never block agent execution.
    """
    try:
        reviews_dir = project_path / ".factory" / "reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{role}-{review_tag}-latest.md" if review_tag else f"{role}-latest.md"
        review_path = reviews_dir / filename
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        header = f"# {role.title()} Agent Output\n\n- **timestamp:** {ts}\n- **exit_code:** {return_code}\n\n---\n\n"
        content = header + output
        if role != "ceo":
            content += IDENTITY_REANCHOR
        review_path.write_text(content)
        logger.debug("Saved review output for %s to %s", role, review_path)
    except Exception:
        logger.debug("Failed to save review for %s", role, exc_info=True)


def begin_cycle_session(
    project_path: Path,
    cycle_id: str | None = None,
    model: str | None = None,
) -> str | None:
    """Create a root Langfuse trace for a factory cycle.

    Sets FACTORY_TRACE_ID and FACTORY_PARENT_SPAN_ID env vars so child
    agents link to this trace. Returns the span_id, or None if Langfuse
    is not configured.
    """
    try:
        from factory.telemetry import begin_trace, is_enabled

        if not is_enabled():
            return None
        result = begin_trace(
            project_path.name,
            cycle_id or "unknown",
            model=model,
        )
        if result is None:
            return None
        trace_id, span_id = result
        os.environ["FACTORY_TRACE_ID"] = trace_id
        os.environ["FACTORY_PARENT_SPAN_ID"] = span_id
        return span_id
    except Exception:
        logger.debug("Failed to begin cycle trace", exc_info=True)
        return None


def complete_cycle_session(
    project_path: Path,
    span_id: str | None,
) -> None:
    """Mark a root Langfuse trace as finished and flush."""
    if span_id is None:
        return
    try:
        from factory.telemetry import end_trace, flush, is_enabled

        if not is_enabled():
            return
        trace_id = os.environ.get("FACTORY_TRACE_ID", "")
        end_trace(trace_id, span_id=span_id)
        flush()
    except Exception:
        logger.debug("Failed to complete cycle trace", exc_info=True)


async def invoke_agents_parallel(
    tasks: list[tuple[AgentRole, str]],
    project_path: Path,
    *,
    timeout: float = 600.0,
    dangerously_skip_permissions: bool = True,
    model: str | None = None,
    runner_name: str | None = None,
    tmux_persist: bool = False,
    background: bool = False,
    review_tags: list[str | None] | None = None,
) -> list[tuple[str, int]]:
    """Invoke multiple agents concurrently. Returns list of (output, return_code).

    Args:
        review_tags: Optional list of review tags, one per task. When not
            provided, auto-generates numeric tags (0, 1, 2, …) for any role
            that appears more than once in *tasks* so their review files don't
            clobber each other.

    Raises:
        ConsecutiveAgentFailureError: If all agents in the batch fail, indicating
            infrastructure problems (e.g., API key not propagating to subprocesses).
    """
    # Auto-generate tags for duplicate roles when none are provided
    if review_tags is None:
        from collections import Counter

        role_counts = Counter(role for role, _ in tasks)
        duplicated_roles = {role for role, count in role_counts.items() if count > 1}
        if duplicated_roles:
            role_idx: dict[str, int] = {}
            review_tags = []
            for role, _ in tasks:
                if role in duplicated_roles:
                    idx = role_idx.get(role, 0)
                    review_tags.append(str(idx))
                    role_idx[role] = idx + 1
                else:
                    review_tags.append(None)
        else:
            review_tags = [None] * len(tasks)

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
            tmux_persist=tmux_persist,
            background=background,
            review_tag=tag,
        )
        for (role, task), tag in zip(tasks, review_tags)
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
