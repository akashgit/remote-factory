"""Tests for factory.plan_check.reporter — Phase 4 report generator."""

from __future__ import annotations

import json

from factory.plan_check.models import (
    AcceptanceCriterion,
    CriterionResult,
    HypothesisVerdict,
    ReportSummary,
    VerificationReport,
)
from factory.plan_check.reporter import (
    generate_report,
    to_json,
    to_markdown,
    write_report,
)


def _criterion(
    cid: str,
    hyp: str,
    desc: str,
    *,
    ctype: str = "deliverable",
    method: str = "file_exists",
    target: dict | None = None,
) -> AcceptanceCriterion:
    return AcceptanceCriterion(
        criterion_id=cid,
        hypothesis_id=hyp,
        criterion_type=ctype,
        description=desc,
        verification_method=method,
        target=target or {"path": "some/file.py"},
    )


def _result(
    cid: str,
    hyp: str,
    desc: str,
    *,
    passed: bool = True,
    actual: str | None = None,
    expected: str | None = None,
    evidence: list[str] | None = None,
    error: str | None = None,
) -> CriterionResult:
    return CriterionResult(
        criterion=_criterion(cid, hyp, desc),
        passed=passed,
        actual_value=actual,
        expected_value=expected,
        evidence=evidence or [],
        error=error,
    )


def _all_pass_report() -> VerificationReport:
    h1 = HypothesisVerdict(
        hypothesis_id="H1",
        hypothesis_title="Set up scaffold",
        criteria=[
            _result("H1.file.models", "H1", "models.py exists"),
            _result("H1.file.init", "H1", "__init__.py exists"),
        ],
    )
    h2 = HypothesisVerdict(
        hypothesis_id="H2",
        hypothesis_title="Parser works",
        criteria=[
            _result("H2.test.parse", "H2", "test_parse passes"),
        ],
    )
    return VerificationReport(
        hypotheses=[h1, h2],
        summary=ReportSummary(
            total_criteria=3,
            passed_criteria=3,
            failed_criteria=0,
            error_criteria=0,
            failed_hypotheses=[],
        ),
    )


def _mixed_report() -> VerificationReport:
    h1 = HypothesisVerdict(
        hypothesis_id="H1",
        hypothesis_title="Set up scaffold",
        criteria=[
            _result("H1.file.models", "H1", "models.py exists"),
            _result("H1.file.init", "H1", "__init__.py exists"),
            _result(
                "H1.eval.tests",
                "H1",
                "tests score >= 0.7",
                passed=False,
                actual="tests score = 0.55",
                expected="tests score >= 0.7",
                evidence=["pytest: 3 passed, 2 failed"],
            ),
        ],
    )
    h2 = HypothesisVerdict(
        hypothesis_id="H2",
        hypothesis_title="Parser works",
        criteria=[
            _result("H2.test.parse", "H2", "test_parse passes"),
            _result(
                "H2.func.extract",
                "H2",
                "extract_criteria function exists",
                passed=False,
                actual="function is a stub",
                expected="non-stub implementation",
            ),
        ],
    )
    return VerificationReport(
        hypotheses=[h1, h2],
        summary=ReportSummary(
            total_criteria=5,
            passed_criteria=3,
            failed_criteria=2,
            error_criteria=0,
            failed_hypotheses=["H1", "H2"],
        ),
    )


def test_report_all_criteria_pass():
    report = _all_pass_report()
    enriched = generate_report(report)
    md = to_markdown(enriched)
    j = to_json(enriched)
    data = json.loads(j)

    assert data["all_passed"] is True
    assert data["redirect_needed"] is False
    assert data["unsatisfied_criteria"] == []
    assert "ALL CRITERIA MET" in md


def test_report_some_criteria_fail():
    report = _mixed_report()
    enriched = generate_report(report)
    j = to_json(enriched)
    data = json.loads(j)

    assert data["all_passed"] is False
    assert data["redirect_needed"] is True
    assert len(data["unsatisfied_criteria"]) == 2
    unsatisfied_ids = {u["criterion_id"] for u in data["unsatisfied_criteria"]}
    assert unsatisfied_ids == {"H1.eval.tests", "H2.func.extract"}


def test_report_actual_vs_expected_in_markdown():
    report = _mixed_report()
    enriched = generate_report(report)
    md = to_markdown(enriched)

    assert "**Expected:** tests score >= 0.7" in md
    assert "**Actual:** tests score = 0.55" in md


def test_report_evidence_included():
    report = _mixed_report()
    enriched = generate_report(report)
    md = to_markdown(enriched)

    assert "**Evidence:**" in md
    assert "pytest: 3 passed, 2 failed" in md


def test_report_failed_first_ordering():
    report = _mixed_report()
    enriched = generate_report(report)

    assert not enriched.hypotheses[0].passed
    assert not enriched.hypotheses[1].passed

    all_pass = _all_pass_report()
    combined = VerificationReport(
        hypotheses=[all_pass.hypotheses[0], report.hypotheses[0]],
        summary=ReportSummary(
            total_criteria=5,
            passed_criteria=4,
            failed_criteria=1,
            error_criteria=0,
            failed_hypotheses=["H1"],
        ),
    )
    enriched2 = generate_report(combined)
    assert not enriched2.hypotheses[0].passed, "failed hypothesis should be first"
    assert enriched2.hypotheses[1].passed, "passing hypothesis should be second"

    failing_h = enriched2.hypotheses[0]
    first_criterion = failing_h.criteria[0]
    assert not first_criterion.passed, "failed criteria should sort before passed"


def test_report_json_roundtrip():
    report = _mixed_report()
    enriched = generate_report(report)
    j = to_json(enriched)
    data = json.loads(j)

    assert isinstance(data["hypotheses"], list)
    assert len(data["hypotheses"]) == 2
    assert "summary" in data
    assert data["pass_rate"] == data["summary"]["pass_rate"]
    assert "unsatisfied_criteria" in data

    for h in data["hypotheses"]:
        assert "hypothesis_id" in h
        assert "criteria" in h
        for cr in h["criteria"]:
            assert "criterion" in cr
            assert "passed" in cr


def test_report_unsatisfied_section():
    report = _mixed_report()
    enriched = generate_report(report)
    md = to_markdown(enriched)

    assert "### Unsatisfied Acceptance Criteria" in md
    assert "**H1.eval.tests**" in md
    assert "**H2.func.extract**" in md
    assert "REDIRECT NEEDED" in md


def test_report_write_creates_files(tmp_path):
    report = _all_pass_report()
    enriched = generate_report(report)
    out = tmp_path / "reports"
    md_path, json_path = write_report(enriched, out)

    assert md_path.exists()
    assert json_path.exists()
    assert md_path.name == "acceptance-verification.md"
    assert json_path.name == "acceptance-verification.json"

    md_content = md_path.read_text()
    assert "Acceptance Criteria Verification Report" in md_content

    json_content = json.loads(json_path.read_text())
    assert json_content["all_passed"] is True
