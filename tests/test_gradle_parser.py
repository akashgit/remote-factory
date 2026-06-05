"""Tests for Gradle output parsing — dependencies, compilation, and test reports."""

from __future__ import annotations

from pathlib import Path

from factory.build_root.gradle_parser import parse_compile, parse_deps, parse_tests

FIXTURES = Path(__file__).parent / "fixtures" / "gradle_output"


class TestParseDeps:
    def test_success_output(self) -> None:
        text = (FIXTURES / "dep_resolution_success.txt").read_text()
        result = parse_deps(text)
        assert result["failed"] == 0
        assert result["resolved"] > 0
        assert isinstance(result["failures"], list)
        assert len(result["failures"]) == 0

    def test_401_failure_output(self) -> None:
        text = (FIXTURES / "dep_resolution_401.txt").read_text()
        result = parse_deps(text)
        assert result["failed"] == 2
        assert len(result["failures"]) == 2
        artifacts = {f["artifact"] for f in result["failures"]}
        assert "propdeps-plugin" in artifacts
        assert "uow" in artifacts

    def test_empty_input(self) -> None:
        result = parse_deps("")
        assert result == {"resolved": 0, "failed": 0, "conflicted": 0, "failures": []}

    def test_conflict_detection(self) -> None:
        text = "+--- org.slf4j:slf4j-api:1.7.30 -> 1.7.36"
        result = parse_deps(text)
        assert result["conflicted"] == 1
        assert result["resolved"] == 1

    def test_multimodule_counts(self) -> None:
        text = (FIXTURES / "dep_resolution_success.txt").read_text()
        result = parse_deps(text)
        assert result["resolved"] >= 10


class TestParseCompile:
    def test_success_output(self) -> None:
        text = (FIXTURES / "compile_success.txt").read_text()
        result = parse_compile(text)
        assert result["failed"] == 0
        assert result["passed"] == 10
        assert len(result["modules"]) == 10

    def test_cascade_failure(self) -> None:
        text = (FIXTURES / "compile_cascade_failure.txt").read_text()
        result = parse_compile(text)
        assert result["failed"] == 3
        failed_modules = [m["name"] for m in result["modules"] if m["status"] == "fail"]
        assert "spring-core" in failed_modules
        assert "spring-beans" in failed_modules
        assert "spring-context" in failed_modules

    def test_passing_modules_have_no_errors(self) -> None:
        text = (FIXTURES / "compile_cascade_failure.txt").read_text()
        result = parse_compile(text)
        for m in result["modules"]:
            if m["status"] == "pass":
                assert m["errors"] == []

    def test_failing_modules_have_errors(self) -> None:
        text = (FIXTURES / "compile_cascade_failure.txt").read_text()
        result = parse_compile(text)
        for m in result["modules"]:
            if m["status"] == "fail":
                assert len(m["errors"]) > 0

    def test_empty_input(self) -> None:
        result = parse_compile("")
        assert result == {"modules": [], "passed": 0, "failed": 0}


class TestParseTests:
    def test_mixed_report(self) -> None:
        xml = (FIXTURES / "test_report_mixed.xml").read_text()
        result = parse_tests(xml)
        assert result["tests"] == 6
        assert result["skipped"] == 1
        assert result["failed"] == 4
        assert result["passed"] == 1

    def test_infra_classification(self) -> None:
        xml = (FIXTURES / "test_report_mixed.xml").read_text()
        result = parse_tests(xml)
        infra = [f for f in result["failures"] if f["classification"] == "TEST_INFRA"]
        assert len(infra) >= 1
        assert any("JmsTemplateTests" in f["class"] for f in infra)

    def test_env_classification(self) -> None:
        xml = (FIXTURES / "test_report_mixed.xml").read_text()
        result = parse_tests(xml)
        env = [f for f in result["failures"] if f["classification"] == "TEST_ENV"]
        assert len(env) >= 1
        assert any("DateFormatTests" in f["class"] for f in env)

    def test_timeout_classification(self) -> None:
        xml = (FIXTURES / "test_report_mixed.xml").read_text()
        result = parse_tests(xml)
        timeout = [f for f in result["failures"] if f["classification"] == "TEST_TIMEOUT"]
        assert len(timeout) >= 1
        assert any("SlowTaskTests" in f["class"] for f in timeout)

    def test_genuine_classification(self) -> None:
        xml = (FIXTURES / "test_report_mixed.xml").read_text()
        result = parse_tests(xml)
        genuine = [f for f in result["failures"] if f["classification"] == "TEST_GENUINE"]
        assert len(genuine) >= 1
        assert any("BeanWrapperTests" in f["class"] for f in genuine)

    def test_minimal_passing_suite(self) -> None:
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="Simple" tests="2" failures="0" errors="0" skipped="0" time="0.1">
  <testcase classname="com.example.Test" name="testA" time="0.05"/>
  <testcase classname="com.example.Test" name="testB" time="0.05"/>
</testsuite>"""
        result = parse_tests(xml)
        assert result["tests"] == 2
        assert result["passed"] == 2
        assert result["failed"] == 0
        assert result["failures"] == []
