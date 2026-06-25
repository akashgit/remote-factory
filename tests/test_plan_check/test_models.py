from factory.plan_check.models import (
    AcceptanceCriterion,
    CriterionResult,
    HypothesisVerdict,
    ReportSummary,
)


def _make_criterion(**overrides):
    defaults = {
        "criterion_id": "H1.deliverable.models_py",
        "hypothesis_id": "H1",
        "criterion_type": "deliverable",
        "description": "models.py exists",
        "verification_method": "file_exists",
        "target": {"path": "factory/plan_check/models.py"},
    }
    defaults.update(overrides)
    return AcceptanceCriterion(**defaults)


def _make_result(passed: bool = True, **overrides):
    defaults = {
        "criterion": _make_criterion(),
        "passed": passed,
    }
    defaults.update(overrides)
    return CriterionResult(**defaults)


def test_acceptance_criterion_serialization():
    original = _make_criterion()
    data = original.model_dump()
    restored = AcceptanceCriterion.model_validate(data)
    assert restored == original
    assert restored.criterion_id == "H1.deliverable.models_py"
    assert restored.target == {"path": "factory/plan_check/models.py"}

    json_str = original.model_dump_json()
    from_json = AcceptanceCriterion.model_validate_json(json_str)
    assert from_json == original


def test_criterion_result_with_failure():
    result = _make_result(
        passed=False,
        actual_value="file not found",
        expected_value="factory/plan_check/models.py exists",
        evidence=["checked path: factory/plan_check/models.py"],
        error=None,
    )
    assert not result.passed
    assert result.actual_value == "file not found"
    assert result.expected_value == "factory/plan_check/models.py exists"
    assert len(result.evidence) == 1


def test_hypothesis_verdict_all_pass():
    results = [
        _make_result(passed=True, criterion=_make_criterion(criterion_id=f"H1.d.{i}"))
        for i in range(3)
    ]
    verdict = HypothesisVerdict(
        hypothesis_id="H1",
        hypothesis_title="Scaffold package",
        criteria=results,
    )
    assert verdict.passed is True
    assert verdict.pass_rate == 1.0
    assert verdict.unsatisfied == []


def test_hypothesis_verdict_partial_fail():
    c1 = _make_result(
        passed=True,
        criterion=_make_criterion(criterion_id="H1.d.1", description="file A exists"),
    )
    c2 = _make_result(
        passed=True,
        criterion=_make_criterion(criterion_id="H1.d.2", description="file B exists"),
    )
    c3 = _make_result(
        passed=False,
        criterion=_make_criterion(criterion_id="H1.d.3", description="file C exists"),
    )
    verdict = HypothesisVerdict(
        hypothesis_id="H1",
        hypothesis_title="Scaffold package",
        criteria=[c1, c2, c3],
    )
    assert verdict.passed is False
    assert abs(verdict.pass_rate - 2 / 3) < 1e-9
    assert verdict.unsatisfied == ["file C exists"]


def test_report_summary_math():
    summary = ReportSummary(
        total_criteria=10,
        passed_criteria=7,
        failed_criteria=2,
        error_criteria=1,
        failed_hypotheses=["H2", "H3"],
    )
    assert summary.pass_rate == 0.7
    assert summary.all_passed is False

    summary_perfect = ReportSummary(
        total_criteria=5,
        passed_criteria=5,
        failed_criteria=0,
        error_criteria=0,
        failed_hypotheses=[],
    )
    assert summary_perfect.pass_rate == 1.0
    assert summary_perfect.all_passed is True
