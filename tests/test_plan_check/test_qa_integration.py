"""Tests for QA Agent integration helpers."""

from __future__ import annotations

from factory.plan_check.models import (
    AcceptanceCriterion,
    CriterionResult,
    HypothesisVerdict,
    ReportSummary,
    VerificationReport,
)
from factory.plan_check.qa_integration import format_for_qa, get_redirect_payload


def _make_report(*, with_failures: bool = True) -> VerificationReport:
    passing_criterion = CriterionResult(
        criterion=AcceptanceCriterion(
            criterion_id="H1.deliverable.greeter_py",
            hypothesis_id="H1",
            criterion_type="deliverable",
            description="greeter.py exists",
            verification_method="file_exists",
            target={"path": "src/greeter.py"},
        ),
        passed=True,
        actual_value="exists",
        expected_value="src/greeter.py exists",
    )

    failing_criterion = CriterionResult(
        criterion=AcceptanceCriterion(
            criterion_id="H2.eval.tests",
            hypothesis_id="H2",
            criterion_type="eval_target",
            description="tests score reaches 0.7",
            verification_method="eval_score",
            target={"dimension": "tests", "min_expected": 0.7},
        ),
        passed=False,
        actual_value="tests=0.55",
        expected_value="tests>=0.7",
    )

    h1 = HypothesisVerdict(
        hypothesis_id="H1",
        hypothesis_title="Implement greeter",
        criteria=[passing_criterion],
    )

    if with_failures:
        h2 = HypothesisVerdict(
            hypothesis_id="H2",
            hypothesis_title="Implement farewell",
            criteria=[failing_criterion],
        )
        hypotheses = [h1, h2]
        summary = ReportSummary(
            total_criteria=2,
            passed_criteria=1,
            failed_criteria=1,
            error_criteria=0,
            failed_hypotheses=["H2"],
        )
    else:
        hypotheses = [h1]
        summary = ReportSummary(
            total_criteria=1,
            passed_criteria=1,
            failed_criteria=0,
            error_criteria=0,
        )

    return VerificationReport(hypotheses=hypotheses, summary=summary)


class TestFormatForQa:
    def test_highlights_failures(self):
        report = _make_report(with_failures=True)
        output = format_for_qa(report)
        assert "UNSATISFIED CRITERIA" in output
        assert "H2" in output
        assert "tests score reaches 0.7" in output
        assert "tests>=0.7" in output
        assert "tests=0.55" in output

    def test_all_passed_message(self):
        report = _make_report(with_failures=False)
        output = format_for_qa(report)
        assert "All acceptance criteria satisfied" in output
        assert "UNSATISFIED" not in output

    def test_includes_command_hint(self):
        report = _make_report(with_failures=True)
        output = format_for_qa(report)
        assert "factory plan-check" in output


class TestRedirectPayload:
    def test_structure_with_failures(self):
        report = _make_report(with_failures=True)
        payload = get_redirect_payload(report)

        assert "unsatisfied" in payload
        assert "redirect_message" in payload
        assert isinstance(payload["unsatisfied"], list)
        assert len(payload["unsatisfied"]) == 1

        item = payload["unsatisfied"][0]
        assert item["hypothesis_id"] == "H2"
        assert item["criterion"] == "tests score reaches 0.7"
        assert item["expected"] == "tests>=0.7"
        assert item["actual"] == "tests=0.55"

    def test_structure_all_passed(self):
        report = _make_report(with_failures=False)
        payload = get_redirect_payload(report)
        assert payload["unsatisfied"] == []
        assert "All criteria satisfied" in payload["redirect_message"]

    def test_redirect_message_includes_hypothesis_ids(self):
        report = _make_report(with_failures=True)
        payload = get_redirect_payload(report)
        assert "H2" in payload["redirect_message"]
