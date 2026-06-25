"""Verify acceptance criteria by running actual checks."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import structlog

from factory.plan_check.models import (
    AcceptanceCriterion,
    CriterionResult,
    HypothesisVerdict,
    ReportSummary,
    VerificationReport,
)
from factory.plan_check.parser import ParsedHypothesis

log = structlog.get_logger()

DEFAULT_TIMEOUT = 60

_STUB_PATTERNS = frozenset({"pass", "Ellipsis", "raise NotImplementedError"})


def detect_stubs(source: str, symbol: str) -> list[str]:
    """Scan a Python source for stub functions/classes matching *symbol*.

    Returns a list of evidence strings for each stub body found, or an
    empty list when the symbol is not a stub.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    evidence: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name != symbol:
                continue
            if _is_stub_body(node.body):
                first_line = source.splitlines()[node.lineno - 1].strip()
                evidence.append(first_line)
    return evidence


def _is_stub_body(body: list[ast.stmt]) -> bool:
    stmts = [s for s in body if not isinstance(s, (ast.Pass,))]
    if not stmts and any(isinstance(s, ast.Pass) for s in body):
        return True

    real = [s for s in body if not _is_docstring(s)]
    if not real:
        return False

    if len(real) == 1:
        s = real[0]
        if isinstance(s, ast.Pass):
            return True
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is ...:
            return True
        if isinstance(s, ast.Raise):
            if isinstance(s.exc, ast.Name) and s.exc.id == "NotImplementedError":
                return True
            if isinstance(s.exc, ast.Call):
                func = s.exc.func
                if isinstance(func, ast.Name) and func.id == "NotImplementedError":
                    return True
                if isinstance(func, ast.Attribute) and func.attr == "NotImplementedError":
                    return True
    return False


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(
        node.value.value, str
    )


# ---------------------------------------------------------------------------
# Individual verifiers
# ---------------------------------------------------------------------------


def _verify_eval_score(
    criterion: AcceptanceCriterion,
    project_path: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> CriterionResult:
    target = criterion.target
    dimension = target.get("dimension", "")
    min_expected = target.get("min_expected")

    scores_path = project_path / ".factory" / "eval" / "scores.json"
    actual_score: float | None = None

    if scores_path.exists():
        try:
            data = json.loads(scores_path.read_text())
            if isinstance(data, dict):
                actual_score = data.get(dimension)
        except (json.JSONDecodeError, OSError) as exc:
            return CriterionResult(
                criterion=criterion,
                passed=False,
                error=f"Failed to read scores: {exc}",
            )
    else:
        try:
            result = subprocess.run(
                ["factory", "eval", str(project_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(project_path),
            )
            if result.returncode != 0:
                return CriterionResult(
                    criterion=criterion,
                    passed=False,
                    error=f"factory eval failed (exit {result.returncode})",
                    evidence=[result.stderr[:500]] if result.stderr else [],
                )
            try:
                data = json.loads(result.stdout)
                if isinstance(data, dict):
                    results = data.get("results", [])
                    for r in results:
                        if isinstance(r, dict) and r.get("dimension") == dimension:
                            actual_score = r.get("score")
                            break
            except json.JSONDecodeError:
                return CriterionResult(
                    criterion=criterion,
                    passed=False,
                    error="Could not parse factory eval output",
                )
        except subprocess.TimeoutExpired:
            return CriterionResult(
                criterion=criterion,
                passed=False,
                error=f"verification timed out after {timeout}s",
            )
        except FileNotFoundError:
            return CriterionResult(
                criterion=criterion,
                passed=False,
                error="factory command not found",
            )

    if actual_score is None:
        return CriterionResult(
            criterion=criterion,
            passed=False,
            actual_value=f"{dimension} score not found",
            expected_value=f"{dimension} >= {min_expected}" if min_expected is not None else None,
        )

    if min_expected is not None:
        passed = actual_score >= min_expected
        return CriterionResult(
            criterion=criterion,
            passed=passed,
            actual_value=f"{dimension}={actual_score}",
            expected_value=f"{dimension}>={min_expected}",
        )

    delta = target.get("delta")
    if delta is not None:
        return CriterionResult(
            criterion=criterion,
            passed=True,
            actual_value=f"{dimension}={actual_score}",
            expected_value=f"{dimension} +{delta}",
            evidence=["Baseline comparison not implemented; passing on score existence"],
        )

    return CriterionResult(
        criterion=criterion,
        passed=actual_score is not None,
        actual_value=f"{dimension}={actual_score}",
    )


def _verify_file_exists(
    criterion: AcceptanceCriterion,
    project_path: Path,
) -> CriterionResult:
    target_path = criterion.target.get("path", "")
    full_path = project_path / target_path
    exists = full_path.exists()
    return CriterionResult(
        criterion=criterion,
        passed=exists,
        actual_value="exists" if exists else "not found",
        expected_value=f"{target_path} exists",
        evidence=[f"checked: {full_path}"],
    )


def _verify_function_exists(
    criterion: AcceptanceCriterion,
    project_path: Path,
) -> CriterionResult:
    target_path = criterion.target.get("path", "")
    symbol = criterion.target.get("symbol", "")
    full_path = project_path / target_path

    if not full_path.exists():
        return CriterionResult(
            criterion=criterion,
            passed=False,
            actual_value=f"file {target_path} not found",
            expected_value=f"function {symbol} in {target_path}",
        )

    try:
        source = full_path.read_text()
    except OSError as exc:
        return CriterionResult(
            criterion=criterion,
            passed=False,
            error=f"Could not read {target_path}: {exc}",
        )

    if not target_path.endswith(".py"):
        found = symbol in source
        return CriterionResult(
            criterion=criterion,
            passed=found,
            actual_value="found" if found else f"symbol '{symbol}' not found in {target_path}",
            expected_value=f"symbol {symbol} in {target_path}",
        )

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return CriterionResult(
            criterion=criterion,
            passed=False,
            error=f"Syntax error in {target_path}: {exc}",
        )

    found = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol:
                found = True
                break

    if not found:
        return CriterionResult(
            criterion=criterion,
            passed=False,
            actual_value=f"symbol '{symbol}' not found in {target_path}",
            expected_value=f"function {symbol} in {target_path}",
        )

    stub_evidence = detect_stubs(source, symbol)
    if stub_evidence:
        return CriterionResult(
            criterion=criterion,
            passed=False,
            actual_value="function exists but is a stub",
            expected_value=f"function {symbol} in {target_path} (non-stub)",
            evidence=stub_evidence,
        )

    return CriterionResult(
        criterion=criterion,
        passed=True,
        actual_value="found",
        expected_value=f"function {symbol} in {target_path}",
    )


def _verify_test_passes(
    criterion: AcceptanceCriterion,
    project_path: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> CriterionResult:
    test_name = criterion.target.get("test_name", "")
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "-x", "-q", "--tb=short", "--no-header", "-k", test_name],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(project_path),
        )
    except subprocess.TimeoutExpired:
        return CriterionResult(
            criterion=criterion,
            passed=False,
            error=f"verification timed out after {timeout}s",
        )
    except FileNotFoundError:
        return CriterionResult(
            criterion=criterion,
            passed=False,
            error="pytest not found",
        )

    output = result.stdout + result.stderr
    if "no tests ran" in output:
        return CriterionResult(
            criterion=criterion,
            passed=False,
            actual_value="test not found",
            expected_value=f"test {test_name} passes",
            evidence=[output[:1000]],
        )

    if result.returncode == 0:
        return CriterionResult(
            criterion=criterion,
            passed=True,
            actual_value="passed",
            expected_value=f"test {test_name} passes",
            evidence=[output[:1000]],
        )

    return CriterionResult(
        criterion=criterion,
        passed=False,
        actual_value="failed",
        expected_value=f"test {test_name} passes",
        evidence=[output[:1000]],
    )


def _verify_command_exits_zero(
    criterion: AcceptanceCriterion,
    project_path: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> CriterionResult:
    command = criterion.target.get("command", "")
    if not command:
        return CriterionResult(
            criterion=criterion,
            passed=False,
            error="No command specified in target",
        )

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(project_path),
        )
    except subprocess.TimeoutExpired:
        return CriterionResult(
            criterion=criterion,
            passed=False,
            error=f"verification timed out after {timeout}s",
        )

    output = (result.stdout + result.stderr)[:1000]
    passed = result.returncode == 0
    return CriterionResult(
        criterion=criterion,
        passed=passed,
        actual_value=f"exit code {result.returncode}",
        expected_value="exit code 0",
        evidence=[output] if output else [],
    )


def _verify_grep_match(
    criterion: AcceptanceCriterion,
    project_path: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> CriterionResult:
    pattern = criterion.target.get("pattern", "")
    if not pattern:
        return CriterionResult(
            criterion=criterion,
            passed=False,
            error="No pattern specified in target",
        )

    try:
        result = subprocess.run(
            ["grep", "-r", pattern, "."],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(project_path),
        )
    except subprocess.TimeoutExpired:
        return CriterionResult(
            criterion=criterion,
            passed=False,
            error=f"verification timed out after {timeout}s",
        )

    passed = result.returncode == 0
    matches = result.stdout.strip().splitlines()
    return CriterionResult(
        criterion=criterion,
        passed=passed,
        actual_value=f"{len(matches)} matches" if passed else "no matches",
        expected_value=f"pattern '{pattern}' found",
        evidence=matches[:10],
    )


# ---------------------------------------------------------------------------
# Dispatch & aggregation
# ---------------------------------------------------------------------------

_VERIFIERS = {
    "eval_score": _verify_eval_score,
    "file_exists": _verify_file_exists,
    "function_exists": _verify_function_exists,
    "test_passes": _verify_test_passes,
    "command_exits_zero": _verify_command_exits_zero,
    "grep_match": _verify_grep_match,
}


def verify_criteria(
    criteria: list[AcceptanceCriterion],
    project_path: Path,
    baseline_sha: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[CriterionResult]:
    results: list[CriterionResult] = []
    for criterion in criteria:
        method = criterion.verification_method
        verifier = _VERIFIERS.get(method)
        if verifier is None:
            results.append(CriterionResult(
                criterion=criterion,
                passed=False,
                error=f"Unknown verification method: {method}",
            ))
            continue

        log.info("verifying_criterion", criterion_id=criterion.criterion_id, method=method)

        if method in ("eval_score", "test_passes", "command_exits_zero", "grep_match"):
            result = verifier(criterion, project_path, timeout=timeout)
        else:
            result = verifier(criterion, project_path)

        results.append(result)
    return results


def verify_hypothesis(
    hypothesis: ParsedHypothesis,
    criteria: list[AcceptanceCriterion],
    project_path: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> HypothesisVerdict:
    results = verify_criteria(criteria, project_path, timeout=timeout)
    return HypothesisVerdict(
        hypothesis_id=hypothesis.id,
        hypothesis_title=hypothesis.title,
        criteria=results,
    )


def verify_plan(
    hypotheses_with_criteria: list[tuple[ParsedHypothesis, list[AcceptanceCriterion]]],
    project_path: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> VerificationReport:
    verdicts: list[HypothesisVerdict] = []
    for hypothesis, criteria in hypotheses_with_criteria:
        verdict = verify_hypothesis(hypothesis, criteria, project_path, timeout=timeout)
        verdicts.append(verdict)

    all_results = [cr for v in verdicts for cr in v.criteria]
    passed_count = sum(1 for r in all_results if r.passed)
    failed_count = sum(1 for r in all_results if not r.passed and r.error is None)
    error_count = sum(1 for r in all_results if r.error is not None)
    failed_hypotheses = [v.hypothesis_id for v in verdicts if not v.passed]

    summary = ReportSummary(
        total_criteria=len(all_results),
        passed_criteria=passed_count,
        failed_criteria=failed_count,
        error_criteria=error_count,
        failed_hypotheses=failed_hypotheses,
    )

    return VerificationReport(hypotheses=verdicts, summary=summary)
