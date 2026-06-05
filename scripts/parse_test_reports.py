#!/usr/bin/env python3
"""Parse Gradle XML test reports or log output into structured JSON."""
from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET

INFRA_PATTERNS = re.compile(
    r"ConnectionRefused|ConnectException|NoClassDefFoundError.*Driver|"
    r"ClassNotFoundException.*jdbc|SocketException|UnknownHostException",
    re.IGNORECASE,
)
ENV_PATTERNS = re.compile(
    r"locale|timezone|TimeZone|encoding|charset|LC_ALL|LANG",
    re.IGNORECASE,
)
TIMEOUT_THRESHOLD_SECONDS = 60.0


def classify_failure(message: str, duration: float) -> str:
    if duration >= TIMEOUT_THRESHOLD_SECONDS:
        return "TEST_TIMEOUT"
    if INFRA_PATTERNS.search(message):
        return "TEST_INFRA"
    if ENV_PATTERNS.search(message):
        return "TEST_ENV"
    return "TEST_GENUINE"


def parse_xml_reports(report_dir: str) -> dict:
    passed = 0
    failed = 0
    skipped = 0
    errors = 0
    classifications: dict[str, int] = {}
    failures: list[dict] = []

    for root_dir, _dirs, files in os.walk(report_dir):
        for fname in files:
            if not fname.startswith("TEST-") or not fname.endswith(".xml"):
                continue
            fpath = os.path.join(root_dir, fname)
            try:
                tree = ET.parse(fpath)
            except ET.ParseError:
                continue
            suite = tree.getroot()
            for testcase in suite.findall("testcase"):
                name = testcase.get("name", "unknown")
                classname = testcase.get("classname", "unknown")
                duration = float(testcase.get("time", "0"))

                failure_el = testcase.find("failure")
                error_el = testcase.find("error")
                skipped_el = testcase.find("skipped")

                if skipped_el is not None:
                    skipped += 1
                elif failure_el is not None:
                    failed += 1
                    msg = failure_el.get("message", "") or failure_el.text or ""
                    category = classify_failure(msg, duration)
                    classifications[category] = classifications.get(category, 0) + 1
                    failures.append({
                        "test": f"{classname}.{name}",
                        "message": msg[:500],
                        "classification": category,
                        "duration": duration,
                    })
                elif error_el is not None:
                    errors += 1
                    msg = error_el.get("message", "") or error_el.text or ""
                    category = classify_failure(msg, duration)
                    classifications[category] = classifications.get(category, 0) + 1
                    failures.append({
                        "test": f"{classname}.{name}",
                        "message": msg[:500],
                        "classification": category,
                        "duration": duration,
                    })
                else:
                    passed += 1

    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
        "classifications": classifications,
        "failures": failures,
    }


def parse_log(log_path: str) -> dict:
    """Fallback: extract test counts from Gradle log output."""
    with open(log_path) as f:
        content = f.read()

    passed = 0
    failed = 0
    skipped = 0
    errors = 0
    failures: list[dict] = []
    classifications: dict[str, int] = {}

    summary_pattern = re.compile(
        r"(\d+)\s+tests?\s+completed,\s+(\d+)\s+failed"
    )
    skip_pattern = re.compile(r"(\d+)\s+tests?\s+skipped")

    for line in content.splitlines():
        m = summary_pattern.search(line)
        if m:
            total_in_line = int(m.group(1))
            failed_in_line = int(m.group(2))
            passed += total_in_line - failed_in_line
            failed += failed_in_line

        m2 = skip_pattern.search(line)
        if m2:
            skipped += int(m2.group(1))

    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
        "classifications": classifications,
        "failures": failures,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: parse_test_reports.py <log_or_report_dir>", file=sys.stderr)
        sys.exit(1)

    target = sys.argv[1]
    if os.path.isdir(target):
        result = parse_xml_reports(target)
    else:
        result = parse_log(target)

    json.dump(result, sys.stdout, indent=2)
    print()
