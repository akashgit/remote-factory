"""Span attribute helpers for LLM usage and Langfuse I/O."""

from __future__ import annotations

import json
import re
from typing import Any

from opentelemetry.trace import Span

from factory_tracing.config import get_max_content_length


def truncate(text: str) -> str:
    max_len = get_max_content_length()
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... [truncated at {max_len} chars]"


def set_llm_usage(span: Span, usage: dict[str, Any]) -> None:
    """Map stream-json usage dict to gen_ai.* attributes with correct int types."""
    if "input_tokens" in usage:
        span.set_attribute("gen_ai.usage.input_tokens", int(usage["input_tokens"]))
    if "output_tokens" in usage:
        span.set_attribute("gen_ai.usage.output_tokens", int(usage["output_tokens"]))
    if "cache_creation_input_tokens" in usage:
        span.set_attribute(
            "gen_ai.usage.cache_creation.input_tokens",
            int(usage["cache_creation_input_tokens"]),
        )
    if "cache_read_input_tokens" in usage:
        span.set_attribute(
            "gen_ai.usage.cache_read.input_tokens",
            int(usage["cache_read_input_tokens"]),
        )


def set_langfuse_io(
    span: Span,
    input_obj: Any | None,
    output_obj: Any | None,
) -> None:
    """Set both gen_ai and langfuse attributes for input/output."""
    if input_obj is not None:
        serialized = json.dumps(input_obj) if not isinstance(input_obj, str) else input_obj
        serialized = truncate(serialized)
        span.set_attribute("gen_ai.prompt", serialized)
        span.set_attribute("langfuse.span.input", serialized)
    if output_obj is not None:
        serialized = json.dumps(output_obj) if not isinstance(output_obj, str) else output_obj
        serialized = truncate(serialized)
        span.set_attribute("gen_ai.completion", serialized)
        span.set_attribute("langfuse.span.output", serialized)


def clean_model_name(model: str) -> str:
    """Strip [1m] or similar bracket suffixes from model names."""
    return re.sub(r"\[[^\]]*\]$", "", model).strip()
