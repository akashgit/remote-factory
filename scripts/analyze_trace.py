#!/usr/bin/env python3
"""Analyze a Langfuse factory trace: extract per-agent time & output token breakdown,
generate pie charts and gantt timeline, and produce a friction analysis.

Usage:
    python scripts/analyze_trace.py <trace_id> [--output-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv


def load_creds():
    for p in [".env.local", ".env"]:
        if Path(p).exists():
            load_dotenv(p, override=True)
    host = (os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")).rstrip("/")
    pk = os.environ["LANGFUSE_PUBLIC_KEY"]
    sk = os.environ["LANGFUSE_SECRET_KEY"]
    return host, pk, sk


def fetch_trace(host, pk, sk, trace_id):
    r = requests.get(f"{host}/api/public/traces/{trace_id}", auth=(pk, sk), timeout=30)
    r.raise_for_status()
    return r.json()


def parse_ts(ts):
    if not ts:
        return None
    for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]:
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            pass
    return None


def analyze(trace):
    observations = trace["observations"]
    obs_by_id = {o["id"]: o for o in observations}

    # Walk parent chain to find the nearest agent:* ancestor (excluding agent:ceo)
    def find_ancestor_agent(obs_id, depth=0):
        if depth > 20:
            return None
        obs = obs_by_id.get(obs_id)
        if not obs:
            return None
        if obs["type"] == "SPAN" and obs["name"].startswith("agent:") and obs["name"] != "agent:ceo":
            return obs
        pid = obs.get("parentObservationId")
        if pid:
            return find_ancestor_agent(pid, depth + 1)
        return None

    # Attribute each observation to an agent role via parent chain
    role_output = defaultdict(int)
    role_thinking = defaultdict(int)
    role_tool_output = defaultdict(int)

    for o in observations:
        ancestor = find_ancestor_agent(o["id"])
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
    agent_spans = sorted(
        [o for o in observations
         if o["type"] == "SPAN"
         and o["name"].startswith("agent:")
         and o["name"] != "agent:ceo"],
        key=lambda o: o.get("startTime", ""),
    )

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

    # Detailed timeline for gantt chart
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


def make_plots(results, output_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    roles = [r["role"] for r in results]
    tokens = [r["est_output_tokens"] for r in results]
    durations = [r["duration_s"] for r in results]
    counts = [r["count"] for r in results]

    colors = {
        "ceo": "#2196F3",
        "builder": "#4CAF50",
        "qa": "#FF9800",
        "researcher": "#9C27B0",
        "strategist": "#F44336",
        "archivist": "#607D8B",
    }
    color_list = [colors.get(r, "#999999") for r in roles]

    labels_tok = [f"{r} (x{c})\n~{t:,} tok" for r, t, c in zip(roles, tokens, counts)]
    labels_dur = [f"{r} (x{c})\n{d:.0f}s" for r, d, c in zip(roles, durations, counts)]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))

    wedges1, texts1, autotexts1 = ax1.pie(
        tokens, labels=labels_tok, colors=color_list,
        autopct="%1.1f%%", pctdistance=0.75, startangle=90,
        textprops={"fontsize": 9},
    )
    ax1.set_title("Estimated Output Tokens\nby Agent Role", fontsize=13, fontweight="bold", pad=15)

    wedges2, texts2, autotexts2 = ax2.pie(
        durations, labels=labels_dur, colors=color_list,
        autopct="%1.1f%%", pctdistance=0.75, startangle=90,
        textprops={"fontsize": 9},
    )
    ax2.set_title("Wall-Clock Time\nby Agent Role", fontsize=13, fontweight="bold", pad=15)

    total_tok = sum(tokens)
    total_dur = sum(durations)
    fig.suptitle(
        f"Factory Trace: snake-test-v3/design\n~{total_tok:,} est. output tokens, {total_dur/60:.0f}min active",
        fontsize=14, fontweight="bold", y=1.02,
    )

    plt.tight_layout(rect=[0, 0, 1, 0.94])
    out_path = Path(output_dir) / "trace_breakdown.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved chart to {out_path}", file=sys.stderr)
    return out_path


def make_timeline_chart(timeline, output_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    colors = {
        "ceo": "#2196F3",
        "builder": "#4CAF50",
        "qa": "#FF9800",
        "researcher": "#9C27B0",
        "strategist": "#F44336",
        "archivist": "#607D8B",
    }

    if not timeline:
        return None

    base_time = parse_ts(timeline[0]["start"])
    if not base_time:
        return None

    fig, ax = plt.subplots(figsize=(18, 4))

    for entry in timeline:
        start = parse_ts(entry["start"])
        end = parse_ts(entry["end"])
        if not start or not end:
            continue
        x_start = (start - base_time).total_seconds() / 60
        width = (end - start).total_seconds() / 60
        color = colors.get(entry["role"], "#999999")
        ax.barh(0.5, width, left=x_start, height=0.6, color=color, edgecolor="white", linewidth=0.5)
        if width > 0.3:
            label = entry["role"]
            if len(label) > 6:
                label = label[:6]
            ax.text(x_start + width / 2, 0.5, f"{label}\n{entry['duration_s']:.0f}s",
                    ha="center", va="center", fontsize=5.5, fontweight="bold", color="white")

    ax.set_xlabel("Minutes from cycle start", fontsize=11)
    ax.set_yticks([])
    ax.set_title("Agent Execution Timeline", fontsize=13, fontweight="bold")

    legend_patches = [mpatches.Patch(color=c, label=r) for r, c in colors.items()]
    ax.legend(handles=legend_patches, loc="upper right", fontsize=9, ncol=6)

    plt.tight_layout()
    out_path = Path(output_dir) / "trace_timeline.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved timeline to {out_path}", file=sys.stderr)
    return out_path


def print_table(results):
    header = "Role            #  AssistOut   Thinking   ~OutTok   Duration   ToolOut"
    print(header)
    print("-" * len(header))
    for r in results:
        print(f"{r['role']:<15} {r['count']:>2} {r['output_chars']:>10,} {r['thinking_chars']:>10,} "
              f"{r['est_output_tokens']:>9,} {r['duration_s']:>9.0f}s {r['tool_output_chars']:>9,}")
    total_tok = sum(r["est_output_tokens"] for r in results)
    total_dur = sum(r["duration_s"] for r in results)
    print("-" * len(header))
    print(f"{'TOTAL':<15}    {'':>10} {'':>10} {total_tok:>9,} {total_dur:>9.0f}s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace_id")
    parser.add_argument("--output-dir", "-o", default=".")
    args = parser.parse_args()

    host, pk, sk = load_creds()
    trace = fetch_trace(host, pk, sk, args.trace_id)

    results, timeline = analyze(trace)
    print_table(results)

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(args.output_dir) / "trace_breakdown.json", "w") as f:
        json.dump({"results": results, "timeline": timeline}, f, indent=2)

    try:
        pie_path = make_plots(results, args.output_dir)
        gantt_path = make_timeline_chart(timeline, args.output_dir)
    except ImportError:
        print("matplotlib not installed - skipping charts", file=sys.stderr)
        pie_path = gantt_path = None

    return results, timeline, pie_path, gantt_path


if __name__ == "__main__":
    main()
