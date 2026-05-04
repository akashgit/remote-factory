"""Built-in security scanner implementations.

Each scanner follows the SecurityScanner protocol:
  - detect(): check project compatibility + tool availability
  - run(): execute scan, parse output, return SecurityScanResult

Concrete scanners:
  - BanditScanner: Python static analysis (bandit)
  - NpmAuditScanner: Node.js dependency audit (npm audit)
  - SemgrepScanner: Multi-language pattern matching (semgrep)
  - TrivyScanner: Container/filesystem vulnerability scanning (trivy)
  - GitSecretsScanner: Hardcoded secrets detection (git-secrets)
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import structlog

from factory.security.models import SecurityIssue, SecurityScanResult, SecuritySeverity

log = structlog.get_logger()


# ── Shared helpers ───────────────────────────────────────────────


def _run_cmd(
    cmd: list[str],
    cwd: Path,
    timeout: int = 120,
) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr). Never raises."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"Timed out after {timeout}s"
    except FileNotFoundError:
        return 1, "", f"Command not found: {cmd[0]}"
    except Exception as exc:
        return 1, "", str(exc)


def _tool_available(cmd: list[str]) -> bool:
    """Check if a CLI tool is available by running a version/help command."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _detect_python_project(project_path: Path) -> bool:
    return (project_path / "pyproject.toml").exists() or (project_path / "setup.py").exists()


def _detect_node_project(project_path: Path) -> bool:
    return (project_path / "package.json").exists()


# ── Severity mapping helpers ─────────────────────────────────────


_BANDIT_SEVERITY_MAP: dict[str, SecuritySeverity] = {
    "HIGH": SecuritySeverity.HIGH,
    "MEDIUM": SecuritySeverity.MEDIUM,
    "LOW": SecuritySeverity.LOW,
}

_NPM_SEVERITY_MAP: dict[str, SecuritySeverity] = {
    "critical": SecuritySeverity.CRITICAL,
    "high": SecuritySeverity.HIGH,
    "moderate": SecuritySeverity.MEDIUM,
    "low": SecuritySeverity.LOW,
    "info": SecuritySeverity.INFO,
}


# ── BanditScanner ────────────────────────────────────────────────


class BanditScanner:
    """Python security scanner using bandit.

    Runs `bandit -r . -f json -q` and parses the JSON output to extract
    individual security issues with severity, file location, and remediation.
    """

    @property
    def name(self) -> str:
        return "bandit"

    def detect(self, project_path: Path) -> bool:
        """Detect if this is a Python project and bandit is available."""
        if not _detect_python_project(project_path):
            return False
        return _tool_available(["python", "-m", "bandit", "--version"])

    def run(self, project_path: Path) -> SecurityScanResult:
        """Run bandit and parse JSON results."""
        start = time.monotonic()
        rc, stdout, stderr = _run_cmd(
            ["python", "-m", "bandit", "-r", ".", "-f", "json", "-q"],
            project_path,
        )
        duration = time.monotonic() - start

        if rc == 1 and "Command not found" in stderr:
            return SecurityScanResult(
                scanner_name=self.name,
                passed=True,
                details="bandit not installed",
                duration_seconds=duration,
            )

        issues: list[SecurityIssue] = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
            for finding in data.get("results", []):
                severity_str = finding.get("issue_severity", "LOW")
                severity = _BANDIT_SEVERITY_MAP.get(severity_str, SecuritySeverity.LOW)
                issues.append(
                    SecurityIssue(
                        severity=severity,
                        category=finding.get("test_id", "unknown"),
                        file=finding.get("filename", ""),
                        line=finding.get("line_number"),
                        message=finding.get("issue_text", ""),
                        remediation=finding.get("more_info", ""),
                        scanner=self.name,
                    )
                )
        except (json.JSONDecodeError, TypeError):
            log.warning("bandit_parse_error", stdout=stdout[:200])

        passed = len(issues) == 0
        if issues:
            details = f"{len(issues)} issues found"
        else:
            details = "clean"

        return SecurityScanResult(
            scanner_name=self.name,
            issues=issues,
            passed=passed,
            details=details,
            duration_seconds=duration,
        )


# ── NpmAuditScanner ──────────────────────────────────────────────


class NpmAuditScanner:
    """Node.js dependency vulnerability scanner using npm audit.

    Runs `npm audit --json` and parses the vulnerability metadata
    to extract counts by severity level.
    """

    @property
    def name(self) -> str:
        return "npm-audit"

    def detect(self, project_path: Path) -> bool:
        """Detect if this is a Node project with a lockfile."""
        if not _detect_node_project(project_path):
            return False
        # npm audit requires a lockfile
        has_lockfile = (
            (project_path / "package-lock.json").exists()
            or (project_path / "npm-shrinkwrap.json").exists()
        )
        return has_lockfile and _tool_available(["npm", "--version"])

    def run(self, project_path: Path) -> SecurityScanResult:
        """Run npm audit and parse JSON results."""
        start = time.monotonic()
        rc, stdout, stderr = _run_cmd(
            ["npm", "audit", "--json"],
            project_path,
            timeout=180,
        )
        duration = time.monotonic() - start

        if rc == 1 and "Command not found" in stderr:
            return SecurityScanResult(
                scanner_name=self.name,
                passed=True,
                details="npm not installed",
                duration_seconds=duration,
            )

        issues: list[SecurityIssue] = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
            vulns = data.get("metadata", {}).get("vulnerabilities", {})
            for sev_name, count in vulns.items():
                if count > 0:
                    severity = _NPM_SEVERITY_MAP.get(sev_name, SecuritySeverity.LOW)
                    # npm audit gives counts per severity, not individual issues.
                    # Create one issue per severity level with the count.
                    issues.append(
                        SecurityIssue(
                            severity=severity,
                            category="dependency_vulnerability",
                            message=f"{count} {sev_name} vulnerabilit{'y' if count == 1 else 'ies'}",
                            scanner=self.name,
                        )
                    )
        except (json.JSONDecodeError, TypeError):
            log.warning("npm_audit_parse_error", stdout=stdout[:200])

        # Total vulnerability count from metadata
        total_vulns = 0
        try:
            data = json.loads(stdout) if stdout.strip() else {}
            vulns = data.get("metadata", {}).get("vulnerabilities", {})
            total_vulns = sum(vulns.get(s, 0) for s in ("low", "moderate", "high", "critical"))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        passed = total_vulns == 0
        if total_vulns > 0:
            details = f"{total_vulns} vulnerabilities"
        else:
            details = "clean"

        return SecurityScanResult(
            scanner_name=self.name,
            issues=issues,
            passed=passed,
            details=details,
            duration_seconds=duration,
        )


# ── SemgrepScanner ───────────────────────────────────────────────


class SemgrepScanner:
    """Multi-language static analysis using semgrep.

    Runs `semgrep scan --json --config auto` for broad rule coverage.
    Supports Python, JavaScript, TypeScript, Go, Ruby, Java, and more.
    """

    @property
    def name(self) -> str:
        return "semgrep"

    def detect(self, project_path: Path) -> bool:
        """Detect if semgrep is installed. Semgrep supports many languages,
        so project type detection is not needed."""
        return _tool_available(["semgrep", "--version"])

    def run(self, project_path: Path) -> SecurityScanResult:
        """Run semgrep and parse JSON results."""
        start = time.monotonic()
        rc, stdout, stderr = _run_cmd(
            ["semgrep", "scan", "--json", "--config", "auto", "--quiet"],
            project_path,
            timeout=300,
        )
        duration = time.monotonic() - start

        if rc == 1 and "Command not found" in stderr:
            return SecurityScanResult(
                scanner_name=self.name,
                passed=True,
                details="semgrep not installed",
                duration_seconds=duration,
            )

        issues: list[SecurityIssue] = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
            for finding in data.get("results", []):
                sev_str = finding.get("extra", {}).get("severity", "WARNING")
                severity = _semgrep_severity(sev_str)
                issues.append(
                    SecurityIssue(
                        severity=severity,
                        category=finding.get("check_id", "unknown"),
                        file=finding.get("path", ""),
                        line=finding.get("start", {}).get("line"),
                        message=finding.get("extra", {}).get("message", ""),
                        remediation=finding.get("extra", {}).get("fix", ""),
                        scanner=self.name,
                    )
                )
        except (json.JSONDecodeError, TypeError):
            log.warning("semgrep_parse_error", stdout=stdout[:200])

        passed = len(issues) == 0
        details = f"{len(issues)} findings" if issues else "clean"

        return SecurityScanResult(
            scanner_name=self.name,
            issues=issues,
            passed=passed,
            details=details,
            duration_seconds=duration,
        )


def _semgrep_severity(sev: str) -> SecuritySeverity:
    """Map semgrep severity string to SecuritySeverity."""
    mapping = {
        "ERROR": SecuritySeverity.HIGH,
        "WARNING": SecuritySeverity.MEDIUM,
        "INFO": SecuritySeverity.LOW,
    }
    return mapping.get(sev.upper(), SecuritySeverity.MEDIUM)


# ── TrivyScanner ─────────────────────────────────────────────────


class TrivyScanner:
    """Filesystem and container vulnerability scanner using trivy.

    Runs `trivy fs --format json --security-checks vuln,secret .` to scan
    for known vulnerabilities in dependencies and hardcoded secrets.
    """

    @property
    def name(self) -> str:
        return "trivy"

    def detect(self, project_path: Path) -> bool:
        """Detect if trivy is installed."""
        return _tool_available(["trivy", "--version"])

    def run(self, project_path: Path) -> SecurityScanResult:
        """Run trivy filesystem scan and parse JSON results."""
        start = time.monotonic()
        rc, stdout, stderr = _run_cmd(
            ["trivy", "fs", "--format", "json", "--scanners", "vuln,secret", "."],
            project_path,
            timeout=300,
        )
        duration = time.monotonic() - start

        if rc == 1 and "Command not found" in stderr:
            return SecurityScanResult(
                scanner_name=self.name,
                passed=True,
                details="trivy not installed",
                duration_seconds=duration,
            )

        issues: list[SecurityIssue] = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
            for result_entry in data.get("Results", []):
                target = result_entry.get("Target", "")
                for vuln in result_entry.get("Vulnerabilities", []):
                    severity = _trivy_severity(vuln.get("Severity", "UNKNOWN"))
                    issues.append(
                        SecurityIssue(
                            severity=severity,
                            category=vuln.get("VulnerabilityID", "unknown"),
                            file=target,
                            message=vuln.get("Title", vuln.get("Description", "")),
                            remediation=vuln.get("FixedVersion", ""),
                            scanner=self.name,
                        )
                    )
                for secret in result_entry.get("Secrets", []):
                    issues.append(
                        SecurityIssue(
                            severity=SecuritySeverity.HIGH,
                            category="hardcoded_secret",
                            file=target,
                            line=secret.get("StartLine"),
                            message=secret.get("Title", "Hardcoded secret detected"),
                            scanner=self.name,
                        )
                    )
        except (json.JSONDecodeError, TypeError):
            log.warning("trivy_parse_error", stdout=stdout[:200])

        passed = len(issues) == 0
        details = f"{len(issues)} findings" if issues else "clean"

        return SecurityScanResult(
            scanner_name=self.name,
            issues=issues,
            passed=passed,
            details=details,
            duration_seconds=duration,
        )


def _trivy_severity(sev: str) -> SecuritySeverity:
    """Map trivy severity string to SecuritySeverity."""
    mapping = {
        "CRITICAL": SecuritySeverity.CRITICAL,
        "HIGH": SecuritySeverity.HIGH,
        "MEDIUM": SecuritySeverity.MEDIUM,
        "LOW": SecuritySeverity.LOW,
        "UNKNOWN": SecuritySeverity.INFO,
    }
    return mapping.get(sev.upper(), SecuritySeverity.MEDIUM)


# ── GitSecretsScanner ────────────────────────────────────────────


class GitSecretsScanner:
    """Hardcoded secrets detector using git-secrets.

    Runs `git secrets --scan` to detect AWS keys, passwords, and other
    sensitive data committed to the repository.
    """

    @property
    def name(self) -> str:
        return "git-secrets"

    def detect(self, project_path: Path) -> bool:
        """Detect if git-secrets is installed and this is a git repo."""
        if not (project_path / ".git").exists():
            return False
        return _tool_available(["git", "secrets", "--list"])

    def run(self, project_path: Path) -> SecurityScanResult:
        """Run git-secrets scan and parse output."""
        start = time.monotonic()
        rc, stdout, stderr = _run_cmd(
            ["git", "secrets", "--scan", "-r", "."],
            project_path,
        )
        duration = time.monotonic() - start

        if rc == 1 and "Command not found" in stderr:
            return SecurityScanResult(
                scanner_name=self.name,
                passed=True,
                details="git-secrets not installed",
                duration_seconds=duration,
            )

        issues: list[SecurityIssue] = []
        if rc != 0 and stdout.strip():
            # git-secrets outputs matched lines as: filename:line_number:matched_content
            for line in stdout.strip().splitlines():
                parts = line.split(":", 2)
                file_path = parts[0] if len(parts) > 0 else ""
                line_num = None
                if len(parts) > 1:
                    try:
                        line_num = int(parts[1])
                    except ValueError:
                        pass
                issues.append(
                    SecurityIssue(
                        severity=SecuritySeverity.HIGH,
                        category="hardcoded_secret",
                        file=file_path,
                        line=line_num,
                        message="Potential secret or credential detected",
                        remediation="Remove the secret and rotate credentials",
                        scanner=self.name,
                    )
                )

        passed = len(issues) == 0
        if issues:
            details = f"{len(issues)} potential secrets found"
        elif rc == 0:
            details = "clean"
        else:
            details = "scan completed with warnings"

        return SecurityScanResult(
            scanner_name=self.name,
            issues=issues,
            passed=passed,
            details=details,
            duration_seconds=duration,
        )
