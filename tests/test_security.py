"""Tests for factory.security — pluggable scanner architecture.

Covers:
  - SecurityIssue and SecurityScanResult models
  - ScannerRegistry (registration, detection, scanning)
  - BanditScanner, NpmAuditScanner (with mocked subprocess)
  - SemgrepScanner, TrivyScanner, GitSecretsScanner (with mocked subprocess)
  - eval_security() integration via the registry
"""

import json
from pathlib import Path
from unittest.mock import patch

from factory.security import ScannerRegistry, get_default_registry
from factory.security.models import (
    SecurityIssue,
    SecurityScanResult,
    SecuritySeverity,
)
from factory.security.scanners import (
    BanditScanner,
    GitSecretsScanner,
    NpmAuditScanner,
    SemgrepScanner,
    TrivyScanner,
)


# ── Model tests ──────────────────────────────────────────────────


class TestSecuritySeverity:
    def test_ordering(self):
        """Severity enum values are strings."""
        assert SecuritySeverity.CRITICAL.value == "critical"
        assert SecuritySeverity.HIGH.value == "high"
        assert SecuritySeverity.INFO.value == "info"


class TestSecurityIssue:
    def test_minimal_issue(self):
        issue = SecurityIssue(
            severity=SecuritySeverity.HIGH,
            category="B101",
            scanner="bandit",
        )
        assert issue.severity == SecuritySeverity.HIGH
        assert issue.file == ""
        assert issue.line is None
        assert issue.remediation == ""

    def test_full_issue(self):
        issue = SecurityIssue(
            severity=SecuritySeverity.CRITICAL,
            category="dependency_vulnerability",
            file="package-lock.json",
            line=42,
            message="Known vulnerability in lodash",
            remediation="Upgrade to lodash>=4.17.21",
            scanner="npm-audit",
        )
        assert issue.file == "package-lock.json"
        assert issue.line == 42
        assert issue.scanner == "npm-audit"


class TestSecurityScanResult:
    def test_empty_result(self):
        result = SecurityScanResult(scanner_name="test")
        assert result.passed is True
        assert result.issue_count == 0
        assert result.issues == []

    def test_result_with_issues(self):
        issues = [
            SecurityIssue(severity=SecuritySeverity.HIGH, category="B101", scanner="bandit"),
            SecurityIssue(severity=SecuritySeverity.LOW, category="B105", scanner="bandit"),
        ]
        result = SecurityScanResult(
            scanner_name="bandit",
            issues=issues,
            passed=False,
            details="2 issues found",
        )
        assert result.issue_count == 2
        assert result.passed is False

    def test_issues_by_severity(self):
        issues = [
            SecurityIssue(severity=SecuritySeverity.HIGH, category="A", scanner="test"),
            SecurityIssue(severity=SecuritySeverity.LOW, category="B", scanner="test"),
            SecurityIssue(severity=SecuritySeverity.HIGH, category="C", scanner="test"),
        ]
        result = SecurityScanResult(scanner_name="test", issues=issues, passed=False)
        highs = result.issues_by_severity(SecuritySeverity.HIGH)
        assert len(highs) == 2
        lows = result.issues_by_severity(SecuritySeverity.LOW)
        assert len(lows) == 1
        crits = result.issues_by_severity(SecuritySeverity.CRITICAL)
        assert len(crits) == 0


# ── Registry tests ───────────────────────────────────────────────


class _StubScanner:
    """Minimal scanner for testing the registry."""

    def __init__(self, scanner_name: str, detects: bool = True, issue_count: int = 0):
        self._name = scanner_name
        self._detects = detects
        self._issue_count = issue_count

    @property
    def name(self) -> str:
        return self._name

    def detect(self, project_path: Path) -> bool:
        return self._detects

    def run(self, project_path: Path) -> SecurityScanResult:
        issues = [
            SecurityIssue(severity=SecuritySeverity.LOW, category="test", scanner=self._name)
            for _ in range(self._issue_count)
        ]
        return SecurityScanResult(
            scanner_name=self._name,
            issues=issues,
            passed=self._issue_count == 0,
            details="clean" if self._issue_count == 0 else f"{self._issue_count} issues",
        )


class TestScannerRegistry:
    def test_empty_registry(self, tmp_path):
        registry = ScannerRegistry()
        assert registry.scanners == []
        assert registry.detect(tmp_path) == []
        assert registry.scan(tmp_path) == []

    def test_register_and_detect(self, tmp_path):
        registry = ScannerRegistry()
        scanner_a = _StubScanner("a", detects=True)
        scanner_b = _StubScanner("b", detects=False)
        registry.register(scanner_a)
        registry.register(scanner_b)
        assert len(registry.scanners) == 2
        applicable = registry.detect(tmp_path)
        assert len(applicable) == 1
        assert applicable[0].name == "a"

    def test_scan_runs_applicable_only(self, tmp_path):
        registry = ScannerRegistry()
        registry.register(_StubScanner("detected", detects=True, issue_count=2))
        registry.register(_StubScanner("skipped", detects=False, issue_count=5))
        results = registry.scan(tmp_path)
        assert len(results) == 1
        assert results[0].scanner_name == "detected"
        assert results[0].issue_count == 2

    def test_scan_handles_exception(self, tmp_path):
        """A scanner that raises during run should not break the registry."""

        class CrashingScanner:
            @property
            def name(self) -> str:
                return "crasher"

            def detect(self, project_path: Path) -> bool:
                return True

            def run(self, project_path: Path) -> SecurityScanResult:
                raise RuntimeError("Scanner exploded")

        registry = ScannerRegistry()
        registry.register(CrashingScanner())
        results = registry.scan(tmp_path)
        assert len(results) == 1
        assert results[0].passed is False
        assert "unexpected error" in results[0].details

    def test_detect_handles_exception(self, tmp_path):
        """A scanner that raises during detect should be skipped."""

        class BadDetectScanner:
            @property
            def name(self) -> str:
                return "bad-detect"

            def detect(self, project_path: Path) -> bool:
                raise RuntimeError("Detect failed")

            def run(self, project_path: Path) -> SecurityScanResult:
                return SecurityScanResult(scanner_name="bad-detect")

        registry = ScannerRegistry()
        registry.register(BadDetectScanner())
        applicable = registry.detect(tmp_path)
        assert applicable == []


class TestDefaultRegistry:
    def test_default_registry_has_all_scanners(self):
        # Reset the global so we get a fresh one
        import factory.security
        factory.security._default_registry = None
        registry = get_default_registry()
        names = {s.name for s in registry.scanners}
        assert names == {"bandit", "npm-audit", "semgrep", "trivy", "git-secrets"}


# ── BanditScanner tests ─────────────────────────────────────────


class TestBanditScanner:
    def test_detect_python_project_with_bandit(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        scanner = BanditScanner()
        with patch("factory.security.scanners._tool_available", return_value=True):
            assert scanner.detect(tmp_path) is True

    def test_detect_non_python_project(self, tmp_path):
        scanner = BanditScanner()
        assert scanner.detect(tmp_path) is False

    def test_detect_bandit_not_installed(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        scanner = BanditScanner()
        with patch("factory.security.scanners._tool_available", return_value=False):
            assert scanner.detect(tmp_path) is False

    def test_run_clean(self, tmp_path):
        scanner = BanditScanner()
        bandit_output = json.dumps({"results": []})
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (0, bandit_output, "")
            result = scanner.run(tmp_path)
        assert result.passed is True
        assert result.issue_count == 0
        assert result.details == "clean"

    def test_run_with_issues(self, tmp_path):
        scanner = BanditScanner()
        bandit_output = json.dumps({
            "results": [
                {
                    "issue_severity": "HIGH",
                    "test_id": "B101",
                    "filename": "app.py",
                    "line_number": 10,
                    "issue_text": "Use of exec detected",
                    "more_info": "https://bandit.readthedocs.io/...",
                },
                {
                    "issue_severity": "MEDIUM",
                    "test_id": "B105",
                    "filename": "config.py",
                    "line_number": 5,
                    "issue_text": "Hardcoded password",
                },
            ],
        })
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (1, bandit_output, "")
            result = scanner.run(tmp_path)
        assert result.passed is False
        assert result.issue_count == 2
        assert result.issues[0].severity == SecuritySeverity.HIGH
        assert result.issues[0].category == "B101"
        assert result.issues[0].file == "app.py"
        assert result.issues[0].line == 10
        assert result.issues[1].severity == SecuritySeverity.MEDIUM

    def test_run_bandit_not_found(self, tmp_path):
        scanner = BanditScanner()
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (1, "", "Command not found: bandit")
            result = scanner.run(tmp_path)
        assert result.passed is True
        assert "not installed" in result.details

    def test_run_invalid_json(self, tmp_path):
        scanner = BanditScanner()
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (0, "not json", "")
            result = scanner.run(tmp_path)
        assert result.passed is True
        assert result.issue_count == 0


# ── NpmAuditScanner tests ───────────────────────────────────────


class TestNpmAuditScanner:
    def test_detect_node_project_with_lockfile(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        (tmp_path / "package-lock.json").write_text("{}\n")
        scanner = NpmAuditScanner()
        with patch("factory.security.scanners._tool_available", return_value=True):
            assert scanner.detect(tmp_path) is True

    def test_detect_no_lockfile(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        scanner = NpmAuditScanner()
        assert scanner.detect(tmp_path) is False

    def test_detect_not_node_project(self, tmp_path):
        scanner = NpmAuditScanner()
        assert scanner.detect(tmp_path) is False

    def test_run_clean(self, tmp_path):
        scanner = NpmAuditScanner()
        audit_output = json.dumps({
            "metadata": {
                "vulnerabilities": {"low": 0, "moderate": 0, "high": 0, "critical": 0},
            },
        })
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (0, audit_output, "")
            result = scanner.run(tmp_path)
        assert result.passed is True
        assert result.details == "clean"

    def test_run_with_vulnerabilities(self, tmp_path):
        scanner = NpmAuditScanner()
        audit_output = json.dumps({
            "metadata": {
                "vulnerabilities": {"low": 2, "moderate": 1, "high": 1, "critical": 0},
            },
        })
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (1, audit_output, "")
            result = scanner.run(tmp_path)
        assert result.passed is False
        assert "4 vulnerabilities" in result.details

    def test_run_npm_not_found(self, tmp_path):
        scanner = NpmAuditScanner()
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (1, "", "Command not found: npm")
            result = scanner.run(tmp_path)
        assert result.passed is True
        assert "not installed" in result.details


# ── SemgrepScanner tests ────────────────────────────────────────


class TestSemgrepScanner:
    def test_detect_checks_tool_availability(self, tmp_path):
        scanner = SemgrepScanner()
        with patch("factory.security.scanners._tool_available", return_value=True):
            assert scanner.detect(tmp_path) is True
        with patch("factory.security.scanners._tool_available", return_value=False):
            assert scanner.detect(tmp_path) is False

    def test_run_clean(self, tmp_path):
        scanner = SemgrepScanner()
        semgrep_output = json.dumps({"results": []})
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (0, semgrep_output, "")
            result = scanner.run(tmp_path)
        assert result.passed is True
        assert result.details == "clean"

    def test_run_with_findings(self, tmp_path):
        scanner = SemgrepScanner()
        semgrep_output = json.dumps({
            "results": [
                {
                    "check_id": "python.lang.security.audit.exec-detected",
                    "path": "app.py",
                    "start": {"line": 15},
                    "extra": {
                        "severity": "ERROR",
                        "message": "Detected use of exec()",
                        "fix": "Use ast.literal_eval instead",
                    },
                },
            ],
        })
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (1, semgrep_output, "")
            result = scanner.run(tmp_path)
        assert result.passed is False
        assert result.issue_count == 1
        assert result.issues[0].severity == SecuritySeverity.HIGH
        assert result.issues[0].file == "app.py"
        assert result.issues[0].line == 15

    def test_run_not_installed(self, tmp_path):
        scanner = SemgrepScanner()
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (1, "", "Command not found: semgrep")
            result = scanner.run(tmp_path)
        assert result.passed is True


# ── TrivyScanner tests ──────────────────────────────────────────


class TestTrivyScanner:
    def test_detect_checks_tool_availability(self, tmp_path):
        scanner = TrivyScanner()
        with patch("factory.security.scanners._tool_available", return_value=True):
            assert scanner.detect(tmp_path) is True
        with patch("factory.security.scanners._tool_available", return_value=False):
            assert scanner.detect(tmp_path) is False

    def test_run_clean(self, tmp_path):
        scanner = TrivyScanner()
        trivy_output = json.dumps({"Results": []})
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (0, trivy_output, "")
            result = scanner.run(tmp_path)
        assert result.passed is True
        assert result.details == "clean"

    def test_run_with_vulnerabilities(self, tmp_path):
        scanner = TrivyScanner()
        trivy_output = json.dumps({
            "Results": [
                {
                    "Target": "requirements.txt",
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-2024-1234",
                            "Severity": "CRITICAL",
                            "Title": "Remote code execution in foo",
                            "FixedVersion": "2.0.1",
                        },
                    ],
                    "Secrets": [],
                },
            ],
        })
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (0, trivy_output, "")
            result = scanner.run(tmp_path)
        assert result.passed is False
        assert result.issue_count == 1
        assert result.issues[0].severity == SecuritySeverity.CRITICAL
        assert result.issues[0].category == "CVE-2024-1234"

    def test_run_with_secrets(self, tmp_path):
        scanner = TrivyScanner()
        trivy_output = json.dumps({
            "Results": [
                {
                    "Target": "config.py",
                    "Vulnerabilities": [],
                    "Secrets": [
                        {
                            "StartLine": 5,
                            "Title": "AWS access key",
                        },
                    ],
                },
            ],
        })
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (0, trivy_output, "")
            result = scanner.run(tmp_path)
        assert result.passed is False
        assert result.issue_count == 1
        assert result.issues[0].severity == SecuritySeverity.HIGH
        assert result.issues[0].category == "hardcoded_secret"

    def test_run_not_installed(self, tmp_path):
        scanner = TrivyScanner()
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (1, "", "Command not found: trivy")
            result = scanner.run(tmp_path)
        assert result.passed is True


# ── GitSecretsScanner tests ─────────────────────────────────────


class TestGitSecretsScanner:
    def test_detect_needs_git_repo(self, tmp_path):
        scanner = GitSecretsScanner()
        assert scanner.detect(tmp_path) is False

    def test_detect_git_repo_with_tool(self, tmp_path):
        (tmp_path / ".git").mkdir()
        scanner = GitSecretsScanner()
        with patch("factory.security.scanners._tool_available", return_value=True):
            assert scanner.detect(tmp_path) is True

    def test_detect_git_repo_without_tool(self, tmp_path):
        (tmp_path / ".git").mkdir()
        scanner = GitSecretsScanner()
        with patch("factory.security.scanners._tool_available", return_value=False):
            assert scanner.detect(tmp_path) is False

    def test_run_clean(self, tmp_path):
        scanner = GitSecretsScanner()
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (0, "", "")
            result = scanner.run(tmp_path)
        assert result.passed is True
        assert result.details == "clean"

    def test_run_with_secrets(self, tmp_path):
        scanner = GitSecretsScanner()
        secrets_output = "config.py:10:AWS_SECRET_KEY=AKIA...\napp.py:25:password='admin123'"
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (1, secrets_output, "")
            result = scanner.run(tmp_path)
        assert result.passed is False
        assert result.issue_count == 2
        assert result.issues[0].file == "config.py"
        assert result.issues[0].line == 10
        assert result.issues[1].file == "app.py"
        assert result.issues[1].line == 25

    def test_run_not_installed(self, tmp_path):
        scanner = GitSecretsScanner()
        with patch("factory.security.scanners._run_cmd") as mock:
            mock.return_value = (1, "", "Command not found: git")
            result = scanner.run(tmp_path)
        assert result.passed is True


# ── eval_security integration tests ─────────────────────────────


class TestEvalSecurityIntegration:
    """Test eval_security() from hygiene.py using the scanner registry."""

    def test_no_scanner_returns_neutral(self, tmp_path):
        from factory.eval.hygiene import eval_security
        # Empty dir, no project markers, no scanners detected
        result = eval_security(tmp_path)
        assert result["name"] == "security"
        assert result["score"] == 0.5
        assert "Not detected" in result["details"]

    def test_clean_scan(self, tmp_path):
        from factory.eval.hygiene import eval_security

        (tmp_path / "pyproject.toml").write_text("[project]\n")

        clean_result = SecurityScanResult(
            scanner_name="bandit",
            issues=[],
            passed=True,
            details="clean",
        )
        with patch("factory.security.ScannerRegistry.scan", return_value=[clean_result]):
            result = eval_security(tmp_path)

        assert result["score"] == 1.0
        assert result["passed"] is True

    def test_scan_with_issues(self, tmp_path):
        from factory.eval.hygiene import eval_security

        (tmp_path / "pyproject.toml").write_text("[project]\n")

        issues = [
            SecurityIssue(severity=SecuritySeverity.HIGH, category="B101", scanner="bandit"),
            SecurityIssue(severity=SecuritySeverity.MEDIUM, category="B105", scanner="bandit"),
            SecurityIssue(severity=SecuritySeverity.LOW, category="B106", scanner="bandit"),
        ]
        result_with_issues = SecurityScanResult(
            scanner_name="bandit",
            issues=issues,
            passed=False,
            details="3 issues",
        )
        with patch("factory.security.ScannerRegistry.scan", return_value=[result_with_issues]):
            result = eval_security(tmp_path)

        assert result["score"] == round(1.0 - 3 * 0.1, 4)
        assert result["passed"] is False
        assert "3 issues" in result["details"]

    def test_score_floor_at_zero(self, tmp_path):
        from factory.eval.hygiene import eval_security

        (tmp_path / "pyproject.toml").write_text("[project]\n")

        issues = [
            SecurityIssue(severity=SecuritySeverity.LOW, category=f"B{i}", scanner="bandit")
            for i in range(15)
        ]
        result_many = SecurityScanResult(
            scanner_name="bandit",
            issues=issues,
            passed=False,
            details="15 issues",
        )
        with patch("factory.security.ScannerRegistry.scan", return_value=[result_many]):
            result = eval_security(tmp_path)

        assert result["score"] == 0.0
