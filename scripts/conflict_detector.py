#!/usr/bin/env python3
"""Detect merge conflicts between open PRs and main, track hotspot files.

Usage:
    python scripts/conflict_detector.py detect [--include-drafts] [--data-file conflicts.jsonl]
    python scripts/conflict_detector.py report [--days 30] [--top 10] [--data-file conflicts.jsonl] [--issue N]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def list_open_prs(include_drafts: bool = False) -> list[dict]:
    result = _run(["gh", "pr", "list", "--state", "open", "--json", "number,headRefName,isDraft", "--limit", "200"])
    if result.returncode != 0:
        print(f"Error listing PRs: {result.stderr.strip()}", file=sys.stderr)
        return []
    prs = json.loads(result.stdout)
    if not include_drafts:
        prs = [pr for pr in prs if not pr.get("isDraft", False)]
    return prs


def check_conflicts(branch: str) -> list[str]:
    result = _run(["git", "merge-tree", "--write-tree", "origin/main", f"origin/{branch}"])
    if result.returncode == 0:
        return []
    conflict_files = []
    for line in result.stdout.splitlines():
        m = re.match(r"CONFLICT \([^)]+\):\s+Merge conflict in (.+)", line)
        if m:
            conflict_files.append(m.group(1))
            continue
        m = re.match(r"CONFLICT \([^)]+\):\s+(.+) deleted in .+ and modified in", line)
        if m:
            conflict_files.append(m.group(1))
            continue
        m = re.match(r"CONFLICT \([^)]+\):\s+(.+) added in .+ and .+", line)
        if m:
            conflict_files.append(m.group(1))
    return conflict_files


def run_detect(args: argparse.Namespace) -> int:
    data_file = Path(args.data_file)
    prs = list_open_prs(include_drafts=args.include_drafts)
    if not prs:
        print("No open PRs found.")
        return 0

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conflicts_found = 0
    events: list[dict] = []

    for pr in prs:
        pr_num = pr["number"]
        branch = pr["headRefName"]
        conflict_files = check_conflicts(branch)
        if conflict_files:
            conflicts_found += 1
            event = {
                "timestamp": now,
                "pr_number": pr_num,
                "pr_branch": branch,
                "conflict_files": conflict_files,
                "total_open_prs": len(prs),
            }
            events.append(event)
            print(f"  PR #{pr_num} ({branch}): {len(conflict_files)} conflicting file(s) — {', '.join(conflict_files)}")

    if events:
        with open(data_file, "a") as f:
            for event in events:
                f.write(json.dumps(event, separators=(",", ":")) + "\n")

    print(f"\nChecked {len(prs)} PRs, {conflicts_found} have conflicts.")
    return 1 if conflicts_found > 0 else 0


def run_report(args: argparse.Namespace) -> int:
    data_file = Path(args.data_file)
    if not data_file.exists():
        print("No conflict data found. Run 'detect' first.")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    file_counter: Counter[str] = Counter()
    file_last_seen: dict[str, str] = {}
    file_prs: dict[str, set[int]] = {}

    with open(data_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            ts = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
            if ts < cutoff:
                continue
            for fp in event["conflict_files"]:
                file_counter[fp] += 1
                prev = file_last_seen.get(fp, "")
                if event["timestamp"] > prev:
                    file_last_seen[fp] = event["timestamp"]
                file_prs.setdefault(fp, set()).add(event["pr_number"])

    if not file_counter:
        print(f"No conflicts recorded in the last {args.days} days.")
        return 0

    top_files = file_counter.most_common(args.top)
    lines = [
        f"## Conflict Hotspots (last {args.days} days)\n",
        "| Rank | File | Conflicts | Last Seen | PRs Affected |",
        "|------|------|-----------|-----------|--------------|",
    ]
    for rank, (fp, count) in enumerate(top_files, 1):
        last = file_last_seen[fp][:10]
        pr_list = ", ".join(f"#{n}" for n in sorted(file_prs[fp]))
        lines.append(f"| {rank} | `{fp}` | {count} | {last} | {pr_list} |")

    report = "\n".join(lines)
    print(report)

    if args.issue:
        result = _run(["gh", "issue", "comment", str(args.issue), "--body", report])
        if result.returncode != 0:
            print(f"Error posting to issue: {result.stderr.strip()}", file=sys.stderr)
            return 1
        print(f"\nPosted report to issue #{args.issue}.")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect PR merge conflicts and track hotspot files.")
    sub = parser.add_subparsers(dest="command")

    detect_p = sub.add_parser("detect", help="Check open PRs for conflicts with main")
    detect_p.add_argument("--include-drafts", action="store_true", help="Include draft PRs")
    detect_p.add_argument("--data-file", default="conflicts.jsonl", help="Path to JSONL data file")

    report_p = sub.add_parser("report", help="Generate hotspot report from recorded conflicts")
    report_p.add_argument("--days", type=int, default=30, help="Look back N days (default: 30)")
    report_p.add_argument("--top", type=int, default=10, help="Show top N files (default: 10)")
    report_p.add_argument("--data-file", default="conflicts.jsonl", help="Path to JSONL data file")
    report_p.add_argument("--issue", type=int, default=None, help="Post report as comment on this issue number")

    parsed = parser.parse_args(argv)
    if parsed.command == "detect":
        return run_detect(parsed)
    elif parsed.command == "report":
        return run_report(parsed)
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
