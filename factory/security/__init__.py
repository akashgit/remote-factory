"""Security scanning subsystem: pluggable scanner architecture.

Provides a Protocol-based scanner interface, a registry with auto-detection,
and concrete scanner implementations for multiple security tools.

Usage:
    from factory.security import ScannerRegistry

    registry = ScannerRegistry()
    results = registry.scan(project_path)
"""

from __future__ import annotations

import structlog
from pathlib import Path
from typing import Protocol, runtime_checkable

from factory.security.models import SecurityScanResult

log = structlog.get_logger()


@runtime_checkable
class SecurityScanner(Protocol):
    """Interface for security scanning tools.

    Each scanner must implement:
      - name: human-readable scanner identifier
      - detect: check if the scanner is applicable to the project
      - run: execute the scan and return structured results
    """

    @property
    def name(self) -> str:
        """Human-readable name for this scanner (e.g. 'bandit', 'npm-audit')."""
        ...

    def detect(self, project_path: Path) -> bool:
        """Return True if this scanner is applicable to the given project.

        This checks both project compatibility (e.g. Python project for bandit)
        and tool availability (e.g. bandit is installed).
        """
        ...

    def run(self, project_path: Path) -> SecurityScanResult:
        """Execute the security scan and return structured results.

        Should not raise exceptions. If the scanner fails, return a
        SecurityScanResult with passed=False and details explaining the failure.
        """
        ...


class ScannerRegistry:
    """Registry of security scanners with auto-detection.

    Scanners are registered at import time and auto-detected per project.
    The registry handles sub-project discovery so individual scanners
    only need to operate on a single project root.
    """

    def __init__(self) -> None:
        self._scanners: list[SecurityScanner] = []

    def register(self, scanner: SecurityScanner) -> None:
        """Add a scanner to the registry."""
        self._scanners.append(scanner)

    @property
    def scanners(self) -> list[SecurityScanner]:
        """All registered scanners."""
        return list(self._scanners)

    def detect(self, project_path: Path) -> list[SecurityScanner]:
        """Return scanners applicable to the given project path."""
        applicable = []
        for scanner in self._scanners:
            try:
                if scanner.detect(project_path):
                    applicable.append(scanner)
            except Exception:
                log.warning("scanner_detect_error", scanner=scanner.name, exc_info=True)
        return applicable

    def scan(self, project_path: Path) -> list[SecurityScanResult]:
        """Run all applicable scanners against the project.

        Returns a list of SecurityScanResult, one per scanner that ran.
        Scanners that are not applicable (detect returns False) are skipped.
        """
        results: list[SecurityScanResult] = []
        applicable = self.detect(project_path)

        if not applicable:
            log.debug("no_applicable_scanners", project=str(project_path))
            return results

        for scanner in applicable:
            try:
                result = scanner.run(project_path)
                results.append(result)
                log.debug(
                    "scanner_completed",
                    scanner=scanner.name,
                    issues=result.issue_count,
                    passed=result.passed,
                )
            except Exception:
                log.warning("scanner_run_error", scanner=scanner.name, exc_info=True)
                results.append(
                    SecurityScanResult(
                        scanner_name=scanner.name,
                        passed=False,
                        details=f"Scanner {scanner.name} failed with an unexpected error",
                    )
                )

        return results


# Global default registry instance, pre-populated with all built-in scanners.
_default_registry: ScannerRegistry | None = None


def get_default_registry() -> ScannerRegistry:
    """Return the global default scanner registry, creating it on first call.

    Lazily imports and registers all built-in scanners to avoid circular
    imports and unnecessary work if the security subsystem is not used.
    """
    global _default_registry
    if _default_registry is not None:
        return _default_registry

    _default_registry = ScannerRegistry()

    # Import and register built-in scanners
    from factory.security.scanners import (
        BanditScanner,
        GitSecretsScanner,
        NpmAuditScanner,
        SemgrepScanner,
        TrivyScanner,
    )

    _default_registry.register(BanditScanner())
    _default_registry.register(NpmAuditScanner())
    _default_registry.register(SemgrepScanner())
    _default_registry.register(TrivyScanner())
    _default_registry.register(GitSecretsScanner())

    return _default_registry
