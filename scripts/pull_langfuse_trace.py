#!/usr/bin/env python3
"""Pull a Langfuse trace and extract the factory orchestration timeline.

Usage:
    python scripts/pull_langfuse_trace.py <trace_id> [--output FILE] [--full]

Requires .env.local with LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv


def load_creds() -> tuple[str, str, str]:
    for p in [".env.local", ".env"]:
        if Path(p).exists():
            load_dotenv(p, override=True)
    host = os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")
    pk = os.environ["LANGFUSE_PUBLIC_KEY"]
    sk = os.environ["LANGFUSE_SECRET_KEY"]
    return host.rstrip("/"), pk, sk


def fetch_trace(host: str, pk: str, sk: str, trace_id: str) -> dict:
    url = f"{host}/api/public/traces/{trace_id}"
    r = requests.get(url, auth=(pk, sk), timeout=30)
    r.raise_for_status()
    return r.json()


def parse_timestamp(ts: str | None) -> datetime | None:
    if not ts:
        return None
    for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]:
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return None


def truncate(text: str | None, limit: int = 500) -> str:
    if not text:
        return "(empty)"
    s = str(text).replace("\n", " ").strip()
    if len(s) > limit:
        return s[:limit] + "..."
    return s


def extract_orchestration(trace: dict, full: bool = False) -> list[dict]:
    """Extract the high-level orchestration timeline from a trace."""
    observations = trace.get("observations", [])

    obs_by_id: dict[str, dict] = {}
    for obs in observations:
        obs_by_id[obs["id"]] = obs

    spans = sorted(
        [o for o in observations if o["type"] == "SPAN"],
        key=lambda o: o.get("startTime", ""),
    )

    ceo_span_id = None
    for s in spans:
        if s["name"] == "agent:ceo":
            ceo_span_id = s["id"]
            break

    timeline = []

    # Trace-level info
    timeline.append({
        "type": "trace",
        "name": trace.get("name", "unknown"),
        "timestamp": trace.get("timestamp", ""),
        "latency_s": trace.get("latency", 0),
        "total_cost": trace.get("totalCost", 0),
        "total_observations": len(observations),
    })

    # Agent spans (direct children of CEO)
    agent_spans = sorted(
        [s for s in spans if s.get("parentObservationId") == ceo_span_id and s["name"] != "agent:ceo"],
        key=lambda s: s.get("startTime", ""),
    )

    for span in agent_spans:
        start = parse_timestamp(span.get("startTime"))
        end = parse_timestamp(span.get("endTime"))
        duration = (end - start).total_seconds() if start and end else None

        # Find child observations for this agent span
        children = [o for o in observations if o.get("parentObservationId") == span["id"]]
        tool_calls = [c for c in children if c["type"] == "TOOL"]
        messages = [c for c in children if c["type"] == "EVENT"]

        input_text = ""
        output_text = ""
        if span.get("input"):
            inp = span["input"]
            if isinstance(inp, dict):
                input_text = inp.get("task", inp.get("prompt", json.dumps(inp)[:2000]))
            else:
                input_text = str(inp)

        if span.get("output"):
            out = span["output"]
            if isinstance(out, dict):
                output_text = json.dumps(out)
            else:
                output_text = str(out)

        limit = 3000 if full else 500
        entry = {
            "type": "agent",
            "name": span["name"],
            "start": span.get("startTime", "")[:19],
            "end": span.get("endTime", "")[:19] if span.get("endTime") else "running",
            "duration_s": round(duration, 1) if duration else None,
            "tool_calls": len(tool_calls),
            "events": len(messages),
            "input_summary": truncate(input_text, limit),
            "output_summary": truncate(output_text, limit),
        }
        timeline.append(entry)

    # CEO's own assistant_message events (between agent spans) — these show the CEO's reasoning
    ceo_messages = sorted(
        [o for o in observations
         if o["type"] == "EVENT"
         and o.get("name") == "assistant_message"
         and o.get("parentObservationId") == ceo_span_id],
        key=lambda o: o.get("startTime", ""),
    )

    ceo_reasoning = []
    for msg in ceo_messages:
        body = msg.get("output", msg.get("input", ""))
        if isinstance(body, dict):
            body = body.get("content", body.get("text", json.dumps(body)))
        text = str(body).strip()
        if len(text) < 10:
            continue
        ceo_reasoning.append({
            "timestamp": msg.get("startTime", "")[:19],
            "text": truncate(text, 1000 if full else 300),
        })

    return timeline, ceo_reasoning


def print_report(timeline: list[dict], ceo_reasoning: list[dict], file=sys.stdout):
    """Print a human-readable orchestration report."""
    p = lambda *a, **kw: print(*a, **kw, file=file)

    trace_info = timeline[0]
    p("=" * 80)
    p(f"FACTORY TRACE: {trace_info['name']}")
    p(f"  Started:      {trace_info['timestamp'][:19]}")
    p(f"  Duration:     {trace_info['latency_s']:.0f}s ({trace_info['latency_s']/60:.1f}m)")
    p(f"  Observations: {trace_info['total_observations']}")
    p(f"  Total cost:   ${trace_info['total_cost']:.4f}")
    p("=" * 80)

    p("\n## AGENT ORCHESTRATION TIMELINE\n")
    for i, entry in enumerate(timeline[1:], 1):
        if entry["type"] != "agent":
            continue
        p(f"### [{i}] {entry['name']}")
        p(f"    Time:  {entry['start']} → {entry['end']} ({entry['duration_s']}s)")
        p(f"    Tools: {entry['tool_calls']} calls, {entry['events']} events")
        p(f"    Input:  {entry['input_summary'][:200]}")
        p(f"    Output: {entry['output_summary'][:200]}")
        p()

    p("\n## CEO REASONING (assistant messages between agent calls)\n")
    for msg in ceo_reasoning[:50]:
        p(f"[{msg['timestamp']}] {msg['text']}")
        p()


def main():
    parser = argparse.ArgumentParser(description="Pull Langfuse trace and extract factory orchestration")
    parser.add_argument("trace_id", help="Langfuse trace ID")
    parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    parser.add_argument("--full", action="store_true", help="Include full input/output text (not truncated)")
    parser.add_argument("--json", action="store_true", help="Output as JSON instead of human-readable")
    args = parser.parse_args()

    host, pk, sk = load_creds()
    trace = fetch_trace(host, pk, sk, args.trace_id)

    timeline, ceo_reasoning = extract_orchestration(trace, full=args.full)

    out_file = open(args.output, "w") if args.output else sys.stdout
    try:
        if args.json:
            json.dump({"timeline": timeline, "ceo_reasoning": ceo_reasoning}, out_file, indent=2)
        else:
            print_report(timeline, ceo_reasoning, file=out_file)
    finally:
        if args.output:
            out_file.close()
            print(f"Written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
