"""End-to-end verification: run a real multi-agent factory cycle and validate ALL 10 criteria in Langfuse.

This module is dev/verification tooling — it MAY import langfuse and dotenv.
It must NOT be imported by production tracing code (config, provider, spans, propagation, integration).
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

from dotenv import load_dotenv

from .config import TracingConfig
from .executor import run_traced_agent, AgentResult
from .propagation import build_traced_env
from .provider import get_tracer_provider, shutdown_tracing
from .spans import trace_factory_cycle


RESEARCHER_PROMPT = (
    "Read the file pyproject.toml in this project and list the package dependencies. "
    "Then explain what the package does based on the code structure."
)

STRATEGIST_PROMPT_TEMPLATE = (
    "Based on these findings about the project: {researcher_output}. "
    "Read src/factory_tracing/executor.py and propose one specific improvement. Be concise."
)


@dataclass
class CriterionResult:
    id: str
    name: str
    passed: bool
    detail: str


@dataclass
class AgentSummary:
    role: str
    num_turns: int
    num_tools: int
    prompt_snippet: str
    response_snippet: str
    tokens_in: int
    tokens_out: int


@dataclass
class VerificationResult:
    trace_id: str
    langfuse_url: str
    span_count: int
    agent_count: int
    llm_call_count: int
    tool_call_count: int
    duration_seconds: float
    tokens_in: int
    tokens_out: int
    agents: list[AgentSummary] = field(default_factory=list)
    criteria: list[CriterionResult] = field(default_factory=list)
    success: bool = False


def _short_uuid() -> str:
    return uuid.uuid4().hex[:8]


def _snippet(text: str, max_len: int = 100) -> str:
    if not text:
        return "(empty)"
    s = text.replace("\n", " ").strip()
    return s[:max_len] + "..." if len(s) > max_len else s


def _query_langfuse_trace(config: TracingConfig, trace_id: str, max_retries: int = 8, delay: float = 3.0) -> dict:
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
                data = json.loads(resp.read().decode())
                observations = data.get("observations") or []
                if len(observations) >= 2 or attempt >= max_retries - 1:
                    return data
        except Exception as exc:
            last_error = exc
        if attempt < max_retries - 1:
            time.sleep(delay)

    raise RuntimeError(f"Failed to fetch trace {trace_id} after {max_retries} attempts: {last_error}")


def _find_children(observations: list[dict], parent_id: str | None) -> list[dict]:
    return [o for o in observations if o.get("parentObservationId") == parent_id]


def _obs_has_content(obs: dict) -> tuple[bool, bool]:
    inp = obs.get("input")
    out = obs.get("output")
    has_in = inp is not None
    has_out = out is not None
    return has_in, has_out


def _check_c1(observations: list[dict]) -> CriterionResult:
    """C1: Real factory cycle — at least 2 agents, each with multiple LLM calls and tool use."""
    agent_obs = [o for o in observations if (o.get("name") or "").startswith("invoke_agent")]
    if len(agent_obs) < 2:
        return CriterionResult("C1", "real_factory_cycle", False,
                               f"Only {len(agent_obs)} agent(s) found, need >= 2")

    agents_with_tools = 0
    for agent in agent_obs:
        agent_id = agent.get("id")
        children = _find_children(observations, agent_id)
        llm_children = [c for c in children if (c.get("name") or "") == "llm_call"]
        tool_children = [c for c in children if (c.get("name") or "").startswith("tool:")]
        if len(llm_children) >= 1 and len(tool_children) >= 1:
            agents_with_tools += 1

    if agents_with_tools < 2:
        return CriterionResult("C1", "real_factory_cycle", False,
                               f"{agents_with_tools}/2 agents have both LLM calls and tool use")

    return CriterionResult("C1", "real_factory_cycle", True,
                           f"{len(agent_obs)} agents, {agents_with_tools} with tools")


def _check_c2(observations: list[dict], trace_data: dict) -> CriterionResult:
    """C2: Every observation has non-null input AND output."""
    total = len(observations)
    missing = []
    for obs in observations:
        has_in, has_out = _obs_has_content(obs)
        if not has_in or not has_out:
            name = obs.get("name", "?")
            parts = []
            if not has_in:
                parts.append("input=null")
            if not has_out:
                parts.append("output=null")
            missing.append(f"{name} ({', '.join(parts)})")

    trace_input = trace_data.get("input")
    trace_output = trace_data.get("output")
    trace_ok = trace_input is not None and trace_output is not None
    if not trace_ok:
        parts = []
        if trace_input is None:
            parts.append("trace.input=null")
        if trace_output is None:
            parts.append("trace.output=null")
        missing.append(f"trace ({', '.join(parts)})")

    if missing:
        return CriterionResult("C2", "all_spans_have_io", False,
                               f"{len(missing)} missing: {'; '.join(missing[:5])}")

    return CriterionResult("C2", "all_spans_have_io", True,
                           f"{total}/{total} observations + trace have input+output")


def _check_c3(observations: list[dict]) -> CriterionResult:
    """C3: Every llm_call span has meaningful input, output, model, and token counts."""
    llm_spans = [o for o in observations if (o.get("name") or "") == "llm_call"]
    if not llm_spans:
        return CriterionResult("C3", "llm_content", False, "No llm_call spans found")

    issues = []
    for i, llm in enumerate(llm_spans):
        inp = llm.get("input")
        out = llm.get("output")
        model = llm.get("model") or llm.get("modelId")

        inp_str = json.dumps(inp) if inp is not None else ""
        out_str = json.dumps(out) if out is not None else ""

        if not inp or not inp_str.strip() or inp_str in ('""', "null", '"{}"'):
            issues.append(f"llm_call #{i} missing input")
        elif "[conversation context]" in inp_str and "prompt" not in inp_str:
            pass

        if not out or not out_str.strip() or out_str in ('""', "null", '"{}"'):
            issues.append(f"llm_call #{i} missing output")

        usage = llm.get("usage") or llm.get("usageDetails") or {}
        input_tokens = usage.get("input") or usage.get("inputTokens") or usage.get("input_tokens") or 0
        output_tokens = usage.get("output") or usage.get("outputTokens") or usage.get("output_tokens") or 0

        if not model and not _get_metadata_attr(llm, "gen_ai.request.model"):
            issues.append(f"llm_call #{i} missing model")
        if input_tokens == 0 and output_tokens == 0:
            meta_in = _get_metadata_attr(llm, "gen_ai.usage.input_tokens")
            meta_out = _get_metadata_attr(llm, "gen_ai.usage.output_tokens")
            if not meta_in and not meta_out:
                issues.append(f"llm_call #{i} zero tokens")

    if issues:
        return CriterionResult("C3", "llm_content", False, "; ".join(issues[:5]))

    return CriterionResult("C3", "llm_content", True,
                           f"{len(llm_spans)} llm_call spans all have content, model, tokens")


def _check_c4(observations: list[dict]) -> CriterionResult:
    """C4: Every tool:* span has non-null input (args) and output (result)."""
    tool_spans = [o for o in observations if (o.get("name") or "").startswith("tool:")]
    if not tool_spans:
        return CriterionResult("C4", "tool_io", False, "No tool:* spans found")

    issues = []
    for i, tool in enumerate(tool_spans):
        name = tool.get("name", "?")
        has_in, has_out = _obs_has_content(tool)
        if not has_in:
            issues.append(f"{name} #{i} input=null")
        if not has_out:
            issues.append(f"{name} #{i} output=null")

    if issues:
        return CriterionResult("C4", "tool_io", False, "; ".join(issues[:5]))

    return CriterionResult("C4", "tool_io", True,
                           f"{len(tool_spans)} tool spans all have input+output")


def _check_c5(observations: list[dict]) -> CriterionResult:
    """C5: Hierarchy — factory.cycle root, invoke_agent children, llm/tool grandchildren."""
    obs_ids = {o.get("id") for o in observations if o.get("id")}
    issues = []

    roots = [o for o in observations if not o.get("parentObservationId")]
    agent_obs = [o for o in observations if (o.get("name") or "").startswith("invoke_agent")]
    llm_tool_obs = [o for o in observations
                    if (o.get("name") or "") == "llm_call" or (o.get("name") or "").startswith("tool:")]

    for agent in agent_obs:
        parent = agent.get("parentObservationId")
        if parent is not None:
            parent_obs = next((o for o in observations if o.get("id") == parent), None)
            if parent_obs and (parent_obs.get("name") or "") != "factory.cycle":
                issues.append(f"invoke_agent not child of factory.cycle (parent: {parent_obs.get('name')})")

    for obs in llm_tool_obs:
        parent = obs.get("parentObservationId")
        if parent is None:
            issues.append(f"{obs.get('name')} has no parent (orphaned)")
        else:
            parent_obs = next((o for o in observations if o.get("id") == parent), None)
            if parent_obs and not (parent_obs.get("name") or "").startswith("invoke_agent"):
                issues.append(f"{obs.get('name')} parent is {parent_obs.get('name')}, expected invoke_agent")

    if issues:
        return CriterionResult("C5", "hierarchy", False, "; ".join(issues[:5]))

    return CriterionResult("C5", "hierarchy", True,
                           f"Correct tree: {len(roots)} root(s), {len(agent_obs)} agents, "
                           f"{len(llm_tool_obs)} llm/tool spans")


def _check_c6(observations: list[dict]) -> CriterionResult:
    """C6: At least one invoke_agent has >= 3 child spans (multi-turn)."""
    agent_obs = [o for o in observations if (o.get("name") or "").startswith("invoke_agent")]
    max_children = 0
    best_agent = ""
    for agent in agent_obs:
        children = _find_children(observations, agent.get("id"))
        if len(children) > max_children:
            max_children = len(children)
            best_agent = agent.get("name", "?")

    if max_children >= 3:
        return CriterionResult("C6", "multi_turn", True,
                               f"{best_agent} has {max_children} child spans")

    return CriterionResult("C6", "multi_turn", False,
                           f"Max child count is {max_children} (need >= 3)")


def _check_c7(observations: list[dict]) -> CriterionResult:
    """C7: Strategist input contains substring from researcher output."""
    agent_obs = [o for o in observations if (o.get("name") or "").startswith("invoke_agent")]
    researcher = next((o for o in agent_obs if "researcher" in (o.get("name") or "")), None)
    strategist = next((o for o in agent_obs if "strategist" in (o.get("name") or "")), None)

    if not researcher or not strategist:
        return CriterionResult("C7", "agent_flow", False,
                               f"Missing: researcher={'found' if researcher else 'missing'}, "
                               f"strategist={'found' if strategist else 'missing'}")

    researcher_output = researcher.get("output")
    strategist_input = strategist.get("input")

    if not researcher_output or not strategist_input:
        return CriterionResult("C7", "agent_flow", False,
                               f"researcher.output={'set' if researcher_output else 'null'}, "
                               f"strategist.input={'set' if strategist_input else 'null'}")

    res_text = json.dumps(researcher_output) if not isinstance(researcher_output, str) else researcher_output
    strat_text = json.dumps(strategist_input) if not isinstance(strategist_input, str) else strategist_input

    words = [w for w in res_text.split() if len(w) > 5 and w.isalpha()]
    matches = [w for w in words[:30] if w.lower() in strat_text.lower()]

    if matches:
        return CriterionResult("C7", "agent_flow", True,
                               f"Strategist input references researcher output "
                               f"(matched: {', '.join(matches[:3])})")

    return CriterionResult("C7", "agent_flow", False,
                           "No researcher output substring found in strategist input")


def _check_c8(observations: list[dict]) -> CriterionResult:
    """C8: No claude_code.* spans, exactly one invoke_agent per role, no orphans."""
    issues = []

    claude_spans = [o for o in observations if (o.get("name") or "").startswith("claude_code.")]
    if claude_spans:
        issues.append(f"{len(claude_spans)} claude_code.* span(s) found")

    agent_obs = [o for o in observations if (o.get("name") or "").startswith("invoke_agent")]
    role_counts: dict[str, int] = {}
    for agent in agent_obs:
        name = agent.get("name", "")
        role_counts[name] = role_counts.get(name, 0) + 1
    for name, count in role_counts.items():
        if count > 1:
            issues.append(f"Duplicate: {name} appears {count} times")

    obs_ids = {o.get("id") for o in observations if o.get("id")}
    for obs in observations:
        parent = obs.get("parentObservationId")
        if parent is not None and parent not in obs_ids:
            issues.append(f"Orphan: {obs.get('name')} has parentId {parent} not in observation set")

    if issues:
        return CriterionResult("C8", "no_duplicates", False, "; ".join(issues[:5]))

    return CriterionResult("C8", "no_duplicates", True,
                           f"No duplicates, no claude_code spans, no orphans")


def _check_c9(trace_data: dict) -> CriterionResult:
    """C9: Trace-level metadata — input, output, name all non-null."""
    issues = []
    trace_input = trace_data.get("input")
    trace_output = trace_data.get("output")
    trace_name = trace_data.get("name")

    if trace_input is None:
        issues.append("trace.input=null")
    if trace_output is None:
        issues.append("trace.output=null")
    if trace_name != "factory.cycle":
        issues.append(f"trace.name='{trace_name}' (expected 'factory.cycle')")

    if issues:
        return CriterionResult("C9", "trace_metadata", False, "; ".join(issues))

    return CriterionResult("C9", "trace_metadata", True,
                           f"name='{trace_name}', input+output set")


def _get_metadata_attr(obs: dict, key: str):
    metadata = obs.get("metadata")
    if isinstance(metadata, dict):
        attrs = metadata.get("attributes", {})
        if isinstance(attrs, dict):
            if key in attrs:
                return attrs[key]
    return None


def _count_by_type(observations: list[dict]) -> tuple[int, int, int]:
    agents = len([o for o in observations if (o.get("name") or "").startswith("invoke_agent")])
    llm_calls = len([o for o in observations if (o.get("name") or "") == "llm_call"])
    tool_calls = len([o for o in observations if (o.get("name") or "").startswith("tool:")])
    return agents, llm_calls, tool_calls


def _build_agent_summaries(observations: list[dict], agent_results: dict[str, AgentResult]) -> list[AgentSummary]:
    summaries = []
    agent_obs = [o for o in observations if (o.get("name") or "").startswith("invoke_agent")]
    agent_obs.sort(key=lambda o: o.get("startTime", ""))

    for agent in agent_obs:
        name = agent.get("name", "")
        role = name.replace("invoke_agent ", "").strip()
        agent_id = agent.get("id")
        children = _find_children(observations, agent_id) if agent_id else []
        tool_children = [c for c in children if (c.get("name") or "").startswith("tool:")]

        ar = agent_results.get(role)
        summaries.append(AgentSummary(
            role=role,
            num_turns=ar.num_turns if ar else len(children),
            num_tools=len(tool_children),
            prompt_snippet=_snippet(ar.response_text[:200] if ar else "", 100) if ar else "(no data)",
            response_snippet=_snippet(ar.response_text if ar else "", 100),
            tokens_in=ar.input_tokens if ar else 0,
            tokens_out=ar.output_tokens if ar else 0,
        ))
    return summaries


def _print_report(result: VerificationResult) -> None:
    print(f"\n{'=' * 70}")
    print("Factory Tracing Verification — 10 Criteria")
    print(f"{'=' * 70}")
    print(f"Trace: {result.trace_id}")
    print(f"URL:   {result.langfuse_url}")
    print(f"Spans: {result.span_count} | Agents: {result.agent_count} | "
          f"LLM calls: {result.llm_call_count} | Tool calls: {result.tool_call_count}")
    print(f"Duration: {result.duration_seconds:.1f}s | "
          f"Tokens: in={result.tokens_in} out={result.tokens_out}")

    if result.agents:
        print(f"\nAgent Summary:")
        for agent in result.agents:
            print(f"  [{agent.role}] {agent.num_turns} turns, {agent.num_tools} tools")
            print(f"    prompt:   {agent.prompt_snippet}")
            print(f"    response: {agent.response_snippet}")

    print(f"\nCriteria:")
    for c in result.criteria:
        status = "PASS" if c.passed else "FAIL"
        print(f"  [{status}] {c.id:<4} {c.name:<22} {c.detail}")

    passed = sum(1 for c in result.criteria if c.passed)
    total = len(result.criteria)
    if result.success:
        print(f"\nResult: PASSED ({passed}/{total})")
    else:
        print(f"\nResult: FAILED ({passed}/{total} passed)")
    print(f"{'=' * 70}")


def run_verification() -> VerificationResult:
    load_dotenv()
    config = TracingConfig.from_env()

    if not config.enabled:
        result = VerificationResult(
            trace_id="", langfuse_url="", span_count=0,
            agent_count=0, llm_call_count=0, tool_call_count=0,
            duration_seconds=0, tokens_in=0, tokens_out=0,
            criteria=[CriterionResult("C0", "tracing_enabled", False,
                                      "FACTORY_TRACING_ENABLED is not set to true")],
            success=False,
        )
        _print_report(result)
        return result

    provider = get_tracer_provider(config)
    if provider is None:
        result = VerificationResult(
            trace_id="", langfuse_url="", span_count=0,
            agent_count=0, llm_call_count=0, tool_call_count=0,
            duration_seconds=0, tokens_in=0, tokens_out=0,
            criteria=[CriterionResult("C0", "provider_init", False,
                                      "TracerProvider failed to initialize")],
            success=False,
        )
        _print_report(result)
        return result

    run_id = f"verify-{_short_uuid()}"
    trace_id_hex = ""
    agent_results: dict[str, AgentResult] = {}
    cycle_start = time.monotonic()
    project_cwd = os.getcwd()

    print("Running 2-agent verification cycle with tool-forcing prompts...")
    print(f"  CWD: {project_cwd}")
    print(f"  Run: {run_id}")

    try:
        with trace_factory_cycle(run_id=run_id, project_name="factory-tracing-verify", mode="verify") as cycle_span:
            trace_id_hex = format(cycle_span.get_span_context().trace_id, "032x")
            print(f"  Trace: {trace_id_hex}")

            traced_env = build_traced_env(base_env=dict(os.environ))

            print("\n  [1/2] Invoking researcher agent...")
            researcher_result = run_traced_agent(
                prompt=RESEARCHER_PROMPT,
                role="researcher",
                run_id=run_id,
                project_name="factory-tracing-verify",
                cwd=project_cwd,
                env=traced_env,
            )
            agent_results["researcher"] = researcher_result
            print(f"        exit={researcher_result.exit_code}, "
                  f"turns={researcher_result.num_turns}, "
                  f"tokens={researcher_result.input_tokens}/{researcher_result.output_tokens}")

            researcher_output = _snippet(researcher_result.response_text, 500)
            strategist_prompt = STRATEGIST_PROMPT_TEMPLATE.format(researcher_output=researcher_output)

            print("  [2/2] Invoking strategist agent...")
            strategist_result = run_traced_agent(
                prompt=strategist_prompt,
                role="strategist",
                run_id=run_id,
                project_name="factory-tracing-verify",
                cwd=project_cwd,
                env=traced_env,
            )
            agent_results["strategist"] = strategist_result
            print(f"        exit={strategist_result.exit_code}, "
                  f"turns={strategist_result.num_turns}, "
                  f"tokens={strategist_result.input_tokens}/{strategist_result.output_tokens}")

            cycle_span.set_attribute("langfuse.span.input", json.dumps({
                "agents": ["researcher", "strategist"],
                "run_id": run_id,
                "project_cwd": project_cwd,
            }))
            cycle_span.set_attribute("langfuse.span.output", json.dumps({
                "researcher": _snippet(researcher_result.response_text, 300),
                "strategist": _snippet(strategist_result.response_text, 300),
                "agents_completed": 2,
            }))
    finally:
        shutdown_tracing()

    total_duration = time.monotonic() - cycle_start
    langfuse_url = f"{config.langfuse_host.rstrip('/')}/trace/{trace_id_hex}"

    print("\nWaiting for spans to flush to Langfuse...")
    time.sleep(5)

    try:
        trace_data = _query_langfuse_trace(config, trace_id_hex)
    except Exception as exc:
        result = VerificationResult(
            trace_id=trace_id_hex, langfuse_url=langfuse_url, span_count=0,
            agent_count=0, llm_call_count=0, tool_call_count=0,
            duration_seconds=round(total_duration, 1), tokens_in=0, tokens_out=0,
            criteria=[CriterionResult("C0", "langfuse_query", False, f"Failed: {exc}")],
            success=False,
        )
        _print_report(result)
        return result

    observations = trace_data.get("observations") or []
    agent_count, llm_call_count, tool_call_count = _count_by_type(observations)
    agent_summaries = _build_agent_summaries(observations, agent_results)

    criteria = [
        _check_c1(observations),
        _check_c2(observations, trace_data),
        _check_c3(observations),
        _check_c4(observations),
        _check_c5(observations),
        _check_c6(observations),
        _check_c7(observations),
        _check_c8(observations),
        _check_c9(trace_data),
        CriterionResult("C10", "automated", True, "All checks ran automatically"),
    ]

    success = all(c.passed for c in criteria)

    total_in = sum(ar.input_tokens for ar in agent_results.values())
    total_out = sum(ar.output_tokens for ar in agent_results.values())

    result = VerificationResult(
        trace_id=trace_id_hex,
        langfuse_url=langfuse_url,
        span_count=len(observations),
        agent_count=agent_count,
        llm_call_count=llm_call_count,
        tool_call_count=tool_call_count,
        duration_seconds=round(total_duration, 1),
        tokens_in=total_in,
        tokens_out=total_out,
        agents=agent_summaries,
        criteria=criteria,
        success=success,
    )

    _print_report(result)
    return result
