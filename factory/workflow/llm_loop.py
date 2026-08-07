"""Async tool-use loop for LLMNode — direct LLM API calls with tool execution."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import structlog

from factory.workflow.primitives import LLMNode

log = structlog.get_logger()


def _build_client(node: LLMNode) -> Any:
    if node.provider == "vertex":
        from anthropic import AnthropicVertex
        region = os.environ.get("CLOUD_ML_REGION", "us-east5")
        if region != "global":
            region = "global"
            log.info("llm_loop.vertex_region_override", region=region)
        return AnthropicVertex(
            project_id=os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", ""),
            region=region,
        )
    from anthropic import Anthropic
    return Anthropic()


def _resolve_model(model: str) -> str:
    aliases = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-4-5-20250929",
        "opus": "claude-opus-4-6-20250904",
    }
    return aliases.get(model, model)


def _tools_to_api_format(node: LLMNode) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in node.tools
    ]


async def run_llm_loop(
    node: LLMNode,
    cwd: Path,
    *,
    instance_context: str = "",
) -> str:
    """Execute the LLM tool-use loop for an LLMNode. Returns final text output."""
    from factory.workflow.llm_tools import execute_tool

    client = _build_client(node)
    tool_map = {t.name: t for t in node.tools}
    api_tools = _tools_to_api_format(node) if node.tools else []
    model = _resolve_model(node.model)

    instance_prompt = node.instance_prompt
    if instance_context:
        instance_prompt = f"{instance_prompt}\n\n{instance_context}"

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": instance_prompt},
    ]

    text_parts: list[str] = []

    for turn in range(node.max_turns):
        log.debug("llm_loop.turn", turn=turn, node=node.id, model=model)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": node.max_tokens,
            "messages": messages,
        }
        if node.system_prompt:
            create_kwargs["system"] = node.system_prompt
        if api_tools:
            create_kwargs["tools"] = api_tools
        if node.temperature != 0.0:
            create_kwargs["temperature"] = node.temperature

        response = await asyncio.to_thread(client.messages.create, **create_kwargs)

        has_tool_use = False
        tool_results: list[dict[str, Any]] = []
        turn_text: list[str] = []

        for block in response.content:
            if block.type == "text":
                turn_text.append(block.text)
                for seq in node.stop_sequences:
                    if seq in block.text:
                        text_parts.extend(turn_text)
                        log.info("llm_loop.stop_sequence", node=node.id, turn=turn)
                        return "\n".join(text_parts)

            elif block.type == "tool_use":
                has_tool_use = True
                if block.name not in tool_map:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Unknown tool: {block.name}",
                        "is_error": True,
                    })
                    continue

                result = await execute_tool(
                    block.name, block.input, tool_map[block.name], cwd,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        messages.append({"role": "assistant", "content": response.content})
        text_parts.extend(turn_text)

        if not has_tool_use:
            break

        messages.append({"role": "user", "content": tool_results})

    log.info("llm_loop.finished", node=node.id, turns=turn + 1)
    return "\n".join(text_parts)
