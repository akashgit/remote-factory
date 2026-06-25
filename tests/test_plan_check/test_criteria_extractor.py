from factory.plan_check.criteria_extractor import extract_criteria
from factory.plan_check.parser import ParsedHypothesis


def _hyp(**kwargs) -> ParsedHypothesis:
    defaults = {"id": "H1", "title": "Test hypothesis"}
    defaults.update(kwargs)
    return ParsedHypothesis(**defaults)


def test_extract_eval_target_arrow():
    h = _hyp(expected_impact="tests 0.5 → 0.7")
    criteria = extract_criteria(h)
    evals = [c for c in criteria if c.criterion_type == "eval_target"]
    assert len(evals) == 1
    assert evals[0].target["dimension"] == "tests"
    assert evals[0].target["min_expected"] == 0.7
    assert evals[0].verification_method == "eval_score"
    assert evals[0].criterion_id == "H1.eval.tests"


def test_extract_eval_target_plus():
    h = _hyp(expected_impact="capability_surface +0.1")
    criteria = extract_criteria(h)
    evals = [c for c in criteria if c.criterion_type == "eval_target"]
    assert len(evals) == 1
    assert evals[0].target["dimension"] == "capability_surface"
    assert evals[0].target["delta"] == 0.1
    assert "min_expected" not in evals[0].target


def test_extract_multiple_eval_targets():
    h = _hyp(expected_impact="capability_surface 0.5 → 0.8, tests 0.7 → 0.85")
    criteria = extract_criteria(h)
    evals = [c for c in criteria if c.criterion_type == "eval_target"]
    assert len(evals) == 2
    dims = {c.target["dimension"] for c in evals}
    assert dims == {"capability_surface", "tests"}
    cap = next(c for c in evals if c.target["dimension"] == "capability_surface")
    assert cap.target["min_expected"] == 0.8
    tests = next(c for c in evals if c.target["dimension"] == "tests")
    assert tests.target["min_expected"] == 0.85


def test_extract_file_deliverable():
    h = _hyp(what="Create `factory/plan_check/models.py` with Pydantic v2 models")
    criteria = extract_criteria(h)
    files = [c for c in criteria if c.verification_method == "file_exists"]
    assert len(files) == 1
    assert files[0].target["path"] == "factory/plan_check/models.py"
    assert files[0].criterion_type == "deliverable"


def test_extract_function_deliverable():
    h = _hyp(
        what='Create `factory/plan_check/analyzer.py` with function `analyze_completion()`'
    )
    criteria = extract_criteria(h)
    funcs = [c for c in criteria if c.verification_method == "function_exists"]
    assert len(funcs) == 1
    assert funcs[0].target["symbol"] == "analyze_completion"
    assert funcs[0].target["path"] == "factory/plan_check/analyzer.py"
    assert funcs[0].criterion_type == "deliverable"


def test_extract_test_requirements():
    h = _hyp(
        what=(
            "Tests in `tests/test_plan_check/test_parser.py`:\n"
            "  - `test_parse_single_hypothesis` -- one block\n"
            "  - `test_parse_multiple_hypotheses` -- many blocks"
        )
    )
    criteria = extract_criteria(h)
    tests = [c for c in criteria if c.verification_method == "test_passes"]
    assert len(tests) == 2
    names = {c.target["test_name"] for c in tests}
    assert names == {"test_parse_single_hypothesis", "test_parse_multiple_hypotheses"}
    for c in tests:
        assert c.criterion_type == "test_requirement"


def test_extract_from_real_hypothesis():
    h = _hyp(
        id="H2",
        title="Parse current.md and extract criteria",
        category="EXPLORE",
        growth_dimension="capability_surface",
        what=(
            "  - Implement `factory/plan_check/parser.py` with function "
            "`parse_strategy_plan(content: str) -> list[ParsedHypothesis]`:\n"
            "    - Split content on regex\n"
            "    - Extract fields from each block\n"
            "  - Implement `factory/plan_check/criteria_extractor.py` with function "
            "`extract_criteria(hypothesis) -> list[AcceptanceCriterion]`:\n"
            "    - Eval target extraction\n"
            "    - Deliverable extraction\n"
            "  - Tests in `tests/test_plan_check/test_parser.py`:\n"
            "    - `test_parse_single_hypothesis`\n"
            "    - `test_parse_multiple_hypotheses`"
        ),
        expected_impact="capability_surface 0.2 → 0.5, tests 0.5 → 0.7",
        priority="high",
    )
    criteria = extract_criteria(h)

    evals = [c for c in criteria if c.criterion_type == "eval_target"]
    assert len(evals) == 2

    files = [c for c in criteria if c.verification_method == "file_exists"]
    assert len(files) == 3
    paths = {c.target["path"] for c in files}
    assert "factory/plan_check/parser.py" in paths
    assert "factory/plan_check/criteria_extractor.py" in paths
    assert "tests/test_plan_check/test_parser.py" in paths

    funcs = [c for c in criteria if c.verification_method == "function_exists"]
    assert len(funcs) == 2
    symbols = {c.target["symbol"] for c in funcs}
    assert symbols == {"parse_strategy_plan", "extract_criteria"}

    tests = [c for c in criteria if c.verification_method == "test_passes"]
    assert len(tests) == 2

    assert len(criteria) == 9


def test_extract_function_multiple_files_same_line():
    h = _hyp(
        what=(
            "Create `src/a.py` with function `foo()` and "
            "`src/b.py` with function `bar()`"
        )
    )
    criteria = extract_criteria(h)
    funcs = [c for c in criteria if c.verification_method == "function_exists"]
    assert len(funcs) == 2
    by_name = {c.target["symbol"]: c.target["path"] for c in funcs}
    assert by_name["foo"] == "src/a.py"
    assert by_name["bar"] == "src/b.py"


def test_no_criteria_from_empty_what():
    h = _hyp(what="", expected_impact="")
    criteria = extract_criteria(h)
    assert criteria == []
