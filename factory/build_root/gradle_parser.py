"""Parse Gradle build output — dependency resolution, compilation, and test reports."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any


def parse_deps(text: str) -> dict[str, Any]:
    """Parse `./gradlew dependencies` output.

    Returns {resolved, failed, conflicted, failures: [{group, artifact, version, error}]}.
    """
    resolved = 0
    failed = 0
    conflicted = 0
    failures: list[dict[str, str]] = []

    dep_line = re.compile(
        r"[|\\+\- ]+([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+):([^\s>]+)"
    )
    fail_line = re.compile(
        r"[|\\+\- ]+([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+):([^\s]+)\s+FAILED"
    )
    conflict_marker = re.compile(r"->")

    for line in text.splitlines():
        if fail_line.search(line):
            m = fail_line.search(line)
            assert m is not None
            failed += 1
            failures.append({
                "group": m.group(1),
                "artifact": m.group(2),
                "version": m.group(3),
                "error": line.strip(),
            })
        elif dep_line.search(line):
            resolved += 1
            if conflict_marker.search(line):
                conflicted += 1

    return {
        "resolved": resolved,
        "failed": failed,
        "conflicted": conflicted,
        "failures": failures,
    }


def parse_compile(text: str) -> dict[str, Any]:
    """Parse `./gradlew compileJava --continue` output.

    Returns {modules: [{name, status, errors: [...]}], passed, failed}.
    """
    modules: list[dict[str, Any]] = []
    passed = 0
    failed = 0

    task_result = re.compile(r"> Task :([a-zA-Z0-9_-]+):compileJava\s*(FAILED)?")
    error_line = re.compile(r"^(.*\.java):(\d+): error: (.+)$")

    current_module: str | None = None
    current_errors: list[str] = []

    for line in text.splitlines():
        tm = task_result.search(line)
        if tm:
            if current_module is not None:
                status = "fail" if current_errors else "pass"
                modules.append({
                    "name": current_module,
                    "status": status,
                    "errors": list(current_errors),
                })
                if status == "pass":
                    passed += 1
                else:
                    failed += 1
            current_module = tm.group(1)
            current_errors = []
            if tm.group(2) == "FAILED":
                current_errors.append(f"Task :{ current_module}:compileJava FAILED")
            continue

        em = error_line.match(line)
        if em and current_module:
            current_errors.append(line.strip())

    if current_module is not None:
        status = "fail" if current_errors else "pass"
        modules.append({
            "name": current_module,
            "status": status,
            "errors": list(current_errors),
        })
        if status == "pass":
            passed += 1
        else:
            failed += 1

    return {"modules": modules, "passed": passed, "failed": failed}


_INFRA_PATTERNS = [
    re.compile(r"java\.net\.ConnectException", re.IGNORECASE),
    re.compile(r"ConnectionRefused", re.IGNORECASE),
    re.compile(r"javax\.jms\.", re.IGNORECASE),
    re.compile(r"javax\.naming\.", re.IGNORECASE),
    re.compile(r"org\.springframework\.ldap", re.IGNORECASE),
    re.compile(r"database.*driver|jdbc.*driver", re.IGNORECASE),
]
_ENV_PATTERNS = [
    re.compile(r"expected:.*<.*locale|timezone.*>", re.IGNORECASE),
    re.compile(r"AssertionError.*\b(en_US|UTC|GMT)\b", re.IGNORECASE),
    re.compile(r"(Windows|/tmp/|C:\\\\)", re.IGNORECASE),
]
_TIMEOUT_THRESHOLD_S = 60.0


def _classify_failure(message: str, time_s: float | None = None) -> str:
    if time_s is not None and time_s >= _TIMEOUT_THRESHOLD_S:
        return "TEST_TIMEOUT"
    for p in _INFRA_PATTERNS:
        if p.search(message):
            return "TEST_INFRA"
    for p in _ENV_PATTERNS:
        if p.search(message):
            return "TEST_ENV"
    return "TEST_GENUINE"


def parse_tests(xml_text: str) -> dict[str, Any]:
    """Parse JUnit XML test report.

    Returns {tests, passed, failed, skipped,
             failures: [{class, method, type, message, classification}]}.
    """
    root = ET.fromstring(xml_text)

    total_tests = 0
    total_failures = 0
    total_skipped = 0
    failure_details: list[dict[str, str]] = []

    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if not suites and root.tag == "testsuites":
        suites = root.findall("testsuite")

    for suite in suites:
        tests_attr = int(suite.get("tests", "0"))
        failures_attr = int(suite.get("failures", "0"))
        errors_attr = int(suite.get("errors", "0"))
        skipped_attr = int(suite.get("skipped", "0"))

        total_tests += tests_attr
        total_failures += failures_attr + errors_attr
        total_skipped += skipped_attr

        for tc in suite.findall("testcase"):
            classname = tc.get("classname", "")
            methodname = tc.get("name", "")
            time_s = float(tc.get("time", "0"))

            for tag in ("failure", "error"):
                elem = tc.find(tag)
                if elem is not None:
                    msg = elem.get("message", "") or (elem.text or "")
                    ftype = elem.get("type", tag)
                    classification = _classify_failure(msg, time_s)
                    failure_details.append({
                        "class": classname,
                        "method": methodname,
                        "type": ftype,
                        "message": msg,
                        "classification": classification,
                    })

    total_passed = total_tests - total_failures - total_skipped

    return {
        "tests": total_tests,
        "passed": total_passed,
        "failed": total_failures,
        "skipped": total_skipped,
        "failures": failure_details,
    }
