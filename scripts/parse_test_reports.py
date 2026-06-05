#!/usr/bin/env python3
"""CLI wrapper: parse JUnit XML test reports from a directory."""

import json
import sys
from pathlib import Path

from factory.build_root.gradle_parser import parse_tests


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: parse_test_reports.py <test-results-dir>", file=sys.stderr)
        sys.exit(1)

    results_dir = Path(sys.argv[1])
    if not results_dir.is_dir():
        print(f"ERROR: {results_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    combined = {"tests": 0, "passed": 0, "failed": 0, "skipped": 0, "failures": []}

    for xml_file in sorted(results_dir.rglob("*.xml")):
        report = parse_tests(xml_file.read_text())
        combined["tests"] += report["tests"]
        combined["passed"] += report["passed"]
        combined["failed"] += report["failed"]
        combined["skipped"] += report["skipped"]
        combined["failures"].extend(report["failures"])

    json.dump(combined, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
