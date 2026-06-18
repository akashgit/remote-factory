"""Execute Claude Code agents with stream-json parsing and OTel span creation.

Parses Claude Code's --output-format stream-json NDJSON output to create
spans with full input/output content, bypassing Claude Code's native OTel
which produces empty content fields.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field

from opentelemetry.trace import StatusCode

from .provider import get_tracer

MAX_CONTENT_LENGTH = 4000


@dataclass
class AgentResult:
    response_text: str
    exit_code: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: float
    model: str
    num_turns: int
    session_id: str


def _truncate(text: str, max_len: int = MAX_CONTENT_LENGTH) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...[truncated]"


def run_traced_agent(
    prompt: str,
    role: str,
    run_id: str,
    project_name: str,
    cwd: str | None = None,
    model: str = "anthropic",
    env: dict | None = None,
) -> AgentResult:
    tracer = get_tracer()

    with tracer.start_as_current_span(f"invoke_agent {role}") as agent_span:
        agent_span.set_attribute("gen_ai.operation.name", "invoke_agent")
        agent_span.set_attribute("gen_ai.agent.name", role)
        agent_span.set_attribute("gen_ai.system", "anthropic")
        agent_span.set_attribute("gen_ai.request.model", model)
        agent_span.set_attribute("factory.run.id", run_id)
        agent_span.set_attribute("factory.project.name", project_name)
        agent_span.set_attribute("factory.task.summary", prompt)
        agent_span.set_attribute("gen_ai.prompt", _truncate(prompt))
        agent_span.set_attribute("langfuse.span.input", _truncate(prompt))
        agent_span.set_attribute("langfuse.observation.type", "span")
        agent_span.set_attribute("langfuse.session.id", run_id)
        agent_span.set_attribute("langfuse.trace.tags", (role,))

        result = _execute_and_parse(
            prompt=prompt,
            role=role,
            agent_span=agent_span,
            tracer=tracer,
            cwd=cwd,
            model=model,
            env=env,
        )

        agent_span.set_attribute("gen_ai.completion", _truncate(result.response_text))
        agent_span.set_attribute("langfuse.span.output", _truncate(result.response_text))
        agent_span.set_attribute("subprocess.returncode", result.exit_code)
        agent_span.set_attribute("subprocess.duration_ms", result.duration_ms)
        agent_span.set_attribute("gen_ai.usage.input_tokens", result.input_tokens)
        agent_span.set_attribute("gen_ai.usage.output_tokens", result.output_tokens)
        agent_span.set_attribute("gen_ai.usage.cost", result.cost_usd)

        if result.model and result.model != model:
            agent_span.set_attribute("gen_ai.request.model", result.model)

        if result.exit_code != 0:
            agent_span.set_status(StatusCode.ERROR, f"agent exited with code {result.exit_code}")
        else:
            agent_span.set_status(StatusCode.OK)

        return result


def _execute_and_parse(
    prompt: str,
    role: str,
    agent_span,
    tracer,
    cwd: str | None,
    model: str,
    env: dict | None,
) -> AgentResult:
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]

    open_tool_spans: dict[str, object] = {}
    detected_model = model
    session_id = ""
    total_input_tokens = 0
    total_output_tokens = 0
    response_text = ""
    cost_usd = 0.0
    duration_ms = 0.0
    num_turns = 0
    turn_count = 0

    start_time = time.monotonic()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env,
        )
    except FileNotFoundError:
        return AgentResult(
            response_text="",
            exit_code=127,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            duration_ms=(time.monotonic() - start_time) * 1000.0,
            model=model,
            num_turns=0,
            session_id="",
        )

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")

            if event_type == "system" and event.get("subtype") == "init":
                detected_model = event.get("model", model)
                session_id = event.get("session_id", "")
                agent_span.set_attribute("gen_ai.request.model", detected_model)
                if session_id:
                    agent_span.set_attribute("gen_ai.session.id", session_id)

            elif event_type == "assistant":
                _handle_assistant_event(
                    event, agent_span, tracer, open_tool_spans,
                    detected_model, prompt, turn_count,
                )
                turn_count += 1
                msg = event.get("message", {})
                usage = msg.get("usage", {})
                if usage:
                    total_input_tokens += usage.get("input_tokens", 0)
                    total_output_tokens += usage.get("output_tokens", 0)

            elif event_type == "user":
                _handle_user_event(event, open_tool_spans)

            elif event_type == "result":
                response_text = event.get("result", "")
                if isinstance(response_text, list):
                    parts = []
                    for block in response_text:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(block.get("text", ""))
                    response_text = "\n".join(parts)
                elif not isinstance(response_text, str):
                    response_text = str(response_text) if response_text else ""

                result_usage = event.get("usage", {})
                if result_usage:
                    total_input_tokens = result_usage.get("input_tokens", total_input_tokens)
                    total_output_tokens = result_usage.get("output_tokens", total_output_tokens)
                cost_usd = event.get("total_cost_usd", 0.0) or 0.0
                duration_ms = event.get("duration_ms", 0.0) or 0.0
                num_turns = event.get("num_turns", 0) or 0

        proc.wait()
        exit_code = proc.returncode

    except Exception:
        proc.kill()
        proc.wait()
        exit_code = proc.returncode if proc.returncode is not None else 1
        if not duration_ms:
            duration_ms = (time.monotonic() - start_time) * 1000.0

    for tool_id, tool_span in open_tool_spans.items():
        tool_span.set_status(StatusCode.ERROR, "tool span never received result")
        tool_span.end()
    open_tool_spans.clear()

    if not duration_ms:
        duration_ms = (time.monotonic() - start_time) * 1000.0

    return AgentResult(
        response_text=response_text,
        exit_code=exit_code,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        model=detected_model,
        num_turns=num_turns,
        session_id=session_id,
    )


def _handle_assistant_event(event, agent_span, tracer, open_tool_spans, model, prompt, turn_count):
    msg = event.get("message", {})
    content_blocks = msg.get("content", [])
    usage = msg.get("usage", {})

    text_parts = []
    tool_use_blocks = []

    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_use_blocks.append(block)

    for tool_block in tool_use_blocks:
        tool_name = tool_block.get("name", "unknown")
        tool_use_id = tool_block.get("id", "")
        tool_input = tool_block.get("input", {})

        tool_span = tracer.start_span(
            f"tool:{tool_name}",
            context=_span_context(agent_span),
        )
        tool_span.set_attribute("tool.name", tool_name)
        tool_span.set_attribute("gen_ai.prompt", _truncate(json.dumps(tool_input)))
        tool_span.set_attribute("langfuse.span.input", _truncate(json.dumps(tool_input)))
        tool_span.set_attribute("langfuse.observation.type", "span")

        if tool_use_id:
            open_tool_spans[tool_use_id] = tool_span

    if text_parts and not tool_use_blocks:
        text_content = "\n".join(text_parts)
        llm_span = tracer.start_span(
            "llm_call",
            context=_span_context(agent_span),
        )
        llm_span.set_attribute("gen_ai.request.model", model)
        llm_span.set_attribute("gen_ai.prompt", _truncate(prompt) if turn_count == 0 else "[conversation context]")
        llm_span.set_attribute("gen_ai.completion", _truncate(text_content))
        llm_span.set_attribute("langfuse.span.input", _truncate(prompt) if turn_count == 0 else "[conversation context]")
        llm_span.set_attribute("langfuse.span.output", _truncate(text_content))
        llm_span.set_attribute("langfuse.observation.type", "span")
        if usage:
            if "input_tokens" in usage:
                llm_span.set_attribute("gen_ai.usage.input_tokens", usage["input_tokens"])
            if "output_tokens" in usage:
                llm_span.set_attribute("gen_ai.usage.output_tokens", usage["output_tokens"])
        llm_span.set_status(StatusCode.OK)
        llm_span.end()


def _handle_user_event(event, open_tool_spans):
    msg = event.get("message", {})
    content_blocks = msg.get("content", [])

    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_result":
            continue

        tool_use_id = block.get("tool_use_id", "")
        result_content = block.get("content", "")
        if isinstance(result_content, list):
            parts = []
            for part in result_content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
            result_content = "\n".join(parts)
        elif not isinstance(result_content, str):
            result_content = str(result_content) if result_content else ""

        if tool_use_id and tool_use_id in open_tool_spans:
            tool_span = open_tool_spans.pop(tool_use_id)
            tool_span.set_attribute("gen_ai.completion", _truncate(result_content))
            tool_span.set_attribute("langfuse.span.output", _truncate(result_content))
            tool_span.set_status(StatusCode.OK)
            tool_span.end()


def _span_context(parent_span):
    from opentelemetry import context as otel_context
    from opentelemetry.trace import set_span_in_context
    return set_span_in_context(parent_span, otel_context.get_current())
