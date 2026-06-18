"""End-to-end verification: run a real multi-agent factory cycle and validate content in Langfuse.

This module is dev/verification tooling — it MAY import langfuse and dotenv.
It must NOT be imported by production tracing code (config, provider, spans, propagation, integration).
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

from dotenv import load_dotenv

from .config import TracingConfig
from .executor import run_traced_agent
from .propagation import build_traced_env
from .provider import get_tracer_provider, shutdown_tracing
from .spans import record_agent_result, trace_agent_invocation, trace_factory_cycle


AGENT_PROMPTS = {
    "researcher": "List 3 key benefits of distributed tracing in multi-agent AI systems. Be concise, use bullet points.",
    "strategist": "Based on these research findings: {prev_output}. Propose a 2-step implementation plan. Be concise.",
}


@dataclass
class ContentCheck:
    name: str
    passed: bool
    expected: str
    actual: str


@dataclass
class AgentTrace:
    role: str
    prompt_snippet: str
    response_snippet: str
    llm_calls: int
    tokens_in: int
    tokens_out: int


@dataclass
class VerificationResult:
    trace_id: str
    langfuse_url: str
    span_count: int
    agents_traced: list[AgentTrace] = field(default_factory=list)
    total_llm_calls: int = 0
    total_tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0, "cache_read": 0})
    total_duration_seconds: float = 0.0
    content_checks: list[ContentCheck] = field(default_factory=list)
    success: bool = False


def _short_uuid() -> str:
    return uuid.uuid4().hex[:8]


def _parse_claude_output(stdout: str) -> dict:
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return {}


def _extract_response_text(parsed: dict) -> str:
    result = parsed.get("result", "")
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = []
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(result) if result else ""


def _extract_usage(parsed: dict) -> dict:
    usage = parsed.get("usage", {})
    if not usage:
        usage = parsed.get("result", {}).get("usage", {}) if isinstance(parsed.get("result"), dict) else {}
    return {
        "input_tokens": usage.get("input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
        "cost_usd": usage.get("cost_usd", 0.0) or 0.0,
    }


def _snippet(text: str, max_len: int = 120) -> str:
    s = text.replace("\n", " ").strip()
    return s[:max_len] + "..." if len(s) > max_len else s


def _query_langfuse_trace(config: TracingConfig, trace_id: str, max_retries: int = 5, delay: float = 3.0) -> dict:
    url = f"{config.langfuse_host.rstrip('/')}/api/public/traces/{trace_id}"
    credentials = base64.b64encode(
        f"{config.langfuse_public_key}:{config.langfuse_secret_key}".encode()
    ).decode()

    last_error = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, method="GET", headers={
                "Authorization": f"Basic {credentials}",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(delay)

    raise RuntimeError(f"Failed to fetch trace {trace_id} after {max_retries} attempts: {last_error}")


def _get_obs_attribute(obs: dict, key: str) -> object:
    if key in ("model", "gen_ai.request.model"):
        top_model = obs.get("model")
        if top_model:
            return top_model
    metadata = obs.get("metadata")
    if isinstance(metadata, dict):
        attrs = metadata.get("attributes", {})
        if isinstance(attrs, dict):
            if key in attrs:
                val = attrs[key]
                if isinstance(val, str) and val.isdigit():
                    return int(val)
                return val
            short_key = key.split(".")[-1]
            if short_key in attrs:
                val = attrs[short_key]
                if isinstance(val, str) and val.isdigit():
                    return int(val)
                return val
    return None


def _find_children(observations: list[dict], parent_id: str) -> list[dict]:
    return [o for o in observations if o.get("parentObservationId") == parent_id]


def _invoke_agent(
    role: str,
    prompt: str,
    run_id: str,
    project_name: str,
    traced_env: dict,
) -> tuple[dict, int, float]:
    """Invoke a Claude agent subprocess using run_traced_agent for full content capture."""
    agent_result = run_traced_agent(
        prompt=prompt,
        role=role,
        run_id=run_id,
        project_name=project_name,
        env=traced_env,
    )
    parsed = {
        "result": agent_result.response_text,
        "model": agent_result.model,
        "usage": {
            "input_tokens": agent_result.input_tokens,
            "output_tokens": agent_result.output_tokens,
            "cost_usd": agent_result.cost_usd,
        },
    }
    return parsed, agent_result.exit_code, agent_result.duration_ms


def _validate_content(trace_data: dict, expected_roles: list[str]) -> list[ContentCheck]:
    checks: list[ContentCheck] = []
    observations = trace_data.get("observations") or []
    obs_names = [o.get("name", "") for o in observations]

    has_cycle = any("factory.cycle" in (n or "") for n in obs_names)
    checks.append(ContentCheck(
        name="root_span_exists",
        passed=has_cycle,
        expected="factory.cycle span present",
        actual="found" if has_cycle else "missing",
    ))

    agent_obs = [o for o in observations if "invoke_agent" in (o.get("name") or "")]
    for role in expected_roles:
        matching = [o for o in agent_obs if role in (o.get("name") or "")]
        found = len(matching) > 0
        checks.append(ContentCheck(
            name=f"agent_span_{role}",
            passed=found,
            expected=f"invoke_agent {role} span present",
            actual=f"found ({len(matching)})" if found else "missing",
        ))

        if matching:
            agent_name = _get_obs_attribute(matching[0], "gen_ai.agent.name")
            name_matches = agent_name == role
            checks.append(ContentCheck(
                name=f"agent_name_{role}",
                passed=name_matches,
                expected=f"gen_ai.agent.name == '{role}'",
                actual=str(agent_name) if agent_name else "missing",
            ))

    for role in expected_roles:
        matching_agents = [o for o in agent_obs if role in (o.get("name") or "")]
        if not matching_agents:
            continue
        agent_id = matching_agents[0].get("id")
        if not agent_id:
            continue
        children = _find_children(observations, agent_id)
        cc_children = [c for c in children if "claude_code" in (c.get("name") or "")]
        has_children = len(cc_children) > 0
        checks.append(ContentCheck(
            name=f"nesting_{role}",
            passed=has_children,
            expected=f"claude_code spans nested under invoke_agent {role}",
            actual=f"{len(cc_children)} claude_code spans found as children" if has_children else "no claude_code children",
        ))

    cc_interaction_spans = [o for o in observations if "claude_code.interaction" in (o.get("name") or "")]
    for span in cc_interaction_spans:
        user_prompt = _get_obs_attribute(span, "user_prompt") or ""
        has_prompt = bool(user_prompt) and len(str(user_prompt)) > 5
        checks.append(ContentCheck(
            name="interaction_has_prompt",
            passed=has_prompt,
            expected="user_prompt attribute non-empty",
            actual=_snippet(str(user_prompt), 80) if user_prompt else "empty/missing",
        ))
        break  # check at least one

    llm_spans = [o for o in observations if "claude_code.llm_request" in (o.get("name") or "")]
    checks.append(ContentCheck(
        name="llm_call_count",
        passed=len(llm_spans) > 2,
        expected="more than 2 LLM requests",
        actual=str(len(llm_spans)),
    ))

    for i, llm in enumerate(llm_spans[:3]):
        model = _get_obs_attribute(llm, "gen_ai.request.model") or ""
        has_model = bool(model)
        checks.append(ContentCheck(
            name=f"llm_has_model_{i}",
            passed=has_model,
            expected="model name present",
            actual=str(model) if model else "missing",
        ))

        input_tokens = _get_obs_attribute(llm, "gen_ai.usage.input_tokens")
        has_input = isinstance(input_tokens, (int, float)) and input_tokens > 0
        checks.append(ContentCheck(
            name=f"llm_input_tokens_{i}",
            passed=has_input,
            expected="input_tokens > 0",
            actual=str(input_tokens) if input_tokens else "0 or missing",
        ))

        output_tokens = _get_obs_attribute(llm, "gen_ai.usage.output_tokens")
        has_output = isinstance(output_tokens, (int, float)) and output_tokens > 0
        checks.append(ContentCheck(
            name=f"llm_output_tokens_{i}",
            passed=has_output,
            expected="output_tokens > 0",
            actual=str(output_tokens) if output_tokens else "0 or missing",
        ))

    for role in expected_roles:
        matching_agents = [o for o in agent_obs if role in (o.get("name") or "")]
        if not matching_agents:
            continue
        agent_id = matching_agents[0].get("id")
        if not agent_id:
            continue
        other_agents = [o for o in agent_obs if role not in (o.get("name") or "")]
        for other in other_agents:
            other_id = other.get("id")
            if not other_id:
                continue
            misplaced = [
                c for c in observations
                if c.get("parentObservationId") == other_id and "claude_code" in (c.get("name") or "")
            ]
            # Just a diagnostic — not a hard fail since we can't control CC's span structure fully

    return checks


def run_verification(num_agents: int = 2) -> VerificationResult:
    load_dotenv()
    config = TracingConfig.from_env()

    if not config.enabled:
        return VerificationResult(
            trace_id="",
            langfuse_url="",
            span_count=0,
            content_checks=[ContentCheck(
                name="tracing_enabled", passed=False,
                expected="FACTORY_TRACING_ENABLED=true", actual="not set",
            )],
            success=False,
        )

    provider = get_tracer_provider(config)
    if provider is None:
        return VerificationResult(
            trace_id="",
            langfuse_url="",
            span_count=0,
            content_checks=[ContentCheck(
                name="provider_init", passed=False,
                expected="TracerProvider initialized", actual="failed",
            )],
            success=False,
        )

    roles = list(AGENT_PROMPTS.keys())[:num_agents]
    run_id = f"verify-{_short_uuid()}"
    trace_id_hex = ""
    agents_traced: list[AgentTrace] = []
    total_in = 0
    total_out = 0
    total_cache = 0
    total_llm = 0
    cycle_start = time.monotonic()
    prev_output = ""

    try:
        with trace_factory_cycle(run_id=run_id, project_name="factory-tracing-verify", mode="verify") as cycle_span:
            trace_id_hex = format(cycle_span.get_span_context().trace_id, "032x")

            for role in roles:
                prompt_template = AGENT_PROMPTS[role]
                prompt = prompt_template.format(prev_output=_snippet(prev_output, 200)) if "{prev_output}" in prompt_template else prompt_template

                with trace_agent_invocation(
                    role=role,
                    task_summary=prompt,
                    run_id=run_id,
                    project_name="factory-tracing-verify",
                ) as agent_span:
                    traced_env = build_traced_env(base_env=dict(os.environ))

                    parsed, exit_code, duration_ms = _invoke_agent(
                        role=role,
                        prompt=prompt,
                        run_id=run_id,
                        project_name="factory-tracing-verify",
                        traced_env=traced_env,
                    )

                    usage = _extract_usage(parsed)
                    response_text = _extract_response_text(parsed)
                    model = parsed.get("model") or None

                    record_agent_result(
                        agent_span,
                        exit_code=exit_code,
                        duration_ms=duration_ms,
                        input_tokens=usage["input_tokens"],
                        output_tokens=usage["output_tokens"],
                        cost_usd=usage["cost_usd"],
                        response_text=response_text or None,
                        model=model,
                    )

                    total_in += usage["input_tokens"]
                    total_out += usage["output_tokens"]
                    total_cache += usage["cache_read_tokens"]

                    agents_traced.append(AgentTrace(
                        role=role,
                        prompt_snippet=_snippet(prompt),
                        response_snippet=_snippet(response_text),
                        llm_calls=0,
                        tokens_in=usage["input_tokens"],
                        tokens_out=usage["output_tokens"],
                    ))

                    prev_output = response_text
    finally:
        shutdown_tracing()

    total_duration = time.monotonic() - cycle_start
    langfuse_url = f"{config.langfuse_host.rstrip('/')}/trace/{trace_id_hex}"

    print("\nWaiting for spans to flush to Langfuse...")
    time.sleep(5)

    trace_data: dict = {}
    content_checks: list[ContentCheck] = []
    span_count = 0
    try:
        trace_data = _query_langfuse_trace(config, trace_id_hex)
        observations = trace_data.get("observations") or []
        span_count = len(observations) + 1

        llm_spans = [o for o in observations if "claude_code.llm_request" in (o.get("name") or "")]
        total_llm = len(llm_spans)

        agent_obs = [o for o in observations if "invoke_agent" in (o.get("name") or "")]
        for agent_trace in agents_traced:
            matching = [o for o in agent_obs if agent_trace.role in (o.get("name") or "")]
            if matching:
                agent_id = matching[0].get("id")
                if agent_id:
                    children = _find_children(observations, agent_id)
                    agent_trace.llm_calls = len([c for c in children if "claude_code.llm_request" in (c.get("name") or "")])

        content_checks = _validate_content(trace_data, roles)

    except Exception as exc:
        content_checks = [ContentCheck(
            name="langfuse_query", passed=False,
            expected="successful query", actual=f"Failed: {exc}",
        )]

    success = all(c.passed for c in content_checks)

    result = VerificationResult(
        trace_id=trace_id_hex,
        langfuse_url=langfuse_url,
        span_count=span_count,
        agents_traced=agents_traced,
        total_llm_calls=total_llm,
        total_tokens={"input": total_in, "output": total_out, "cache_read": total_cache},
        total_duration_seconds=round(total_duration, 2),
        content_checks=content_checks,
        success=success,
    )

    _print_report(result, trace_data)
    return result


def _print_span_tree(observations: list[dict], parent_id: str | None, indent: int = 0) -> None:
    children = [o for o in observations if o.get("parentObservationId") == parent_id]
    children.sort(key=lambda o: o.get("startTime", ""))
    for obs in children:
        name = obs.get("name", "?")
        model = _get_obs_attribute(obs, "gen_ai.request.model") or ""
        input_t = _get_obs_attribute(obs, "gen_ai.usage.input_tokens") or 0
        output_t = _get_obs_attribute(obs, "gen_ai.usage.output_tokens") or 0
        prefix = "  " * indent + ("├─ " if indent > 0 else "")
        info_parts = []
        if model:
            info_parts.append(f"model={model}")
        if input_t or output_t:
            info_parts.append(f"tokens={input_t}/{output_t}")
        info = f" ({', '.join(info_parts)})" if info_parts else ""
        print(f"  {prefix}{name}{info}")
        _print_span_tree(observations, obs.get("id"), indent + 1)


def _print_report(result: VerificationResult, trace_data: dict) -> None:
    print(f"\n{'='*70}")
    print("Factory Tracing — Multi-Agent Verification Report")
    print(f"{'='*70}")
    print(f"Trace ID:       {result.trace_id}")
    print(f"Langfuse:       {result.langfuse_url}")
    print(f"Span count:     {result.span_count}")
    print(f"Duration:       {result.total_duration_seconds}s")
    print(f"Total tokens:   in={result.total_tokens['input']}  out={result.total_tokens['output']}  cache={result.total_tokens['cache_read']}")
    print(f"Total LLM calls: {result.total_llm_calls}")

    if result.agents_traced:
        print(f"\n{'─'*70}")
        print("Agent Details:")
        for agent in result.agents_traced:
            print(f"\n  [{agent.role}]")
            print(f"    Prompt:   {agent.prompt_snippet}")
            print(f"    Response: {agent.response_snippet}")
            print(f"    LLM calls: {agent.llm_calls}  tokens: in={agent.tokens_in} out={agent.tokens_out}")

    observations = trace_data.get("observations") or [] if trace_data else []
    if observations:
        print(f"\n{'─'*70}")
        print("Span Tree:")
        print(f"  factory.cycle (root)")
        root_children = [o for o in observations if not o.get("parentObservationId")]
        if root_children:
            for obs in root_children:
                _print_span_tree(observations, None, 0)
                break
        else:
            _print_span_tree(observations, None, 0)

    print(f"\n{'─'*70}")
    print("Content Quality Checks:")
    for c in result.content_checks:
        status = "PASS" if c.passed else "FAIL"
        print(f"  [{status}] {c.name}")
        print(f"         expected: {c.expected}")
        print(f"         actual:   {c.actual}")

    print(f"\n{'─'*70}")
    print(f"Summary: {len(result.agents_traced)} agents traced, {result.total_llm_calls} LLM calls, "
          f"{result.total_tokens['input'] + result.total_tokens['output']} total tokens, "
          f"{result.total_duration_seconds}s")

    passed = sum(1 for c in result.content_checks if c.passed)
    total = len(result.content_checks)
    overall = "ALL CHECKS PASSED" if result.success else f"FAILED ({passed}/{total} passed)"
    print(f"Overall: {overall}")
    print(f"{'='*70}\n")
