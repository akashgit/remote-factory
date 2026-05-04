"""Data models for the security scanning subsystem.

SecurityIssue represents a single finding from any scanner.
SecurityScanResult aggregates issues from a single scanner run.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class SecuritySeverity(str, Enum):
    """Severity levels for security findings, ordered from most to least critical."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class SecurityIssue(BaseModel):
    """A single security finding from a scanner."""

    model_config = ConfigDict(strict=True, extra="forbid")

    severity: SecuritySeverity
    category: str
    file: str = ""
    line: int | None = None
    message: str = ""
    remediation: str = ""
    scanner: str = ""


class SecurityScanResult(BaseModel):
    """Aggregated result from a single scanner run."""

    model_config = ConfigDict(strict=True, extra="forbid")

    scanner_name: str
    issues: list[SecurityIssue] = []
    passed: bool = True
    details: str = ""
    duration_seconds: float = 0.0

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    def issues_by_severity(self, severity: SecuritySeverity) -> list[SecurityIssue]:
        """Filter issues by severity level."""
        return [i for i in self.issues if i.severity == severity]
