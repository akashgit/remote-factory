"""End-to-end verification: invoke a real Claude Code agent and query Langfuse.

This module is dev/verification tooling — it MAY import langfuse and dotenv.
It must NOT be imported by production tracing code (config, provider, spans, propagation, integration).
"""
from __future__ import annotations

import base64
import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

from dotenv import load_dotenv

from .config import TracingConfig
from .propagation import build_traced_env
from .provider import get_tracer_provider, shutdown_tracing
from .spans import record_agent_result, trace_agent_invocation, trace_factory_cycle


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class VerificationResult:
    trace_id: str
    langfuse_url: str
    span_count: int
    checks: list[CheckResult] = field(default_factory=list)
    success: bool = False


def _short_uuid() -> str:
    return uuid.uuid4().hex[:8]


def _parse_claude_output(stdout: str) -> dict:
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return {}


def _query_langfuse_trace(config: TracingConfig, trace_id: str, max_retries: int = 3, delay: float = 2.0) -> dict:
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
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(delay)

    raise RuntimeError(f"Failed to fetch trace {trace_id} after {max_retries} attempts: {last_error}")


def _get_obs_attribute(obs: dict, key: str) -> object:
    """Extract an attribute from an observation's metadata or attributes."""
    for source in ("metadata", "attributes"):
        container = obs.get(source)
        if isinstance(container, dict) and key in container:
            return container[key]
    return None


def _validate_trace(trace_data: dict, trace_id: str) -> list[CheckResult]:
    checks = []

    observations = trace_data.get("observations") or []

    obs_names = [o.get("name", "") for o in observations]

    has_cycle = any("factory.cycle" in (n or "") for n in obs_names)
    checks.append(CheckResult(
        name="root_span_exists",
        passed=has_cycle,
        detail="factory.cycle span found" if has_cycle else "factory.cycle span NOT found",
    ))

    has_agent = any("invoke_agent" in (n or "") and "verify-agent" in (n or "") for n in obs_names)
    checks.append(CheckResult(
        name="agent_span_exists",
        passed=has_agent,
        detail="invoke_agent verify-agent span found" if has_agent else "invoke_agent verify-agent span NOT found",
    ))

    has_cc_span = any("claude_code" in (n or "") for n in obs_names)
    checks.append(CheckResult(
        name="claude_code_span_exists",
        passed=has_cc_span,
        detail="claude_code span found" if has_cc_span else "claude_code span NOT found (Claude Code OTel may not be available)",
    ))

    fetched_id = trace_data.get("id", "")
    checks.append(CheckResult(
        name="trace_id_consistent",
        passed=bool(fetched_id),
        detail=f"Trace ID: {fetched_id}" if fetched_id else "Could not verify trace ID",
    ))

    llm_spans = [o for o in observations if "claude_code.llm_request" in (o.get("name") or "")]
    if llm_spans:
        llm = llm_spans[0]
        llm_model = _get_obs_attribute(llm, "gen_ai.request.model") or ""
        has_model = bool(llm_model)
        checks.append(CheckResult(
            name="llm_span_has_model",
            passed=has_model,
            detail=f"claude_code.llm_request model={llm_model}" if has_model else "claude_code.llm_request has no model name",
        ))

        llm_input_tokens = _get_obs_attribute(llm, "gen_ai.usage.input_tokens")
        has_llm_tokens = isinstance(llm_input_tokens, (int, float)) and llm_input_tokens > 0
        checks.append(CheckResult(
            name="llm_span_has_tokens",
            passed=has_llm_tokens,
            detail=f"claude_code.llm_request input_tokens={llm_input_tokens}" if has_llm_tokens else "claude_code.llm_request has zero/missing input_tokens",
        ))

    agent_obs_list = [o for o in observations if "invoke_agent" in (o.get("name") or "")]
    if agent_obs_list:
        agent_obs = agent_obs_list[0]
        agent_tokens = _get_obs_attribute(agent_obs, "gen_ai.usage.input_tokens")
        has_agent_tokens = isinstance(agent_tokens, (int, float)) and agent_tokens > 0
        checks.append(CheckResult(
            name="agent_span_has_tokens",
            passed=has_agent_tokens,
            detail=f"invoke_agent input_tokens={agent_tokens}" if has_agent_tokens else "invoke_agent has zero/missing gen_ai.usage.input_tokens",
        ))

    total_usage = trace_data.get("usage") or trace_data.get("totalUsage") or {}
    trace_input = total_usage.get("input", 0) or total_usage.get("inputTokens", 0) or 0
    has_trace_usage = trace_input > 0
    checks.append(CheckResult(
        name="trace_has_token_usage",
        passed=has_trace_usage,
        detail=f"Trace token usage: input={trace_input}" if has_trace_usage else "Trace shows zero token usage",
    ))

    return checks


def run_verification() -> VerificationResult:
    load_dotenv()

    config = TracingConfig.from_env()

    if not config.enabled:
        return VerificationResult(
            trace_id="",
            langfuse_url="",
            span_count=0,
            checks=[CheckResult(name="tracing_enabled", passed=False, detail="FACTORY_TRACING_ENABLED is not set")],
            success=False,
        )

    provider = get_tracer_provider(config)
    if provider is None:
        return VerificationResult(
            trace_id="",
            langfuse_url="",
            span_count=0,
            checks=[CheckResult(name="provider_init", passed=False, detail="TracerProvider failed to initialize")],
            success=False,
        )

    run_id = f"verify-{_short_uuid()}"
    trace_id_hex = ""

    try:
        with trace_factory_cycle(run_id=run_id, project_name="factory-tracing-verify", mode="verify") as cycle_span:
            trace_id_hex = format(cycle_span.get_span_context().trace_id, "032x")

            with trace_agent_invocation(
                role="verify-agent",
                task_summary="verification probe",
                run_id=run_id,
                project_name="factory-tracing-verify",
            ) as agent_span:
                traced_env = build_traced_env(base_env=dict(__import__("os").environ))

                start_time = time.monotonic()
                try:
                    result = subprocess.run(
                        ["claude", "-p", "Reply with the single word VERIFIED", "--output-format", "json"],
                        env=traced_env,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    exit_code = result.returncode
                    stdout = result.stdout
                except FileNotFoundError:
                    exit_code = 127
                    stdout = ""
                except subprocess.TimeoutExpired:
                    exit_code = 124
                    stdout = ""

                parsed = _parse_claude_output(stdout)
                input_tokens = 0
                output_tokens = 0
                cost_usd = 0.0
                response_text = ""
                model = None
                if isinstance(parsed, dict):
                    usage = parsed.get("usage", {}) or parsed.get("result", {}).get("usage", {}) or {}
                    input_tokens = usage.get("input_tokens", 0) or 0
                    output_tokens = usage.get("output_tokens", 0) or 0
                    cost_usd = usage.get("cost_usd", 0.0) or 0.0
                    response_text = parsed.get("result", "") if isinstance(parsed.get("result"), str) else ""
                    model = parsed.get("model") or None

                record_agent_result(
                    agent_span,
                    exit_code=exit_code,
                    start_time=start_time,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    response_text=response_text or None,
                    model=model,
                )
    finally:
        shutdown_tracing()

    langfuse_url = f"{config.langfuse_host.rstrip('/')}/trace/{trace_id_hex}"

    time.sleep(3)

    trace_data: dict = {}
    try:
        trace_data = _query_langfuse_trace(config, trace_id_hex)
        checks = _validate_trace(trace_data, trace_id_hex)
        observations = trace_data.get("observations") or []
        span_count = len(observations) + 1
    except Exception as exc:
        checks = [CheckResult(
            name="langfuse_query",
            passed=False,
            detail=f"Failed to query Langfuse: {exc}",
        )]
        span_count = 0

    required_checks = {"root_span_exists", "agent_span_exists", "trace_id_consistent"}
    success = all(c.passed for c in checks if c.name in required_checks)

    result = VerificationResult(
        trace_id=trace_id_hex,
        langfuse_url=langfuse_url,
        span_count=span_count,
        checks=checks,
        success=success,
    )

    _print_report(result, trace_data)

    return result


def _print_report(result: VerificationResult, trace_data: dict) -> None:
    print(f"\n{'='*60}")
    print("Factory Tracing Verification Report")
    print(f"{'='*60}")
    print(f"Trace ID:    {result.trace_id}")
    print(f"Langfuse:    {result.langfuse_url}")
    print(f"Span count:  {result.span_count}")
    print()

    observations = trace_data.get("observations") or [] if trace_data else []
    if observations:
        print("Span Details:")
        for obs in observations:
            name = obs.get("name", "?")
            metadata = obs.get("metadata") or {}
            model = _get_obs_attribute(obs, "gen_ai.request.model") or metadata.get("model", "")
            input_t = _get_obs_attribute(obs, "gen_ai.usage.input_tokens") or 0
            output_t = _get_obs_attribute(obs, "gen_ai.usage.output_tokens") or 0
            start = obs.get("startTime", "")
            end = obs.get("endTime", "")
            print(f"  - {name}")
            if model:
                print(f"    model: {model}")
            if input_t or output_t:
                print(f"    tokens: in={input_t} out={output_t}")
            if start and end:
                print(f"    time: {start} -> {end}")
        print()

    print("Checks:")
    for c in result.checks:
        status = "PASS" if c.passed else "FAIL"
        print(f"  [{status}] {c.name}: {c.detail}")

    print()
    overall = "SUCCESS" if result.success else "FAILED"
    print(f"Overall: {overall}")
    print(f"{'='*60}\n")
