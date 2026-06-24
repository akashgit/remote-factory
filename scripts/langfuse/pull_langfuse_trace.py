#!/usr/bin/env python3
"""Pull a Langfuse trace and extract the factory orchestration timeline.

Produces three views:
  1. Agent orchestration timeline (spans, durations, child counts)
  2. CEO reasoning log (assistant messages from the CEO agent)
  3. Factory CLI commands (factory agent/begin/finalize/precheck/review calls)

Usage:
    python scripts/pull_langfuse_trace.py <trace_id> [--output FILE] [--full] [--json]
    python scripts/pull_langfuse_trace.py <trace_id> --no-cache   # force fresh fetch

Requires .env.local with LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY.
"""
from __future__ import annotations

import argparse
import json
import sys

from langfuse_client import (
    fetch_trace,
    find_ancestor_agent,
    get_agent_spans,
    parse_ts,
    truncate,
)


def extract_orchestration(trace: dict, full: bool = False) -> tuple[list[dict], list[dict]]:
    """Extract the high-level orchestration timeline from a trace.

    Returns (timeline, ceo_reasoning) where:
      - timeline[0] is trace-level metadata
      - timeline[1:] are agent span entries
      - ceo_reasoning is a list of CEO assistant messages with timestamps
    """
    observations = trace.get("observations", [])
    obs_by_id = {o["id"]: o for o in observations}

    agent_spans = get_agent_spans(observations)

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

    # Agent spans with child observation counts
    for span in agent_spans:
        start = parse_ts(span.get("startTime"))
        end = parse_ts(span.get("endTime"))
        duration = (end - start).total_seconds() if start and end else None

        children = [o for o in observations if o.get("parentObservationId") == span["id"]]
        tool_calls = [c for c in children if c["type"] == "TOOL"]
        events = [c for c in children if c["type"] == "EVENT"]

        input_text = ""
        if span.get("input"):
            inp = span["input"]
            input_text = inp.get("task", inp.get("prompt", json.dumps(inp)[:2000])) if isinstance(inp, dict) else str(inp)

        output_text = ""
        if span.get("output"):
            out = span["output"]
            output_text = json.dumps(out) if isinstance(out, dict) else str(out)

        limit = 3000 if full else 500
        timeline.append({
            "type": "agent",
            "name": span["name"],
            "start": span.get("startTime", "")[:19],
            "end": span.get("endTime", "")[:19] if span.get("endTime") else "running",
            "duration_s": round(duration, 1) if duration else None,
            "tool_calls": len(tool_calls),
            "events": len(events),
            "input_summary": truncate(input_text, limit),
            "output_summary": truncate(output_text, limit),
        })

    # CEO reasoning: assistant_message events that belong to the CEO
    # (i.e., their ancestor agent is None — they're parented to the CEO span)
    ceo_messages = sorted(
        [
            o for o in observations
            if o["type"] == "EVENT"
            and o.get("name") == "assistant_message"
            and find_ancestor_agent(o["id"], obs_by_id) is None
        ],
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


def extract_factory_commands(trace: dict) -> list[dict]:
    """Extract factory CLI commands (factory agent/begin/finalize/etc.) from CEO tool calls.

    Returns a list of {timestamp, command, output_preview} dicts, sorted by time.
    These show the CEO's actual orchestration actions.
    """
    observations = trace.get("observations", [])
    obs_by_id = {o["id"]: o for o in observations}

    FACTORY_KEYWORDS = [
        "factory agent", "factory begin", "factory finalize",
        "factory eval", "factory precheck", "factory review",
        "factory log", "factory study", "factory guard",
        "factory backlog", "factory init", "factory detect",
    ]

    commands = []
    for o in sorted(observations, key=lambda x: x.get("startTime", "")):
        if o["type"] != "TOOL" or o.get("name") != "tool:Bash":
            continue
        # Only include CEO-level commands (no agent ancestor)
        if find_ancestor_agent(o["id"], obs_by_id) is not None:
            continue

        inp = o.get("input", {})
        cmd = inp.get("command", str(inp)) if isinstance(inp, dict) else str(inp)

        if not any(kw in cmd for kw in FACTORY_KEYWORDS):
            continue

        output = o.get("output", "") or ""
        if isinstance(output, dict):
            output = json.dumps(output)

        commands.append({
            "timestamp": o.get("startTime", "")[:19],
            "command": cmd[:800],
            "output_preview": truncate(str(output), 300),
        })

    return commands


def print_report(
    timeline: list[dict],
    ceo_reasoning: list[dict],
    factory_commands: list[dict],
    file=sys.stdout,
):
    """Print a human-readable orchestration report."""
    def p(*a, **kw):
        print(*a, **kw, file=file)

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
        p(f"    Time:  {entry['start']} -> {entry['end']} ({entry['duration_s']}s)")
        p(f"    Tools: {entry['tool_calls']} calls, {entry['events']} events")
        p(f"    Input:  {entry['input_summary'][:200]}")
        p(f"    Output: {entry['output_summary'][:200]}")
        p()

    p("\n## FACTORY CLI COMMANDS (CEO orchestration actions)\n")
    for cmd in factory_commands:
        p(f"[{cmd['timestamp']}]")
        p(f"  $ {cmd['command'][:400]}")
        p(f"  -> {cmd['output_preview'][:200]}")
        p()

    p("\n## CEO REASONING (assistant messages)\n")
    for msg in ceo_reasoning[:50]:
        p(f"[{msg['timestamp']}] {msg['text']}")
        p()


def main():
    parser = argparse.ArgumentParser(description="Pull Langfuse trace and extract factory orchestration")
    parser.add_argument("trace_id", help="Langfuse trace ID")
    parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    parser.add_argument("--full", action="store_true", help="Include full input/output text (not truncated)")
    parser.add_argument("--json", action="store_true", help="Output as JSON instead of human-readable")
    parser.add_argument("--no-cache", action="store_true", help="Force fresh fetch from Langfuse (skip cache)")
    args = parser.parse_args()

    trace = fetch_trace(args.trace_id, use_cache=not args.no_cache)
    timeline, ceo_reasoning = extract_orchestration(trace, full=args.full)
    factory_commands = extract_factory_commands(trace)

    out_file = open(args.output, "w") if args.output else sys.stdout
    try:
        if args.json:
            json.dump({
                "timeline": timeline,
                "ceo_reasoning": ceo_reasoning,
                "factory_commands": factory_commands,
            }, out_file, indent=2)
        else:
            print_report(timeline, ceo_reasoning, factory_commands, file=out_file)
    finally:
        if args.output:
            out_file.close()
            print(f"Written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
