"""Pre-check gate — hard, non-overridable checks before keep/revert decisions.

The CEO CANNOT override a failed precheck. A failure means mandatory revert.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from factory.models import HardConstraint
from factory.strategy import find_anti_patterns

log = structlog.get_logger()


@dataclass
class CheckResult:
    """Result of a single precheck."""

    name: str
    passed: bool
    detail: str


@dataclass
class PreCheckResult:
    """Aggregate result of all prechecks."""

    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    blocking_failures: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = []
        for c in self.checks:
            icon = "PASS" if c.passed else "FAIL"
            lines.append(f"  {icon}: {c.name} — {c.detail}")
        if self.blocking_failures:
            lines.append(f"\nBLOCKING: {', '.join(self.blocking_failures)}")
        return "\n".join(lines)


def check_score_direction(
    score_before: float | None,
    score_after: float | None,
    threshold: float,
) -> CheckResult:
    """Verify score did not regress and meets threshold."""
    if score_before is None or score_after is None:
        return CheckResult(
            name="score_direction",
            passed=False,
            detail="Missing score data (before or after is None)",
        )

    if score_after < score_before:
        return CheckResult(
            name="score_direction",
            passed=False,
            detail=f"Score regressed: {score_before:.4f} → {score_after:.4f} (delta={score_after - score_before:+.4f})",
        )

    if score_after < threshold:
        return CheckResult(
            name="score_direction",
            passed=False,
            detail=f"Below threshold: {score_after:.4f} < {threshold:.4f}",
        )

    return CheckResult(
        name="score_direction",
        passed=True,
        detail=f"Score OK: {score_before:.4f} → {score_after:.4f} (delta={score_after - score_before:+.4f}, threshold={threshold:.4f})",
    )


def check_scope(
    project_path: Path,
    baseline_sha: str,
    allowed_scope: list[str] | None = None,
) -> CheckResult:
    """Run factory guard --check-scope and report pass/fail."""
    cmd = ["uv", "run", "python", "-m", "factory", "guard", str(project_path), "--baseline", baseline_sha]
    if allowed_scope:
        cmd.append("--check-scope")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=project_path,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(name="scope", passed=False, detail="Guard check timed out")
    except FileNotFoundError:
        return CheckResult(name="scope", passed=False, detail="Guard command not found")

    if result.returncode == 0:
        return CheckResult(name="scope", passed=True, detail="Guard check clean")

    violations = [
        line.replace("VIOLATION: ", "").strip()
        for line in result.stdout.splitlines()
        if line.startswith("VIOLATION:")
    ]
    return CheckResult(
        name="scope",
        passed=False,
        detail=f"Guard violations: {'; '.join(violations) or result.stdout.strip()[:200]}",
    )


def check_anti_pattern(
    hypothesis: str,
    history: list[dict],
    similarity_threshold: float = 0.6,
) -> CheckResult:
    """Check if hypothesis is too similar to a previously reverted experiment."""
    matches = find_anti_patterns(hypothesis, history, similarity_threshold)
    if not matches:
        return CheckResult(
            name="anti_pattern",
            passed=True,
            detail="No similar reverted experiments found",
        )

    best = max(matches, key=lambda m: m["similarity"])
    return CheckResult(
        name="anti_pattern",
        passed=False,
        detail=(
            f"Similar to reverted experiment #{best.get('id', '?')}: "
            f"'{best.get('hypothesis', '')[:60]}' "
            f"(similarity={best['similarity']:.2f})"
        ),
    )


def check_surfaces(
    project_path: Path,
    baseline_sha: str,
) -> CheckResult:
    """Run factory guard --check-surfaces and report pass/fail.

    This is a hard gate — the CEO cannot override a failed surface check.
    Any modification to a fixed surface file is a mandatory revert.
    """
    cmd = [
        "uv", "run", "python", "-m", "factory", "guard",
        str(project_path), "--baseline", baseline_sha, "--check-surfaces",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=project_path,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(name="fixed_surfaces", passed=False, detail="Surface guard check timed out")
    except FileNotFoundError:
        return CheckResult(name="fixed_surfaces", passed=False, detail="Guard command not found")

    if result.returncode == 0:
        return CheckResult(name="fixed_surfaces", passed=True, detail="No fixed surfaces modified")

    violations = [
        line.replace("VIOLATION: ", "").strip()
        for line in result.stdout.splitlines()
        if line.startswith("VIOLATION:")
    ]
    return CheckResult(
        name="fixed_surfaces",
        passed=False,
        detail=f"Fixed surface violations: {'; '.join(violations) or result.stdout.strip()[:200]}",
    )


def check_leakage(
    hypothesis: str,
    project_path: Path,
    fixed_surfaces: list[str],
    baseline_sha: str | None = None,
    sensitivity: str = "medium",
) -> CheckResult:
    """Check for ground truth content leaking into hypothesis or code diff.

    Scans:
    1. The hypothesis text against fingerprints of fixed surface files
    2. The PR diff (if baseline_sha provided) against the same fingerprints

    This catches both direct references ("the answer is 42") and indirect
    hints ("do NOT use subtraction").
    """
    from factory.research.leakage import (
        fingerprint_fixed_surfaces,
        get_diff_text,
        scan_diff_for_leakage,
        scan_for_leakage,
    )

    fingerprints = fingerprint_fixed_surfaces(project_path, fixed_surfaces)
    if not fingerprints:
        return CheckResult(
            name="ground_truth_leakage",
            passed=True,
            detail="No fixed surface files found to fingerprint (skipped)",
        )

    # Scan hypothesis text
    report = scan_for_leakage(hypothesis, fingerprints, sensitivity)

    # Also scan PR diff if baseline is available
    if baseline_sha and not report.flagged:
        diff_text = get_diff_text(project_path, baseline_sha)
        if diff_text:
            report = scan_diff_for_leakage(diff_text, fingerprints, sensitivity)

    if not report.flagged:
        return CheckResult(
            name="ground_truth_leakage",
            passed=True,
            detail="No ground truth leakage detected",
        )

    finding_details = "; ".join(
        f"{f.leak_type}: '{f.leaked_token}' from {f.source_file}"
        for f in report.findings[:3]
    )
    return CheckResult(
        name="ground_truth_leakage",
        passed=report.risk_level not in ("medium", "high"),
        detail=f"Leakage risk={report.risk_level}: {finding_details}",
    )


def check_smoke_test(
    smoke_test_command: str,
    project_path: Path,
    timeout: float = 120,
) -> CheckResult:
    """Run the smoke test command and report pass/fail."""
    if not smoke_test_command.strip():
        return CheckResult(
            name="smoke_test",
            passed=True,
            detail="No smoke test configured (skipped)",
        )

    try:
        result = subprocess.run(
            smoke_test_command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=project_path,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="smoke_test",
            passed=False,
            detail=f"Smoke test timed out after {timeout}s",
        )
    except Exception as e:
        return CheckResult(
            name="smoke_test",
            passed=False,
            detail=f"Smoke test error: {e}",
        )

    if result.returncode == 0:
        return CheckResult(
            name="smoke_test",
            passed=True,
            detail="Smoke test passed",
        )

    stderr_snippet = result.stderr.strip()[:200] if result.stderr else ""
    stdout_snippet = result.stdout.strip()[:200] if result.stdout else ""
    output = stderr_snippet or stdout_snippet or f"exit code {result.returncode}"
    return CheckResult(
        name="smoke_test",
        passed=False,
        detail=f"Smoke test failed: {output}",
    )


def check_hard_constraints(
    constraints: list[HardConstraint],
    project_path: Path,
    timeout: float = 120,
) -> list[CheckResult]:
    """Run user-defined hard constraint checks. Each must exit 0 to pass."""
    log.info("hard_constraints_start", count=len(constraints))
    results: list[CheckResult] = []
    for constraint in constraints:
        try:
            result = subprocess.run(
                constraint.check,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=project_path,
            )
        except subprocess.TimeoutExpired:
            results.append(CheckResult(
                name=f"hard_constraint:{constraint.name}",
                passed=False,
                detail=f"Hard constraint '{constraint.name}' timed out after {timeout}s",
            ))
            continue
        except Exception as e:
            results.append(CheckResult(
                name=f"hard_constraint:{constraint.name}",
                passed=False,
                detail=f"Hard constraint '{constraint.name}' error: {e}",
            ))
            continue

        if result.returncode == 0:
            log.info("hard_constraint_result", name=constraint.name, passed=True)
            results.append(CheckResult(
                name=f"hard_constraint:{constraint.name}",
                passed=True,
                detail=f"Hard constraint '{constraint.name}' passed",
            ))
        else:
            stderr_snippet = result.stderr.strip()[:200] if result.stderr else ""
            stdout_snippet = result.stdout.strip()[:200] if result.stdout else ""
            output = stderr_snippet or stdout_snippet or f"exit code {result.returncode}"
            log.info("hard_constraint_result", name=constraint.name, passed=False, detail=output)
            results.append(CheckResult(
                name=f"hard_constraint:{constraint.name}",
                passed=False,
                detail=f"Hard constraint '{constraint.name}' failed: {output}",
            ))
    return results


def run_precheck(
    *,
    score_before: float | None,
    score_after: float | None,
    threshold: float,
    hypothesis: str,
    history: list[dict],
    project_path: Path,
    baseline_sha: str | None = None,
    allowed_scope: list[str] | None = None,
    smoke_test_command: str = "",
    similarity_threshold: float = 0.6,
    fixed_surfaces: list[str] | None = None,
    hard_constraints: list[HardConstraint] | None = None,
) -> PreCheckResult:
    """Run all prechecks and return aggregate result.

    A single failure makes the whole precheck fail. The CEO cannot override this.
    """
    checks: list[CheckResult] = []

    # 1. Score direction
    checks.append(check_score_direction(score_before, score_after, threshold))

    # 2. Scope / guard check (only if baseline SHA provided)
    if baseline_sha:
        checks.append(check_scope(project_path, baseline_sha, allowed_scope))

    # 3. Fixed surface guard (only if baseline + fixed_surfaces provided)
    if baseline_sha and fixed_surfaces:
        checks.append(check_surfaces(project_path, baseline_sha))

    # 4. Ground truth leakage check (only if fixed_surfaces provided)
    if fixed_surfaces:
        checks.append(check_leakage(hypothesis, project_path, fixed_surfaces, baseline_sha))

    # 5. Anti-pattern detection
    checks.append(check_anti_pattern(hypothesis, history, similarity_threshold))

    # 6. Smoke test
    checks.append(check_smoke_test(smoke_test_command, project_path))

    # 7. Hard constraints (user-defined checks from factory.md)
    if hard_constraints:
        checks.extend(check_hard_constraints(hard_constraints, project_path))

    # Aggregate
    failures = [c.name for c in checks if not c.passed]
    passed = len(failures) == 0

    result = PreCheckResult(
        passed=passed,
        checks=checks,
        blocking_failures=failures,
    )

    log.info(
        "precheck_complete",
        passed=passed,
        checks_run=len(checks),
        failures=failures,
    )
    return result
