"""Traced agent execution with stream-json parsing and OTel span creation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from opentelemetry import trace

from factory_tracing.config import TracingConfig, get_max_content_length
from factory_tracing.propagation import build_traced_env
from factory_tracing.provider import get_provider, get_tracer
from factory_tracing.spans import clean_model_name, set_langfuse_io, set_llm_usage, truncate

logger = logging.getLogger(__name__)


class ConversationTracker:
    """Accumulates messages as structured JSON for span input/output."""

    def __init__(self, system_prompt: str | None, user_task: str) -> None:
        self.messages: list[dict] = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})
        self.messages.append({"role": "user", "content": user_task})

    def add_assistant(self, content_blocks: list[dict]) -> None:
        self.messages.append({"role": "assistant", "content": content_blocks})

    def add_tool_result(self, tool_use_id: str, result: str) -> None:
        self.messages.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": result}],
        })

    def get_messages_json(self) -> str:
        return json.dumps(self.messages)


@dataclass
class AgentResult:
    """Result from a traced agent execution."""

    stdout: str = ""
    return_code: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    num_turns: int = 0
    duration_ms: float = 0.0


def _build_claude_command(
    task: str,
    system_prompt_file: str | None,
    model: str,
    cwd: str | None,
) -> list[str]:
    cmd = ["claude"]
    if system_prompt_file:
        cmd.extend(["--append-system-prompt-file", system_prompt_file])
    cmd.extend(["-p", task, "--output-format", "stream-json"])
    cmd.append("--dangerously-skip-permissions")
    if model and model != "anthropic":
        cmd.extend(["--model", model])
    return cmd


def _process_assistant_message(
    message: dict,
    conversation: ConversationTracker,
    tracer: trace.Tracer,
    parent_span: trace.Span,
    model: str,
    result: AgentResult,
) -> None:
    """Process a single assistant message event from the stream."""
    content_blocks = message.get("content", [])
    usage = message.get("usage", {})

    ctx = trace.set_span_in_context(parent_span)
    with tracer.start_as_current_span("llm_call", context=ctx) as llm_span:
        llm_span.set_attribute("gen_ai.request.model", clean_model_name(model))
        llm_span.set_attribute("gen_ai.system", "anthropic")

        input_json = truncate(conversation.get_messages_json())
        output_json = truncate(json.dumps(content_blocks))
        set_langfuse_io(llm_span, input_json, output_json)

        if usage:
            set_llm_usage(llm_span, usage)
            result.input_tokens += int(usage.get("input_tokens", 0))
            result.output_tokens += int(usage.get("output_tokens", 0))
            result.cache_creation_tokens += int(usage.get("cache_creation_input_tokens", 0))
            result.cache_read_tokens += int(usage.get("cache_read_input_tokens", 0))

    for block in content_blocks:
        if block.get("type") == "tool_use":
            tool_name = block.get("name", "unknown")
            tool_id = block.get("id", "")
            tool_input = block.get("input", {})
            ctx = trace.set_span_in_context(parent_span)
            with tracer.start_as_current_span(f"tool:{tool_name}", context=ctx) as tool_span:
                tool_span.set_attribute("tool.name", tool_name)
                tool_span.set_attribute("tool.id", tool_id)
                set_langfuse_io(tool_span, tool_input, None)

    conversation.add_assistant(content_blocks)


def _process_tool_result(
    message: dict,
    conversation: ConversationTracker,
    tracer: trace.Tracer,
    parent_span: trace.Span,
) -> None:
    """Process a tool result event from the stream."""
    content_blocks = message.get("content", [])
    for block in content_blocks:
        if block.get("type") == "tool_result":
            tool_use_id = block.get("tool_use_id", "")
            result_content = block.get("content", "")
            if isinstance(result_content, list):
                result_content = json.dumps(result_content)
            conversation.add_tool_result(tool_use_id, str(result_content))


def _process_result_event(
    data: dict,
    result: AgentResult,
    agent_span: trace.Span,
) -> None:
    """Process the final result event with cost and usage summary."""
    cost = data.get("cost_usd", 0.0)
    if cost:
        result.cost_usd = float(cost)
        agent_span.set_attribute("gen_ai.usage.cost", float(cost))

    result.duration_ms = float(data.get("duration_ms", 0.0) or 0.0)
    result.num_turns = int(data.get("num_turns", 0) or 0)

    model = data.get("model", "")
    if model:
        result.model = clean_model_name(model)

    usage = data.get("usage", {})
    if usage:
        result.input_tokens = int(usage.get("input_tokens", result.input_tokens))
        result.output_tokens = int(usage.get("output_tokens", result.output_tokens))

    final_result = data.get("result", "")

    output_obj = {
        "response": str(final_result)[:2000] if final_result else "",
        "model": result.model,
        "tokens": {
            "input": result.input_tokens,
            "output": result.output_tokens,
        },
        "cost_usd": result.cost_usd,
    }
    set_langfuse_io(agent_span, None, output_obj)

    if isinstance(final_result, str):
        result.stdout = final_result


def run_traced_agent(
    prompt: str,
    role: str,
    run_id: str,
    project_name: str,
    system_prompt: str | None = None,
    cwd: str | None = None,
    model: str = "anthropic",
    env: dict | None = None,
) -> AgentResult:
    """Run a Claude Code agent with full OTel tracing.

    Creates an agent span as the root, then parses stream-json output to create
    llm_call and tool spans with full conversation context.
    """
    config = TracingConfig.from_env()
    if config.enabled:
        get_provider(config)

    tracer = get_tracer("factory")
    result = AgentResult()

    prompt_file = None
    try:
        if system_prompt:
            prompt_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", prefix="factory-tracing-prompt-", delete=False,
            )
            prompt_file.write(system_prompt)
            prompt_file.close()

        with tracer.start_as_current_span(f"agent:{role}") as agent_span:
            agent_span.set_attribute("agent.role", role)
            agent_span.set_attribute("agent.run_id", run_id)
            agent_span.set_attribute("project.name", project_name)

            if system_prompt:
                agent_span.set_attribute(
                    "gen_ai.system_instructions",
                    truncate(system_prompt),
                )

            input_obj = {
                "system_prompt": truncate(system_prompt) if system_prompt else None,
                "task": truncate(prompt),
                "role": role,
            }
            set_langfuse_io(agent_span, input_obj, None)

            conversation = ConversationTracker(system_prompt, prompt)

            cmd = _build_claude_command(
                prompt,
                prompt_file.name if prompt_file else None,
                model,
                cwd,
            )

            traced_env = build_traced_env(env)

            try:
                proc = _run_subprocess(cmd, cwd=cwd, env=traced_env)
                _parse_stream_output(proc, conversation, tracer, agent_span, model, result)
                result.return_code = proc.wait()
            except FileNotFoundError:
                logger.error("claude CLI not found on PATH")
                result.return_code = 1
                result.stdout = "Error: 'claude' CLI not found on PATH"
                agent_span.set_status(trace.StatusCode.ERROR, "claude CLI not found")
            except Exception as e:
                logger.exception("Agent execution failed")
                result.return_code = 1
                result.stdout = f"Error: {e}"
                agent_span.set_status(trace.StatusCode.ERROR, str(e))

            if result.return_code != 0:
                agent_span.set_status(
                    trace.StatusCode.ERROR,
                    f"Agent exited with code {result.return_code}",
                )

    finally:
        if prompt_file:
            Path(prompt_file.name).unlink(missing_ok=True)

    return result


def _run_subprocess(
    cmd: list[str],
    cwd: str | None,
    env: dict[str, str],
) -> "subprocess.Popen[bytes]":
    import subprocess

    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
    )


def _parse_stream_output(
    proc: "subprocess.Popen[bytes]",
    conversation: ConversationTracker,
    tracer: trace.Tracer,
    agent_span: trace.Span,
    model: str,
    result: AgentResult,
) -> None:
    """Parse stream-json output line by line and create spans."""
    assert proc.stdout is not None
    for line in proc.stdout:
        line_str = line.decode("utf-8", errors="replace").strip()
        if not line_str:
            continue
        try:
            event = json.loads(line_str)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "assistant":
            message = event.get("message", event)
            detected_model = message.get("model", "") or event.get("model", "")
            if detected_model:
                model = detected_model
                result.model = clean_model_name(model)
            _process_assistant_message(
                message, conversation, tracer, agent_span, model, result,
            )

        elif event_type == "tool_result":
            _process_tool_result(event, conversation, tracer, agent_span)

        elif event_type == "result":
            _process_result_event(event, result, agent_span)
