#!/usr/bin/env python3
"""Analyze a failed benchmark run by correlating it with its Langfuse trace.

Produces a structured markdown diagnosis of WHY a benchmark failed:
agent timeline, error events, tool failures, CEO reasoning, and optionally
an LLM-generated root cause analysis via `claude -p`.

Usage:
    python scripts/langfuse/analyze_failure.py <result.json> [--output FILE] [--no-llm] [--verbose]

Degrades gracefully:
  - No matching trace found → template-only output
  - No claude CLI on PATH  → template-only output (same as --no-llm)
  - Missing Langfuse creds → exits 0 with warning

Exit codes:
  0  success (including degraded output)
  1  hard failure (bad input file, invalid JSON)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from langfuse_client import (
    fetch_trace,
    get_agent_spans,
    list_traces,
    load_creds,
    parse_ts,
    truncate,
)
from pull_langfuse_trace import extract_factory_commands, extract_orchestration


def parse_benchmark_timestamp(ts_str: str) -> datetime:
    return datetime.strptime(ts_str, "%Y%m%dT%H%M%SZ")


def find_matching_trace(
    benchmark: str,
    instance_id: str,
    timestamp: datetime,
    duration_seconds: int,
) -> dict | None:
    from_ts = timestamp - timedelta(minutes=5)
    to_ts = timestamp + timedelta(seconds=duration_seconds) + timedelta(minutes=5)

    traces = list_traces(from_ts, to_ts)
    if not traces:
        return None

    candidates = []
    for t in traces:
        name = (t.get("name") or "").lower()
        meta = json.dumps(t.get("metadata") or {}).lower()
        text = name + " " + meta
        if benchmark.lower() in text or instance_id.lower() in text:
            candidates.append(t)

    if not candidates:
        candidates = traces

    return max(candidates, key=lambda t: t.get("latency", 0) or 0)


def extract_error_events(observations: list[dict]) -> list[dict]:
    error_keywords = {"error", "fail", "exception", "timeout", "crash"}
    errors = []
    for o in observations:
        name = (o.get("name") or "").lower()
        level = (o.get("level") or "").upper()
        is_error = level == "ERROR" or any(kw in name for kw in error_keywords)
        if not is_error:
            continue
        output = o.get("output", o.get("input", ""))
        if isinstance(output, dict):
            output = json.dumps(output)
        errors.append({
            "timestamp": (o.get("startTime") or "")[:19],
            "name": o.get("name", "unknown"),
            "level": level or "WARN",
            "type": o.get("type", "EVENT"),
            "text": truncate(str(output), 500),
        })
    return sorted(errors, key=lambda e: e["timestamp"])


def extract_tool_failures(observations: list[dict]) -> list[dict]:
    error_indicators = ["error", "traceback", "exception", "failed", "errno"]
    failures = []
    for o in observations:
        if o.get("type") != "TOOL":
            continue
        output = o.get("output", "")
        if isinstance(output, dict):
            output = json.dumps(output)
        output_lower = str(output).lower()
        if not any(ind in output_lower for ind in error_indicators):
            continue
        failures.append({
            "timestamp": (o.get("startTime") or "")[:19],
            "tool": o.get("name", "unknown"),
            "output": truncate(str(output), 400),
        })
    return sorted(failures, key=lambda f: f["timestamp"])


def format_agent_timeline(agent_spans: list[dict]) -> str:
    if not agent_spans:
        return "_No agent spans found._\n"
    lines = []
    for span in agent_spans:
        start = parse_ts(span.get("startTime"))
        end = parse_ts(span.get("endTime"))
        dur = f"{(end - start).total_seconds():.0f}s" if start and end else "running"
        status = "completed" if end and parse_ts(span.get("endTime")) else "running/interrupted"
        lines.append(f"- **{span['name']}** — {dur} ({status})")
    return "\n".join(lines) + "\n"


def build_context_string(
    agent_spans: list[dict],
    errors: list[dict],
    tool_failures: list[dict],
    timeline: list[dict],
    ceo_reasoning: list[dict],
    factory_commands: list[dict],
    max_chars: int = 8000,
) -> str:
    parts = []

    parts.append("## Agent Timeline")
    for span in agent_spans[:20]:
        start = parse_ts(span.get("startTime"))
        end = parse_ts(span.get("endTime"))
        dur = f"{(end - start).total_seconds():.0f}s" if start and end else "running"
        parts.append(f"  {span['name']}: {dur}")

    parts.append("\n## Errors")
    for e in errors[:10]:
        parts.append(f"  [{e['timestamp']}] {e['name']}: {e['text'][:200]}")

    parts.append("\n## Tool Failures")
    for f in tool_failures[:10]:
        parts.append(f"  [{f['timestamp']}] {f['tool']}: {f['output'][:200]}")

    parts.append("\n## CEO Reasoning (last 5)")
    for msg in ceo_reasoning[-5:]:
        parts.append(f"  [{msg['timestamp']}] {msg['text'][:300]}")

    parts.append("\n## Factory Commands (last 10)")
    for cmd in factory_commands[-10:]:
        parts.append(f"  [{cmd['timestamp']}] $ {cmd['command'][:200]}")
        parts.append(f"    -> {cmd['output_preview'][:150]}")

    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return text


def run_llm_diagnosis(context: str, benchmark: str, instance_id: str) -> str | None:
    if not shutil.which("claude"):
        return None

    prompt = (
        f"You are analyzing a failed benchmark run.\n"
        f"Benchmark: {benchmark}\n"
        f"Instance: {instance_id}\n\n"
        f"Below is the extracted trace data from the run. Analyze it and provide:\n"
        f"1. Which agent failed or was running when the failure occurred\n"
        f"2. What went wrong (specific errors, timeouts, or unexpected behavior)\n"
        f"3. Root cause hypothesis\n"
        f"4. Suggested fix or investigation path\n\n"
        f"Be concise — 4-8 sentences total.\n\n"
        f"--- TRACE DATA ---\n{context}"
    )

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "claude-sonnet-4-6", "--max-turns", "1"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def build_template_diagnosis(
    errors: list[dict],
    tool_failures: list[dict],
    agent_spans: list[dict],
) -> str:
    lines = []
    if not errors and not tool_failures:
        if agent_spans:
            last = agent_spans[-1]
            end = parse_ts(last.get("endTime"))
            if not end:
                lines.append("The last agent span was still running when the benchmark ended — likely a timeout.")
            else:
                lines.append(f"All agent spans completed but the benchmark was not resolved. Last agent: **{last['name']}**.")
        else:
            lines.append("No agent spans or errors found in the trace — the run may have failed before agent execution began.")
    else:
        if errors:
            lines.append(f"Found **{len(errors)} error event(s)** in the trace:")
            for e in errors[:3]:
                lines.append(f"- `{e['name']}` at {e['timestamp']}: {e['text'][:150]}")
        if tool_failures:
            lines.append(f"\nFound **{len(tool_failures)} tool failure(s)**:")
            for f in tool_failures[:3]:
                lines.append(f"- `{f['tool']}` at {f['timestamp']}: {f['output'][:150]}")

    return "\n".join(lines) if lines else "No diagnostic signals extracted from the trace."


def generate_report(
    result_data: dict,
    trace: dict | None,
    trace_id: str | None,
    host: str | None,
    use_llm: bool = True,
    verbose: bool = False,
) -> str:
    benchmark = result_data["benchmark"]
    instance_id = result_data["instance_id"]
    solver = result_data.get("solver", "unknown")
    duration = result_data.get("duration_seconds", 0)

    lines = [
        f"### Failure Analysis: {benchmark} / {instance_id}\n",
        f"**Solver:** {solver}",
        f"**Duration:** {duration}s",
    ]

    if trace_id and host:
        lines.append(f"**Trace:** [{trace_id}]({host}/trace/{trace_id})")

    if trace is None:
        lines.append("\n#### Agent Timeline\n_No matching Langfuse trace found._\n")
        lines.append("#### Failure Signals\n_Unable to extract — no trace available._\n")
        lines.append("#### Diagnosis\nNo trace data available for diagnosis. "
                      "Check that Langfuse credentials are configured and the trace was ingested.\n")
        return "\n".join(lines)

    observations = trace.get("observations", [])
    agent_spans = get_agent_spans(observations)
    errors = extract_error_events(observations)
    tool_failures = extract_tool_failures(observations)
    timeline_data, ceo_reasoning = extract_orchestration(trace)
    factory_commands = extract_factory_commands(trace)

    lines.append("\n#### Agent Timeline")
    lines.append(format_agent_timeline(agent_spans))

    lines.append("#### Failure Signals")
    signal_parts = []
    if errors:
        signal_parts.append(f"**Errors ({len(errors)}):**")
        for e in errors[:5]:
            signal_parts.append(f"- `{e['name']}` ({e['level']}) at {e['timestamp']}: {e['text'][:200]}")
    if tool_failures:
        signal_parts.append(f"\n**Tool Failures ({len(tool_failures)}):**")
        for f in tool_failures[:5]:
            signal_parts.append(f"- `{f['tool']}` at {f['timestamp']}: {f['output'][:200]}")
    if not signal_parts:
        signal_parts.append("_No explicit error events or tool failures detected._")
    lines.append("\n".join(signal_parts))

    lines.append("\n#### Diagnosis")

    diagnosis = None
    if use_llm:
        context = build_context_string(
            agent_spans, errors, tool_failures,
            timeline_data, ceo_reasoning, factory_commands,
        )
        if verbose:
            print(f"[verbose] Context for LLM: {len(context)} chars", file=sys.stderr)
        diagnosis = run_llm_diagnosis(context, benchmark, instance_id)

    if diagnosis:
        lines.append(diagnosis)
    else:
        lines.append(build_template_diagnosis(errors, tool_failures, agent_spans))

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze a failed benchmark run using its Langfuse trace"
    )
    parser.add_argument("result_json", help="Path to benchmark result JSON file")
    parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM diagnosis (template only)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output to stderr")
    args = parser.parse_args()

    result_path = Path(args.result_json)
    if not result_path.exists():
        print(f"ERROR: Result file not found: {result_path}", file=sys.stderr)
        return 1

    try:
        result_data = json.loads(result_path.read_text())
    except (json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: Invalid JSON in {result_path}: {e}", file=sys.stderr)
        return 1

    if result_data.get("resolved", False):
        if args.verbose:
            print("[verbose] Benchmark resolved — nothing to diagnose.", file=sys.stderr)
        return 0

    try:
        host, _, _ = load_creds()
    except (KeyError, Exception) as e:
        print(f"WARNING: Langfuse credentials not available ({e}), skipping trace analysis.", file=sys.stderr)
        report = generate_report(result_data, trace=None, trace_id=None, host=None, use_llm=False)
        _write_output(report, args.output)
        return 0

    ts_str = result_data.get("timestamp", "")
    duration = result_data.get("duration_seconds", 0)
    benchmark = result_data.get("benchmark", "")
    instance_id = result_data.get("instance_id", "")

    trace = None
    trace_id = None
    try:
        timestamp = parse_benchmark_timestamp(ts_str)
        matched = find_matching_trace(benchmark, instance_id, timestamp, duration)
        if matched:
            trace_id = matched.get("id")
            if args.verbose:
                print(f"[verbose] Matched trace: {trace_id}", file=sys.stderr)
            trace = fetch_trace(trace_id, use_cache=False)
        else:
            print(f"WARNING: No matching trace found for {benchmark}/{instance_id} "
                  f"in window around {ts_str}", file=sys.stderr)
    except (ValueError, KeyError) as e:
        print(f"WARNING: Could not search for trace: {e}", file=sys.stderr)
    except Exception as e:
        print(f"WARNING: Trace fetch failed: {e}", file=sys.stderr)

    report = generate_report(
        result_data,
        trace=trace,
        trace_id=trace_id,
        host=host,
        use_llm=not args.no_llm,
        verbose=args.verbose,
    )

    _write_output(report, args.output)
    return 0


def _write_output(report: str, output_path: str | None) -> None:
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(report)
        print(f"Analysis written to {output_path}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    sys.exit(main())
