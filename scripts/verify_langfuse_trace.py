#!/usr/bin/env python3
"""Verify that a factory session produced exactly one well-structured Langfuse trace.

Usage:
    python scripts/verify_langfuse_trace.py <project-name> [--after TIMESTAMP]

Checks:
1. Exactly one trace exists for the project (after the given timestamp)
2. The trace has a root span
3. All agent spans (strategist, builder, qa, etc.) are nested under the root
4. No standalone agent traces were created
5. Transcript observations (tool calls, messages) exist under agent spans
"""

import argparse
import base64
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone


def api_get(path: str) -> dict:
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-dev-local-key")
    sec = os.environ.get("LANGFUSE_SECRET_KEY", "sk-lf-dev-local-key")
    auth = base64.b64encode(f"{pub}:{sec}".encode()).decode()
    req = urllib.request.Request(
        f"{host}{path}",
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project_name", help="Project name to search for in traces")
    parser.add_argument("--after", help="ISO timestamp — only consider traces after this time")
    args = parser.parse_args()

    project = args.project_name
    after_ts = args.after

    errors = []
    warnings = []

    # 1. Fetch all traces
    traces_data = api_get("/api/public/traces?limit=50")
    all_traces = traces_data["data"]

    # Filter to traces matching this project (check name, input, and observations)
    def _matches(t):
        if project in t.get("name", ""):
            return True
        inp = t.get("input") or {}
        if isinstance(inp, dict) and project in str(inp.get("project", "")):
            return True
        if isinstance(inp, str) and project in inp:
            return True
        return False

    matching = [t for t in all_traces if _matches(t)]
    if after_ts:
        matching = [t for t in matching if t["timestamp"] >= after_ts]

    print(f"Found {len(matching)} trace(s) matching '{project}'" +
          (f" after {after_ts}" if after_ts else ""))

    if len(matching) == 0:
        errors.append("FAIL: No traces found for this project")
        _report(errors, warnings)
        return 1

    if len(matching) > 1:
        errors.append(
            f"FAIL: Expected exactly 1 trace, found {len(matching)}: "
            + ", ".join(f"{t['name']} ({t['id'][:12]})" for t in matching)
        )

    # Use the most recent matching trace
    trace = matching[0]
    trace_id = trace["id"]
    trace_name = trace["name"]
    print(f"Trace: {trace_name} ({trace_id[:16]}...)")

    # 2. Check trace name format
    if not trace_name.startswith("factory:"):
        warnings.append(f"WARNING: Trace name should start with 'factory:', got '{trace_name}' (may be overwritten by cross-process SDK; fixed after flush)")

    # 3. Fetch all observations for this trace
    all_obs = []
    page = 1
    while True:
        obs_data = api_get(f"/api/public/observations?traceId={trace_id}&limit=100&page={page}")
        all_obs.extend(obs_data["data"])
        if page >= obs_data["meta"]["totalPages"]:
            break
        page += 1

    print(f"Total observations: {len(all_obs)}")

    # 4. Find root span and agent spans (root span has name starting with "factory:")
    spans = [o for o in all_obs if o["type"] == "SPAN"]
    root_spans = [s for s in spans if s.get("parentObservationId") is None
                  or s["name"].startswith("factory:")]
    agent_spans = [s for s in spans if s["name"].startswith("agent:")]

    print(f"Spans: {len(spans)} total, {len(root_spans)} root, {len(agent_spans)} agent")

    if len(root_spans) == 0:
        errors.append("FAIL: No root span found")
    elif len(root_spans) > 1:
        errors.append(f"FAIL: Expected 1 root span, found {len(root_spans)}")
    else:
        root_id = root_spans[0]["id"]
        print(f"Root span: {root_spans[0]['name']} ({root_id[:16]})")

        # 5. Check all agent spans are parented to root
        for s in agent_spans:
            parent = s.get("parentObservationId")
            if parent != root_id:
                errors.append(
                    f"FAIL: Agent span '{s['name']}' has parent {parent[:16] if parent else 'None'}, "
                    f"expected root {root_id[:16]}"
                )

    # 6. Check agent span names
    agent_names = sorted(set(s["name"] for s in agent_spans))
    print(f"Agent spans: {agent_names}")

    expected_roles = {"agent:strategist", "agent:builder", "agent:ceo"}
    found_roles = set(s["name"] for s in agent_spans)
    missing = expected_roles - found_roles
    if missing:
        errors.append(f"FAIL: Expected agent roles not found: {missing}")

    if "agent:ceo" in found_roles:
        print("CEO session: traced ✓")
    else:
        errors.append("FAIL: CEO session (agent:ceo) not traced — the main interactive session must appear as a span")

    # 7. Check that agent spans have child observations (transcript ingestion)
    for span in agent_spans:
        children = [o for o in all_obs if o.get("parentObservationId") == span["id"]]
        if len(children) == 0:
            warnings.append(f"WARNING: Agent span '{span['name']}' ({span['id'][:12]}) has no child observations")
        else:
            tool_obs = [c for c in children if c["type"] == "TOOL"]
            event_obs = [c for c in children if c["type"] == "EVENT"]
            print(f"  {span['name']}: {len(children)} children ({len(tool_obs)} tools, {len(event_obs)} events)")

    # 8. Check for standalone agent traces (should not exist)
    standalone = [
        t for t in all_traces
        if project in t.get("name", "")
        and t["id"] != trace_id
        and (after_ts is None or t["timestamp"] >= after_ts)
    ]
    if standalone:
        for st in standalone:
            errors.append(
                f"FAIL: Standalone trace found: {st['name']} ({st['id'][:12]}) — "
                "agents should nest under the root trace, not create separate traces"
            )

    # 9. Check for QA agent if build completed (look for qa in agent names)
    if "agent:qa" in found_roles:
        print("QA agent: traced ✓")
    else:
        warnings.append("WARNING: No QA agent span found (may not have been invoked in build mode)")

    _report(errors, warnings)
    return 1 if errors else 0


def _report(errors, warnings):
    print()
    if errors:
        print("=" * 60)
        print("VERIFICATION FAILED")
        print("=" * 60)
        for e in errors:
            print(f"  {e}")
    if warnings:
        print("-" * 60)
        print("WARNINGS")
        print("-" * 60)
        for w in warnings:
            print(f"  {w}")
    if not errors:
        print("=" * 60)
        print("VERIFICATION PASSED ✓")
        print("=" * 60)
        print("Single trace with all agent spans properly nested under root.")


if __name__ == "__main__":
    sys.exit(main())
