#!/usr/bin/env python3
"""Parse ./gradlew compileJava --continue output into structured JSON."""
from __future__ import annotations

import json
import re
import sys


def parse_compile(log_path: str) -> dict:
    with open(log_path) as f:
        content = f.read()

    passed = 0
    failed = 0
    failed_modules: list[str] = []

    task_pattern = re.compile(
        r">\s+Task\s+:(\S+):compileJava\s+(.*)"
    )

    for line in content.splitlines():
        m = task_pattern.search(line)
        if not m:
            continue
        module = m.group(1)
        status = m.group(2).strip()
        if "FAILED" in status:
            failed += 1
            failed_modules.append(module)
        else:
            passed += 1

    return {
        "passed": passed,
        "failed": failed,
        "total": passed + failed,
        "failed_modules": failed_modules,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: parse_compile.py <log_file>", file=sys.stderr)
        sys.exit(1)
    result = parse_compile(sys.argv[1])
    json.dump(result, sys.stdout, indent=2)
    print()
