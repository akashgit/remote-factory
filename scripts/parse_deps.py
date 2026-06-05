#!/usr/bin/env python3
"""Parse ./gradlew dependencies output into structured JSON."""
from __future__ import annotations

import json
import re
import sys


def parse_deps(log_path: str) -> dict:
    with open(log_path) as f:
        content = f.read()

    resolved = 0
    failed = 0
    failed_artifacts: list[str] = []

    dep_pattern = re.compile(
        r"[+\\|]---\s+(\S+:\S+:\S+)(.*)"
    )

    for line in content.splitlines():
        m = dep_pattern.search(line)
        if not m:
            continue
        artifact = m.group(1)
        rest = m.group(2)
        if "FAILED" in rest:
            failed += 1
            failed_artifacts.append(artifact)
        else:
            resolved += 1

    return {
        "resolved": resolved,
        "failed": failed,
        "total": resolved + failed,
        "failed_artifacts": failed_artifacts,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: parse_deps.py <log_file>", file=sys.stderr)
        sys.exit(1)
    result = parse_deps(sys.argv[1])
    json.dump(result, sys.stdout, indent=2)
    print()
