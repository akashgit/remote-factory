"""Agent runner — load prompts and invoke Claude Code instances."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from factory.ace.injector import inject_playbook, load_playbook
from factory.runners import get_runner

logger = logging.getLogger(__name__)

AgentRole = str

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


IDENTITY_REANCHOR = """\

---

> **⚠ CEO IDENTITY RE-ANCHOR (Sacred Rule 8)**
> You are the Factory CEO. You orchestrate, delegate, and decide. You do NOT implement.
> If you are about to write code, run tests, do research, or fix bugs — STOP and spawn the appropriate agent.
> Re-read your Permitted/Forbidden Actions lists in the Identity section above.
"""

# Directory containing base agent prompts (shipped with the factory)
_PROMPTS_DIR = Path(__file__).parent / "prompts"
_USER_PROMPTS_DIR = Path.home() / ".factory" / "agents" / "prompts"


def resolve_prompt(
    role: AgentRole,
    project_path: Path | None = None,
    *,
    use_profile: bool = False,
    workflow_mode: str | None = None,
) -> str:
    """Resolve the prompt for an agent role.

    Resolution order:
    1. Project-specific override: <project>/.factory/agents/<role>.md
    2. User-global: ~/.factory/agents/prompts/<role>.md
    3. Factory default: factory/agents/prompts/<role>.md

    When *use_profile* is True, loads ~/.factory/profile.md and appends it
    after the ACE playbook injection.

    When *workflow_mode* is set and *role* is ``"ceo"``, the corresponding
    ``skills/workflow-{workflow_mode}/SKILL.md`` is appended to the prompt
    so it survives context compaction.

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
            if role == "ceo" and workflow_mode and project_path is not None:
                prompt = _maybe_inject_skill(prompt, project_path, workflow_mode)
            return prompt

    # Check user-global prompts (~/.factory/agents/prompts/)
    user_path = _USER_PROMPTS_DIR / f"{role}.md"
    if user_path.exists():
        logger.info("Using user-global prompt for %s: %s", role, user_path)
        prompt = user_path.read_text()
        playbook = load_playbook(role)
        if playbook:
            prompt = inject_playbook(prompt, playbook)
            logger.info("Injected playbook for %s (user-global)", role)
        if use_profile:
            prompt = _maybe_inject_profile(prompt, role)
        if role == "ceo" and workflow_mode and project_path is not None:
            prompt = _maybe_inject_skill(prompt, project_path, workflow_mode)
        return prompt

    # Fall back to factory default
    default_path = _PROMPTS_DIR / f"{role}.md"
    if not default_path.exists():
        override_hint = (
            f" or {project_path / '.factory' / 'agents' / f'{role}.md'}" if project_path else ""
        )
        raise FileNotFoundError(
            f"No prompt found for agent role '{role}'. "
            f"Expected at {default_path}, {_USER_PROMPTS_DIR / f'{role}.md'}{override_hint}"
        )

    prompt = default_path.read_text()

    # Auto-inject evolved playbook if one exists for this role
    playbook = load_playbook(role)
    if playbook:
        prompt = inject_playbook(prompt, playbook)
        logger.info("Injected playbook for %s", role)

    if use_profile:
        prompt = _maybe_inject_profile(prompt, role)

    if role == "ceo" and workflow_mode and project_path is not None:
        prompt = _maybe_inject_skill(prompt, project_path, workflow_mode)

    return prompt


def _maybe_inject_profile(prompt: str, role: str) -> str:
    """Load and inject user profile if it exists."""
    from factory.profile import inject_profile, load_profile

    profile = load_profile()
    if profile:
        prompt = inject_profile(prompt, profile)
        logger.info("Injected user profile for %s", role)
    return prompt


def _maybe_inject_skill(prompt: str, project_path: Path, workflow_mode: str) -> str:
    """Append the workflow SKILL.md to the CEO prompt so it survives compaction."""
    skill_path = project_path / "skills" / f"workflow-{workflow_mode}" / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(
            f"SKILL.md not found for mode {workflow_mode} at {skill_path}. "
            f"Run 'factory workflow export-skills' or check ensure_skills() was called."
        )
    skill_content = skill_path.read_text()
    logger.info("Injected SKILL.md for workflow-%s into CEO prompt", workflow_mode)
    return prompt + f"\n\n# Workflow Playbook ({workflow_mode})\n\n{skill_content}"


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
    session_id: str | None = None,
    resume_session_id: str | None = None,
    use_profile: bool = False,
    tmux_persist: bool = False,
    background: bool = False,
    review_tag: str | None = None,
    workflow_mode: str | None = None,
    settings_file: str | None = None,
    prompt_override: str | None = None,
    transcript_dir: Path | None = None,
) -> tuple[str, int]:
    """Invoke a Claude Code agent with the resolved prompt + task.

    Returns (stdout, return_code).

    Raises:
        ConsecutiveAgentFailureError: If too many consecutive agent spawns fail
            (only when _track_failures=True).
    """
    global _consecutive_failures

    if prompt_override:
        prompt = prompt_override
    else:
        prompt = resolve_prompt(
            role, project_path, use_profile=use_profile, workflow_mode=workflow_mode
        )

    if os.environ.get("FACTORY_NO_GITHUB") == "1":
        prompt += (
            "\n\n## GitHub Disabled\n\n"
            "GitHub integration is disabled for this session (--no-github). "
            "Do NOT run any gh CLI commands (gh issue, gh pr, gh api, etc.). "
            "Do NOT create pull requests or reference GitHub issues. "
            "Work locally only — create commits, run tests, but skip all GitHub operations. "
            "When a step would normally involve GitHub, skip it and note that it was skipped.\n"
        )

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
        session_id=session_id,
        resume_session_id=resume_session_id,
        project_path=project_path,
        extras={
            "tmux_persist": tmux_persist,
            "background": background,
            **({"settings_file": settings_file} if settings_file else {}),
        },
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
                project_path,
                "agent.failed",
                agent=role,
                data={"return_code": return_code, "stderr": stdout[:200] if stdout else ""},
            )
            _complete_span_safe(
                project_path,
                sid,
                status="failed",
                usage=usage,
                metadata=result.metadata,
                output=stdout,
            )
            if _track_failures:
                _consecutive_failures += 1
                _check_failure_threshold(project_path, role)
        else:
            completed_data: dict[str, object] = {"return_code": 0}
            if review_tag:
                completed_data["review_tag"] = review_tag
            if usage is not None:
                completed_data.update(
                    {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "cache_read_tokens": usage.cache_read_tokens,
                        "total_cost_usd": usage.total_cost_usd,
                        "duration_ms": usage.duration_ms,
                        "num_turns": usage.num_turns,
                        "model": usage.model,
                    }
                )
            for meta_key in ("session_id", "stop_reason", "terminal_reason"):
                if result.metadata.get(meta_key) is not None:
                    completed_data[meta_key] = result.metadata[meta_key]
            _emit_safe(
                project_path,
                "agent.completed",
                agent=role,
                data=completed_data,
            )
            _complete_span_safe(
                project_path,
                sid,
                status="completed",
                usage=usage,
                metadata=result.metadata,
                output=stdout,
            )
            if _track_failures:
                _consecutive_failures = 0

        _save_review(project_path, role, stdout, return_code, review_tag=review_tag)

        if transcript_dir is not None:
            _save_transcript(transcript_dir, role, result.raw_stream, task)

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
            trace_id,
            parent_span_id,
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
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "total_cost_usd",
                "duration_ms",
                "num_turns",
                "model",
            ):
                val = getattr(usage, key, None)
                if val is not None:
                    usage_dict[key] = val

        meta = dict(metadata or {})
        claude_session_id = meta.pop("session_id", None)
        if isinstance(claude_session_id, str) and claude_session_id:
            ingest_transcript_to_span(trace_id, span_id, claude_session_id, project_path)

        end_span(
            trace_id,
            span_id,
            status=status,
            usage=usage_dict,
            metadata=meta or None,
            output=output[:4000] if output else None,
        )
        from factory.telemetry import flush as _flush

        _flush()
    except Exception:
        logger.debug("Failed to complete span %s", span_id, exc_info=True)


def _save_review(
    project_path: Path,
    role: str,
    output: str,
    return_code: int,
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


def _save_transcript(
    transcript_dir: Path,
    role: str,
    raw_stream: str,
    task: str,
) -> None:
    """Save the full agent session transcript to a directory.

    Structure (modeled on Meta Harness's log_session):
        transcript_dir/
            stream.jsonl   — raw stream-json events (complete conversation)
            meta.json      — prompt, model, tokens, cost, duration
            tools/
                001_Read.txt   — per-tool-call, human-readable
    """
    import json

    try:
        from datetime import datetime, timezone

        # Try to detect iteration from state.json (LUMEN workflow)
        iteration_suffix = ""
        if ".factory/lumen/" in str(transcript_dir):
            state_file = transcript_dir.parent / "state.json"
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text())
                    iteration = state.get("iteration", 0)
                    iteration_suffix = f"_iteration_{iteration}"
                except Exception:
                    pass  # Silently ignore state.json read failures

        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_dir = transcript_dir / f"{role}{iteration_suffix}_{ts}"
        run_dir.mkdir(parents=True, exist_ok=True)

        # 1. Write raw stream
        (run_dir / "stream.jsonl").write_text(raw_stream)

        # 2. Parse events to build meta + tools
        events = []
        tool_calls: list[dict] = []
        tool_map: dict[str, dict] = {}
        token_usage = {"input_tokens": 0, "output_tokens": 0}
        session_id = ""
        cost_usd = 0.0
        model = ""

        for line in raw_stream.strip().splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            events.append(event)
            etype = event.get("type", "")

            if etype == "assistant":
                msg = event.get("message", {})
                usage = msg.get("usage", {})
                token_usage["input_tokens"] += usage.get("input_tokens", 0)
                token_usage["output_tokens"] += usage.get("output_tokens", 0)
                for cache_key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
                    if cache_key in usage:
                        token_usage[cache_key] = token_usage.get(cache_key, 0) + usage[cache_key]

                for block in msg.get("content", []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        tc = {
                            "name": block["name"],
                            "tool_id": block.get("id", ""),
                            "input": block.get("input", {}),
                            "output": "",
                            "is_error": False,
                        }
                        tool_calls.append(tc)
                        tool_map[tc["tool_id"]] = tc

            elif etype == "user":
                msg = event.get("message", {})
                for block in msg.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tid = block.get("tool_use_id", "")
                        if tid in tool_map:
                            tool_map[tid]["output"] = str(block.get("content", ""))
                            tool_map[tid]["is_error"] = block.get("is_error", False)

            elif etype == "result":
                session_id = event.get("session_id", "")
                cost_usd = event.get("total_cost_usd", 0.0)
                model = event.get("model", "")
                result_usage = event.get("usage", {})
                if result_usage:
                    token_usage["input_tokens"] = result_usage.get(
                        "input_tokens", token_usage["input_tokens"]
                    )
                    token_usage["output_tokens"] = result_usage.get(
                        "output_tokens", token_usage["output_tokens"]
                    )

        # 3. Write meta.json
        meta = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "task": task[:500],
            "model": model,
            "session_id": session_id,
            "cost_usd": cost_usd,
            "token_usage": token_usage,
            "tool_count": len(tool_calls),
            "tool_summary": [
                f"{tc['name']}({'ERR ' if tc['is_error'] else ''}"
                f"{tc['input'].get('file_path') or tc['input'].get('command', '')[:120]})"
                for tc in tool_calls
            ],
        }
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str))

        # 4. Write tools/ directory
        if tool_calls:
            tools_dir = run_dir / "tools"
            tools_dir.mkdir(exist_ok=True)
            for i, tc in enumerate(tool_calls, 1):
                parts = [tc["name"]]
                if tc["is_error"]:
                    parts[0] += " [ERROR]"
                parts.append("")

                for k, v in tc["input"].items():
                    val = str(v)
                    if "\n" in val or len(val) > 80:
                        parts.append(f"{k}:")
                        parts.append(val)
                        parts.append("")
                    else:
                        parts.append(f"{k}: {v}")

                if tc["output"]:
                    parts.append("")
                    parts.append("--- output ---")
                    parts.append(tc["output"])

                (tools_dir / f"{i:03d}_{tc['name']}.txt").write_text("\n".join(parts))

        logger.debug("Saved transcript for %s to %s", role, run_dir)
    except Exception:
        logger.debug("Failed to save transcript for %s", role, exc_info=True)


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
        try:
            factory_dir = project_path / ".factory"
            factory_dir.mkdir(parents=True, exist_ok=True)
            (factory_dir / "trace_id.txt").write_text(trace_id)
        except OSError:
            logger.debug("Failed to write trace_id.txt", exc_info=True)
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
