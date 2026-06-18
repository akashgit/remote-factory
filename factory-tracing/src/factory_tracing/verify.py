"""10-criteria verification of tracing output against Langfuse."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from factory_tracing.executor import AgentResult, run_traced_agent
from factory_tracing.provider import shutdown

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class VerificationReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)


def _query_langfuse_traces(
    host: str, public_key: str, secret_key: str,
) -> list[dict]:
    """Query Langfuse REST API for recent traces."""
    import base64

    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    url = f"{host.rstrip('/')}/api/public/traces?limit=10&orderBy=timestamp.desc"
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("data", [])
    except Exception as e:
        logger.error("Failed to query Langfuse: %s", e)
        return []


def _query_langfuse_observations(
    host: str, public_key: str, secret_key: str, trace_id: str,
) -> list[dict]:
    """Query Langfuse REST API for observations (spans) on a trace."""
    import base64

    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    url = f"{host.rstrip('/')}/api/public/observations?traceId={trace_id}&limit=100"
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("data", [])
    except Exception as e:
        logger.error("Failed to query Langfuse observations: %s", e)
        return []


def _load_system_prompt() -> str | None:
    """Load CLAUDE.md from the project root as the system prompt."""
    candidates = [
        Path.cwd() / "CLAUDE.md",
        Path(__file__).resolve().parent.parent.parent.parent / "CLAUDE.md",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text()
    return None


def run_verification(
    host: str | None = None,
    public_key: str | None = None,
    secret_key: str | None = None,
) -> VerificationReport:
    """Run two traced agents and verify spans against 10 criteria."""
    host = host or os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
    public_key = public_key or os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = secret_key or os.environ.get("LANGFUSE_SECRET_KEY", "")

    report = VerificationReport()

    system_prompt = _load_system_prompt()

    logger.info("Running agent 1: list files...")
    result1 = run_traced_agent(
        prompt="List the files in the current directory using ls. Just run the command and report the output.",
        role="verifier-ls",
        run_id="verify-1",
        project_name="factory-tracing-verify",
        system_prompt=system_prompt,
        model="anthropic",
    )

    logger.info("Running agent 2: read file...")
    result2 = run_traced_agent(
        prompt="Read the file pyproject.toml and report its contents. Use the cat command.",
        role="verifier-read",
        run_id="verify-2",
        project_name="factory-tracing-verify",
        system_prompt=system_prompt,
        model="anthropic",
    )

    shutdown()

    time.sleep(3)

    # Check 1: agents completed
    report.checks.append(CheckResult(
        "agents_completed",
        result1.return_code == 0 and result2.return_code == 0,
        f"Agent 1 rc={result1.return_code}, Agent 2 rc={result2.return_code}",
    ))

    traces = _query_langfuse_traces(host, public_key, secret_key)

    # Check 2: traces exist
    report.checks.append(CheckResult(
        "traces_exist",
        len(traces) >= 2,
        f"Found {len(traces)} traces",
    ))

    if len(traces) < 1:
        for i in range(3, 11):
            report.checks.append(CheckResult(f"check_{i}", False, "No traces to verify"))
        return report

    all_observations: list[dict] = []
    for t in traces[:2]:
        trace_id = t.get("id", "")
        obs = _query_langfuse_observations(host, public_key, secret_key, trace_id)
        all_observations.extend(obs)

    # Check 3: spans have non-null input
    inputs_present = all(
        o.get("input") is not None
        for o in all_observations
        if o.get("type") in ("SPAN", "GENERATION")
    )
    input_values = [
        str(o.get("input", ""))[:100]
        for o in all_observations
        if o.get("type") in ("SPAN", "GENERATION")
    ]
    report.checks.append(CheckResult(
        "all_spans_have_input",
        inputs_present,
        f"Input values: {input_values[:3]}",
    ))

    # Check 4: spans have non-null output
    outputs_present = all(
        o.get("output") is not None
        for o in all_observations
        if o.get("type") in ("SPAN", "GENERATION")
    )
    output_values = [
        str(o.get("output", ""))[:100]
        for o in all_observations
        if o.get("type") in ("SPAN", "GENERATION")
    ]
    report.checks.append(CheckResult(
        "all_spans_have_output",
        outputs_present,
        f"Output values: {output_values[:3]}",
    ))

    # Check 5: llm_call inputs are structured JSON arrays (not flat text)
    llm_inputs_structured = True
    llm_input_samples: list[str] = []
    for o in all_observations:
        if o.get("name", "").startswith("llm_call"):
            inp = o.get("input", "")
            inp_str = json.dumps(inp) if not isinstance(inp, str) else inp
            llm_input_samples.append(inp_str[:80])
            if isinstance(inp_str, str) and not inp_str.lstrip().startswith("["):
                llm_inputs_structured = False
    report.checks.append(CheckResult(
        "llm_inputs_structured_json",
        llm_inputs_structured,
        f"Samples: {llm_input_samples[:2]}",
    ))

    # Check 6: llm_call token counts > 10 (not fabricated)
    tokens_valid = True
    token_samples: list[str] = []
    for o in all_observations:
        if o.get("name", "").startswith("llm_call"):
            usage = o.get("usage", {}) or {}
            input_tokens = usage.get("input", usage.get("inputTokens", 0)) or 0
            token_samples.append(f"input_tokens={input_tokens}")
            if input_tokens <= 10:
                tokens_valid = False
    report.checks.append(CheckResult(
        "llm_tokens_not_fabricated",
        tokens_valid,
        f"Samples: {token_samples[:3]}",
    ))

    # Check 7: system instructions set on agent spans
    sys_instr_found = False
    sys_instr_samples: list[str] = []
    for t in traces[:2]:
        metadata = t.get("metadata", {}) or {}
        inp = t.get("input", {}) or {}
        sys_prompt = None
        if isinstance(inp, dict):
            sys_prompt = inp.get("system_prompt")
        if sys_prompt:
            sys_instr_found = True
            sys_instr_samples.append(str(sys_prompt)[:60])
    report.checks.append(CheckResult(
        "system_instructions_set",
        sys_instr_found,
        f"Found: {sys_instr_samples[:2]}",
    ))

    # Check 8: cost > 0 on agent spans
    cost_positive = False
    cost_values: list[str] = []
    for t in traces[:2]:
        metadata = t.get("metadata", {}) or {}
        output = t.get("output", {}) or {}
        cost = 0.0
        if isinstance(output, dict):
            cost = output.get("cost_usd", 0.0) or 0.0
        cost_values.append(f"cost={cost}")
        if cost > 0:
            cost_positive = True
    report.checks.append(CheckResult(
        "cost_positive",
        cost_positive,
        f"Values: {cost_values}",
    ))

    # Check 9: no '[conversation context]' placeholders
    no_placeholders = True
    for o in all_observations:
        for field_name in ("input", "output"):
            val = str(o.get(field_name, ""))
            if "[conversation context]" in val:
                no_placeholders = False
    report.checks.append(CheckResult(
        "no_conversation_placeholders",
        no_placeholders,
        "Checked all span inputs/outputs for placeholder text",
    ))

    # Check 10: model names have no bracket suffixes
    model_clean = True
    model_samples: list[str] = []
    for o in all_observations:
        model_val = o.get("model", "") or ""
        if model_val:
            model_samples.append(model_val)
            if "[" in model_val:
                model_clean = False
    report.checks.append(CheckResult(
        "model_names_clean",
        model_clean,
        f"Models: {model_samples[:3]}",
    ))

    return report
