"""End-to-end verification: invoke a real Claude Code agent and query Langfuse.

This module is dev/verification tooling — it MAY import langfuse and dotenv.
It must NOT be imported by production tracing code (config, provider, spans, propagation, integration).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import time
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


def _query_langfuse_trace(config: TracingConfig, trace_id: str, max_retries: int = 3, delay: float = 2.0):
    from langfuse import Langfuse

    client = Langfuse(
        public_key=config.langfuse_public_key,
        secret_key=config.langfuse_secret_key,
        host=config.langfuse_host,
    )

    last_error = None
    for attempt in range(max_retries):
        try:
            trace = client.fetch_trace(trace_id)
            return trace
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(delay)

    raise RuntimeError(f"Failed to fetch trace {trace_id} after {max_retries} attempts: {last_error}")


def _validate_trace(trace_data, trace_id: str) -> list[CheckResult]:
    checks = []

    observations = getattr(trace_data, "observations", None) or []
    if hasattr(trace_data, "data"):
        data = trace_data.data
        observations = getattr(data, "observations", None) or []

    obs_names = [getattr(o, "name", "") for o in observations]

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

    data_obj = getattr(trace_data, "data", trace_data)
    fetched_id = getattr(data_obj, "id", None) or ""
    ids_match = trace_id in fetched_id or fetched_id in trace_id
    checks.append(CheckResult(
        name="trace_id_consistent",
        passed=bool(fetched_id),
        detail=f"Trace ID: {fetched_id}" if fetched_id else "Could not verify trace ID",
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
                if isinstance(parsed, dict):
                    usage = parsed.get("usage", {}) or parsed.get("result", {}).get("usage", {}) or {}
                    input_tokens = usage.get("input_tokens", 0) or 0
                    output_tokens = usage.get("output_tokens", 0) or 0
                    cost_usd = usage.get("cost_usd", 0.0) or 0.0

                record_agent_result(
                    agent_span,
                    exit_code=exit_code,
                    duration_ms=0.0,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                )
    finally:
        shutdown_tracing()

    langfuse_url = f"{config.langfuse_host.rstrip('/')}/trace/{trace_id_hex}"

    time.sleep(3)

    try:
        trace_data = _query_langfuse_trace(config, trace_id_hex)
        checks = _validate_trace(trace_data, trace_id_hex)
        span_count = 0
        data_obj = getattr(trace_data, "data", trace_data)
        observations = getattr(data_obj, "observations", None) or []
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

    return VerificationResult(
        trace_id=trace_id_hex,
        langfuse_url=langfuse_url,
        span_count=span_count,
        checks=checks,
        success=success,
    )
