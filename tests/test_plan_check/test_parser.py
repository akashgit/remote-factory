from factory.plan_check.parser import parse_strategy_plan


SINGLE_HYPOTHESIS = """\
## Build Plan

### Phase 1: Scaffold
#### H1: Set up package with models
- **Category:** EXPLORE
- **Growth dimension:** capability_surface
- **What:**
  - Create `factory/plan_check/__init__.py`
  - Create `factory/plan_check/models.py` with Pydantic v2 models
- **Expected impact:** capability_surface 0.0 → 0.2, tests 0.0 → 0.5
- **Priority:** high
"""

MULTIPLE_HYPOTHESES = """\
### Phase 1
#### H1: Scaffold package
- **Category:** EXPLORE
- **Growth dimension:** capability_surface
- **What:**
  - Create models
- **Expected impact:** capability_surface 0.0 → 0.2
- **Priority:** high

### Phase 2
#### H2: Build parser
- **Category:** EXPLORE
- **Growth dimension:** capability_surface
- **What:**
  - Implement parser
- **Expected impact:** capability_surface 0.2 → 0.5
- **Priority:** high

### Phase 3
#### H3: Build verifier
- **Category:** FIX
- **Growth dimension:** tests
- **What:**
  - Implement verifier
- **Expected impact:** tests 0.5 → 0.8
- **Priority:** medium
"""


def test_parse_single_hypothesis():
    results = parse_strategy_plan(SINGLE_HYPOTHESIS)
    assert len(results) == 1
    h = results[0]
    assert h.id == "H1"
    assert h.title == "Set up package with models"
    assert h.category == "EXPLORE"
    assert h.growth_dimension == "capability_surface"
    assert h.expected_impact == "capability_surface 0.0 → 0.2, tests 0.0 → 0.5"
    assert h.priority == "high"


def test_parse_multiple_hypotheses():
    results = parse_strategy_plan(MULTIPLE_HYPOTHESES)
    assert len(results) == 3
    assert results[0].id == "H1"
    assert results[1].id == "H2"
    assert results[2].id == "H3"
    assert results[0].title == "Scaffold package"
    assert results[1].title == "Build parser"
    assert results[2].title == "Build verifier"
    assert results[2].category == "FIX"
    assert results[2].growth_dimension == "tests"
    assert results[2].priority == "medium"


def test_parse_multiline_what():
    results = parse_strategy_plan(SINGLE_HYPOTHESIS)
    h = results[0]
    assert "`factory/plan_check/__init__.py`" in h.what
    assert "`factory/plan_check/models.py`" in h.what
    assert "Pydantic v2 models" in h.what
    lines = [ln.strip() for ln in h.what.split("\n") if ln.strip()]
    assert len(lines) >= 2


def test_parse_missing_optional_fields():
    content = """\
#### H1: Minimal hypothesis
- **What:**
  - Do something
"""
    results = parse_strategy_plan(content)
    assert len(results) == 1
    h = results[0]
    assert h.id == "H1"
    assert h.title == "Minimal hypothesis"
    assert h.category == ""
    assert h.growth_dimension == ""
    assert h.expected_impact == ""
    assert h.type == ""
    assert h.priority == ""
    assert "Do something" in h.what
