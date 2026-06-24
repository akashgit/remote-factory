#!/usr/bin/env python3
"""Analyze a Langfuse factory trace: per-agent time & output token breakdown,
pie charts, and swim-lane Gantt timeline.

Attributes each observation to an agent role by walking the Langfuse parent
chain (not by timestamp overlap), so overlapping agent spans are handled
correctly.

Output tokens are estimated as (assistant_message chars + thinking chars) / 4
because the current Langfuse setup does not record per-observation token counts.

Usage:
    python scripts/analyze_trace.py <trace_id> [-o OUTPUT_DIR]
    python scripts/analyze_trace.py <trace_id> --no-cache --title "My Project"

Requires .env.local with LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY.
Requires matplotlib for chart generation (pip install matplotlib).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from langfuse_client import (
    fetch_trace,
    find_ancestor_agent,
    get_agent_spans,
    parse_ts,
)

AGENT_COLORS = {
    "ceo": "#2196F3",
    "builder": "#4CAF50",
    "qa": "#FF9800",
    "researcher": "#9C27B0",
    "strategist": "#F44336",
    "archivist": "#607D8B",
    "refiner": "#00BCD4",
    "failure_analyst": "#795548",
}


def analyze(trace: dict) -> tuple[list[dict], list[dict]]:
    """Extract per-agent token and timing breakdown from a trace.

    Returns (results, timeline) where:
      - results: per-role aggregates (output_chars, thinking_chars, est_output_tokens, duration_s)
      - timeline: per-span entries for the Gantt chart
    """
    observations = trace["observations"]
    obs_by_id = {o["id"]: o for o in observations}

    # Attribute each observation to an agent role via parent chain
    role_output = defaultdict(int)
    role_thinking = defaultdict(int)
    role_tool_output = defaultdict(int)

    for o in observations:
        ancestor = find_ancestor_agent(o["id"], obs_by_id)
        role = ancestor["name"].replace("agent:", "") if ancestor else "ceo"

        if o["type"] == "EVENT":
            out = o.get("output", "") or ""
            if isinstance(out, dict):
                out = json.dumps(out)
            chars = len(str(out))
            if o["name"] == "thinking":
                role_thinking[role] += chars
            elif o["name"] == "assistant_message":
                role_output[role] += chars
        elif o["type"] == "TOOL":
            out = o.get("output", "") or ""
            if isinstance(out, dict):
                out = json.dumps(out)
            role_tool_output[role] += len(str(out))

    # Agent spans for timing
    agent_spans = get_agent_spans(observations)

    role_duration = defaultdict(float)
    role_count = defaultdict(int)
    for s in agent_spans:
        role = s["name"].replace("agent:", "")
        start = parse_ts(s.get("startTime"))
        end = parse_ts(s.get("endTime"))
        if start and end:
            role_duration[role] += (end - start).total_seconds()
        role_count[role] += 1

    # CEO duration = active cycle time minus agent time
    if agent_spans:
        cycle_start = parse_ts(agent_spans[0].get("startTime"))
        archivists = [s for s in agent_spans if "archivist" in s["name"]]
        if archivists:
            cycle_end = max(parse_ts(s.get("endTime")) for s in archivists if s.get("endTime"))
        else:
            cycle_end = parse_ts(agent_spans[-1].get("endTime"))
        total_cycle = (cycle_end - cycle_start).total_seconds() if cycle_start and cycle_end else 0
        total_agent = sum(role_duration.values())
        role_duration["ceo"] = max(0, total_cycle - total_agent)
        role_count["ceo"] = 1

    # Build results
    all_roles = sorted(set(
        list(role_output.keys()) + list(role_thinking.keys()) + list(role_duration.keys())
    ))
    results = []
    for role in all_roles:
        out_c = role_output.get(role, 0)
        think_c = role_thinking.get(role, 0)
        tool_c = role_tool_output.get(role, 0)
        est_tok = (out_c + think_c) // 4
        results.append({
            "role": role,
            "count": role_count.get(role, 0),
            "output_chars": out_c,
            "thinking_chars": think_c,
            "tool_output_chars": tool_c,
            "est_output_tokens": est_tok,
            "duration_s": round(role_duration.get(role, 0), 1),
        })
    results.sort(key=lambda r: -r["est_output_tokens"])

    # Timeline for Gantt chart
    timeline = []
    for i, s in enumerate(agent_spans):
        role = s["name"].replace("agent:", "")
        start = parse_ts(s.get("startTime"))
        end = parse_ts(s.get("endTime"))
        dur = (end - start).total_seconds() if start and end else 0
        timeline.append({
            "seq": i + 1,
            "role": role,
            "start": s.get("startTime", "")[:19],
            "end": s.get("endTime", "")[:19],
            "duration_s": round(dur, 1),
        })

    return results, timeline


def make_pie_charts(results: list[dict], output_dir: str, title: str = "") -> Path:
    """Generate side-by-side pie charts for token and time distribution."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    roles = [r["role"] for r in results]
    tokens = [r["est_output_tokens"] for r in results]
    durations = [r["duration_s"] for r in results]
    counts = [r["count"] for r in results]

    color_list = [AGENT_COLORS.get(r, "#999999") for r in roles]
    labels_tok = [f"{r} (x{c})\n~{t:,} tok" for r, t, c in zip(roles, tokens, counts)]
    labels_dur = [f"{r} (x{c})\n{d:.0f}s" for r, d, c in zip(roles, durations, counts)]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))

    ax1.pie(
        tokens, labels=labels_tok, colors=color_list,
        autopct="%1.1f%%", pctdistance=0.75, startangle=90,
        textprops={"fontsize": 9},
    )
    ax1.set_title("Estimated Output Tokens\nby Agent Role", fontsize=13, fontweight="bold", pad=15)

    ax2.pie(
        durations, labels=labels_dur, colors=color_list,
        autopct="%1.1f%%", pctdistance=0.75, startangle=90,
        textprops={"fontsize": 9},
    )
    ax2.set_title("Wall-Clock Time\nby Agent Role", fontsize=13, fontweight="bold", pad=15)

    total_tok = sum(tokens)
    total_dur = sum(durations)
    trace_title = title or "Factory Trace"
    fig.suptitle(
        f"{trace_title}\n~{total_tok:,} est. output tokens, {total_dur/60:.0f}min active",
        fontsize=14, fontweight="bold", y=1.02,
    )

    plt.tight_layout(rect=[0, 0, 1, 0.94])
    out_path = Path(output_dir) / "trace_breakdown.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved pie chart to {out_path}", file=sys.stderr)
    return out_path


def make_gantt_chart(timeline: list[dict], output_dir: str, title: str = "") -> Path | None:
    """Generate a swim-lane Gantt chart with one row per agent role."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    if not timeline:
        return None

    base_time = parse_ts(timeline[0]["start"])
    if not base_time:
        return None

    # Determine swim lanes from the roles present
    role_order = ["researcher", "strategist", "builder", "qa", "archivist",
                  "refiner", "failure_analyst"]
    present_roles = []
    for r in role_order:
        if any(e["role"] == r for e in timeline):
            present_roles.append(r)
    y_positions = {role: i for i, role in enumerate(reversed(present_roles))}

    fig, ax = plt.subplots(figsize=(20, max(3, len(present_roles) * 1.2 + 1)))

    for entry in timeline:
        start = parse_ts(entry["start"])
        end = parse_ts(entry["end"])
        if not start or not end:
            continue
        x_start = (start - base_time).total_seconds() / 60
        width = (end - start).total_seconds() / 60
        role = entry["role"]
        y = y_positions.get(role, -1)
        if y < 0:
            continue
        color = AGENT_COLORS.get(role, "#999999")
        ax.barh(y, width, left=x_start, height=0.7, color=color,
                edgecolor="white", linewidth=1, alpha=0.85)
        if width > 0.6:
            ax.text(x_start + width / 2, y, f"{entry['duration_s']:.0f}s",
                    ha="center", va="center", fontsize=7, fontweight="bold", color="white")

    ax.set_xlabel("Minutes from cycle start", fontsize=11)
    ax.set_yticks(list(y_positions.values()))
    ax.set_yticklabels([r.capitalize() for r in reversed(present_roles)], fontsize=10)

    trace_title = title or "Agent Execution Timeline"
    ax.set_title(trace_title, fontsize=13, fontweight="bold")

    max_x = max(
        (parse_ts(t["end"]) - base_time).total_seconds() / 60
        for t in timeline if parse_ts(t["end"])
    )
    ax.set_xlim(-0.5, max_x + 0.5)
    ax.grid(axis="x", alpha=0.3)
    ax.invert_yaxis()

    legend_patches = [
        mpatches.Patch(color=AGENT_COLORS.get(r, "#999"), label=r.capitalize())
        for r in present_roles
    ]
    ax.legend(handles=legend_patches, loc="upper right", fontsize=9,
              ncol=min(len(present_roles), 6))

    plt.tight_layout()
    out_path = Path(output_dir) / "trace_timeline.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved Gantt chart to {out_path}", file=sys.stderr)
    return out_path


def print_table(results: list[dict]):
    """Print a formatted table of per-agent token/time breakdown."""
    header = "Role            #  AssistOut   Thinking   ~OutTok   Duration   ToolOut"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['role']:<15} {r['count']:>2} {r['output_chars']:>10,} "
            f"{r['thinking_chars']:>10,} {r['est_output_tokens']:>9,} "
            f"{r['duration_s']:>9.0f}s {r['tool_output_chars']:>9,}"
        )
    total_tok = sum(r["est_output_tokens"] for r in results)
    total_dur = sum(r["duration_s"] for r in results)
    print("-" * len(header))
    print(f"{'TOTAL':<15}    {'':>10} {'':>10} {total_tok:>9,} {total_dur:>9.0f}s")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze a Langfuse factory trace: token/time breakdown + charts"
    )
    parser.add_argument("trace_id", help="Langfuse trace ID")
    parser.add_argument("--output-dir", "-o", default=".", help="Directory for charts and JSON")
    parser.add_argument("--title", "-t", default="", help="Title for charts (default: trace name)")
    parser.add_argument("--no-cache", action="store_true", help="Force fresh fetch from Langfuse")
    parser.add_argument("--no-charts", action="store_true", help="Skip chart generation")
    args = parser.parse_args()

    trace = fetch_trace(args.trace_id, use_cache=not args.no_cache)
    title = args.title or trace.get("name", "Factory Trace")

    results, timeline = analyze(trace)
    print_table(results)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "trace_breakdown.json", "w") as f:
        json.dump({"results": results, "timeline": timeline}, f, indent=2)
    print(f"\nSaved JSON to {out_dir / 'trace_breakdown.json'}", file=sys.stderr)

    if not args.no_charts:
        try:
            make_pie_charts(results, str(out_dir), title=title)
            make_gantt_chart(timeline, str(out_dir), title=f"{title} — Timeline")
        except ImportError:
            print("matplotlib not installed — skipping charts (pip install matplotlib)", file=sys.stderr)


if __name__ == "__main__":
    main()
