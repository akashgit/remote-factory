#!/usr/bin/env python3
"""Analyze a failed benchmark run using its Langfuse trace and claude -p.

Usage: python scripts/langfuse/analyze_failure.py <result.json> [--output FILE] [--no-llm] [--summary] [--verbose]
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from langfuse_client import fetch_trace, list_traces, load_creds
from pull_langfuse_trace import extract_factory_commands, extract_orchestration, print_report


def parse_benchmark_timestamp(ts_str: str) -> datetime:
    return datetime.strptime(ts_str, "%Y%m%dT%H%M%SZ")


def find_matching_trace(
    benchmark: str,
    instance_id: str,
    timestamp: datetime,
    duration_seconds: int,
    verbose: bool = False,
) -> dict | None:
    from_ts = timestamp - timedelta(minutes=5)
    to_ts = timestamp + timedelta(seconds=duration_seconds) + timedelta(minutes=5)

    if verbose:
        print(f"[verbose] Searching traces from {from_ts} to {to_ts}", file=sys.stderr)

    traces = list_traces(from_ts, to_ts)
    if verbose:
        print(f"[verbose] Found {len(traces)} traces in window", file=sys.stderr)
    if not traces:
        return None

    metadata_matches = []
    for t in traces:
        meta = t.get("metadata") or {}
        if meta.get("benchmark") == benchmark and meta.get("instance_id") == instance_id:
            metadata_matches.append(t)

    if metadata_matches:
        if verbose:
            print(f"[verbose] Matched {len(metadata_matches)} traces by metadata", file=sys.stderr)
        selected = min(metadata_matches, key=lambda t: t.get("startTime", "") or "")
        if verbose:
            print(f"[verbose] Selected trace: {selected['id']} (earliest)", file=sys.stderr)
        return selected

    candidates = []
    for t in traces:
        name = (t.get("name") or "").lower()
        meta = json.dumps(t.get("metadata") or {}).lower()
        text = name + " " + meta
        if benchmark.lower() in text or instance_id.lower() in text:
            candidates.append(t)

    if verbose:
        print(f"[verbose] Filtered to {len(candidates)} text candidates", file=sys.stderr)

    if not candidates:
        return None

    selected = min(candidates, key=lambda t: t.get("startTime", "") or "")
    if verbose:
        print(f"[verbose] Selected trace: {selected['id']} (earliest)", file=sys.stderr)
    return selected


def format_trace_dump(trace: dict) -> str:
    timeline, ceo_reasoning = extract_orchestration(trace, full=True)
    factory_commands = extract_factory_commands(trace)
    buf = io.StringIO()
    print_report(timeline, ceo_reasoning, factory_commands, file=buf)
    return buf.getvalue()


def run_llm_summary(trace_dump: str, benchmark: str, instance_id: str) -> str | None:
    if not shutil.which("claude"):
        return None

    prompt = (
        f"Benchmark: {benchmark}, Instance: {instance_id}. "
        "Summarize why this benchmark failed in at most 2 sentences. "
        f"Keep it under 140 characters total. Here is the trace: {trace_dump}"
    )

    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def run_llm_analysis(trace_dump: str, benchmark: str, instance_id: str) -> str | None:
    if not shutil.which("claude"):
        return None

    prompt = (
        f"Benchmark: {benchmark}\nInstance: {instance_id}\n\n"
        "Here is the full trace of a failed benchmark run. "
        "Analyze it and explain what went wrong.\n\n"
        f"{trace_dump}"
    )

    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def generate_report(
    result_data: dict,
    trace: dict | None,
    trace_id: str | None,
    host: str | None,
    use_llm: bool = True,
    verbose: bool = False,
    summary: bool = False,
) -> str:
    benchmark = result_data["benchmark"]
    instance_id = result_data["instance_id"]
    solver = result_data.get("solver", "unknown")
    duration = result_data.get("duration_seconds", 0)

    if summary:
        if trace is None or not use_llm:
            return "No trace available for summary."
        trace_dump = format_trace_dump(trace)
        if verbose:
            print(f"[verbose] Summary trace dump: {len(trace_dump)} chars", file=sys.stderr)
        text = run_llm_summary(trace_dump, benchmark, instance_id)
        if text:
            return text
        return "No trace available for summary."

    header = (
        f"### Failure Analysis: {benchmark} / {instance_id}\n\n"
        f"**Solver:** {solver}\n"
        f"**Duration:** {duration}s\n"
    )
    if trace_id and host:
        header += f"**Trace:** [{trace_id}]({host}/trace/{trace_id})\n"

    if trace is None:
        exception_text = (result_data.get("details") or {}).get("exception", "")
        if exception_text:
            return header + "\n#### Exception from Harbor\n\n" + exception_text + "\n"
        return header + "\nNo matching Langfuse trace found.\n"

    trace_dump = format_trace_dump(trace)

    if use_llm:
        if verbose:
            print(f"[verbose] Trace dump: {len(trace_dump)} chars", file=sys.stderr)
        diagnosis = run_llm_analysis(trace_dump, benchmark, instance_id)
        if diagnosis:
            return header + "\n#### Diagnosis\n\n" + diagnosis + "\n"

    return header + "\n#### Trace Timeline\n\n" + trace_dump


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze a failed benchmark run using its Langfuse trace"
    )
    parser.add_argument("result_json", help="Path to benchmark result JSON file")
    parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM analysis, output raw trace")
    parser.add_argument("--summary", action="store_true", help="Output a short 1-2 sentence summary instead of full analysis")
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

    solver = result_data.get("solver", "")
    if solver == "claude-code":
        if args.summary:
            report = "Trace analysis not available for claude-code solver."
        else:
            benchmark = result_data.get("benchmark", "unknown")
            instance_id = result_data.get("instance_id", "unknown")
            duration = result_data.get("duration_seconds", 0)
            report = (
                f"### Failure Analysis: {benchmark} / {instance_id}\n\n"
                f"**Solver:** {solver}\n"
                f"**Duration:** {duration}s\n\n"
                "Trace analysis not available — claude-code solver does not create "
                "factory-managed Langfuse traces.\n"
            )
        _write_output(report, args.output)
        return 0

    try:
        host, _, _ = load_creds()
    except (KeyError, Exception) as e:
        print(f"WARNING: Langfuse credentials not available ({e}), skipping.", file=sys.stderr)
        report = generate_report(result_data, trace=None, trace_id=None, host=None, use_llm=False)
        _write_output(report, args.output)
        return 0

    trace, trace_id = None, None

    direct_trace_id = (result_data.get("details") or {}).get("trace_id", "")
    if direct_trace_id:
        print(f"Using direct trace ID: {direct_trace_id}", file=sys.stderr)
        trace_id = direct_trace_id
        try:
            trace = fetch_trace(trace_id, use_cache=False)
        except Exception as e:
            print(f"WARNING: Failed to fetch direct trace {trace_id}: {e}", file=sys.stderr)
    else:
        try:
            ts_str = result_data.get("timestamp", "")
            benchmark = result_data.get("benchmark", "")
            instance_id = result_data.get("instance_id", "")
            timestamp = parse_benchmark_timestamp(ts_str)
            matched = find_matching_trace(
                benchmark, instance_id, timestamp,
                result_data.get("duration_seconds", 0),
                verbose=args.verbose,
            )
            if matched:
                trace_id = matched.get("id")
                if args.verbose:
                    print(f"[verbose] Matched trace: {trace_id}", file=sys.stderr)
                trace = fetch_trace(trace_id, use_cache=False)
            else:
                print(f"WARNING: No matching trace for {benchmark}/{instance_id}", file=sys.stderr)
        except (ValueError, KeyError) as e:
            print(f"WARNING: Could not search for trace: {e}", file=sys.stderr)
        except Exception as e:
            print(f"WARNING: Trace fetch failed: {e}", file=sys.stderr)

    report = generate_report(
        result_data, trace=trace, trace_id=trace_id, host=host,
        use_llm=not args.no_llm, verbose=args.verbose,
        summary=args.summary,
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
