"""Qualitative eval harnesses for non-code factory outputs.

Structural/regex-based scoring — deterministic, fast, composable.
No LLM calls. Each function returns {"score": float, "details": {...}}.
"""

from __future__ import annotations

import re
from pathlib import Path


def eval_research_quality(research_path: Path) -> dict:
    """Score research output on coverage, depth, grounding, relevance.

    Structural checks (no LLM needed):
    - File exists and is non-empty (0 or 1)
    - Contains external references/links (0 or 1)
    - Has multiple sections/findings (count -> normalized score)
    - Length >= minimum threshold (0 or 1)
    """
    details: dict = {}
    scores: list[float] = []

    if not research_path.exists():
        return {"score": 0.0, "details": {"exists": False}}

    text = research_path.read_text()

    exists_and_nonempty = len(text.strip()) > 0
    details["exists"] = exists_and_nonempty
    scores.append(1.0 if exists_and_nonempty else 0.0)

    if not exists_and_nonempty:
        return {"score": 0.0, "details": details}

    links = re.findall(r"https?://[^\s\)]+", text)
    has_references = len(links) > 0
    details["has_references"] = has_references
    details["reference_count"] = len(links)
    scores.append(1.0 if has_references else 0.0)

    headings = re.findall(r"^#{1,4}\s+.+$", text, re.MULTILINE)
    section_count = len(headings)
    details["section_count"] = section_count
    section_score = min(section_count / 5.0, 1.0)
    scores.append(section_score)

    min_length = 500
    length = len(text)
    details["length"] = length
    details["min_length_threshold"] = min_length
    meets_length = length >= min_length
    details["meets_length"] = meets_length
    scores.append(1.0 if meets_length else 0.0)

    final_score = sum(scores) / len(scores)
    return {"score": round(final_score, 3), "details": details}


def eval_strategy_quality(strategy_path: Path) -> dict:
    """Score strategy output on specificity, eval impact, FEEC compliance, growth coverage.

    Structural checks (no LLM needed):
    - Has at least one hypothesis (0 or 1)
    - Each hypothesis has Category/What/Why/Expected impact (completeness score)
    - At least one growth dimension tagged (0 or 1)
    - No calendar-time estimates (penalty if found)
    """
    details: dict = {}
    scores: list[float] = []

    if not strategy_path.exists():
        return {"score": 0.0, "details": {"exists": False}}

    text = strategy_path.read_text()

    if not text.strip():
        return {"score": 0.0, "details": {"exists": True, "empty": True}}

    hypothesis_headers = re.findall(
        r"^#{1,4}\s+H\d+[:\s]|^#{1,4}\s+Hypothesis\s+\d+",
        text,
        re.MULTILINE,
    )
    has_hypotheses = len(hypothesis_headers) > 0
    details["hypothesis_count"] = len(hypothesis_headers)
    details["has_hypotheses"] = has_hypotheses
    scores.append(1.0 if has_hypotheses else 0.0)

    required_fields = ["Category", "What", "Why", "Expected impact"]
    found_fields = 0
    for field in required_fields:
        pattern = rf"\*\*{re.escape(field)}[:\*]"
        if re.search(pattern, text, re.IGNORECASE):
            found_fields += 1
    completeness = found_fields / len(required_fields) if required_fields else 0.0
    details["field_completeness"] = completeness
    details["found_fields"] = found_fields
    details["required_fields"] = len(required_fields)
    scores.append(completeness)

    growth_dimensions = [
        "capability_surface",
        "experiment_diversity",
        "observability",
        "research_grounding",
        "factory_effectiveness",
    ]
    growth_pattern = "|".join(re.escape(d) for d in growth_dimensions)
    growth_matches = re.findall(
        rf"\*\*Growth dimension[:\*].*?({growth_pattern})",
        text,
        re.IGNORECASE,
    )
    has_growth = len(growth_matches) > 0
    details["has_growth_dimension"] = has_growth
    details["growth_dimensions_found"] = growth_matches
    scores.append(1.0 if has_growth else 0.0)

    calendar_patterns = [
        r"\d+[-–]\d+\s+weeks?",
        r"\d+[-–]\d+\s+months?",
        r"\d+[-–]\d+\s+days?",
        r"timeline[:\s]",
        r"estimated\s+time",
        r"ETA[:\s]",
    ]
    calendar_found = any(
        re.search(p, text, re.IGNORECASE) for p in calendar_patterns
    )
    details["has_calendar_estimates"] = calendar_found
    penalty = -0.2 if calendar_found else 0.0
    details["calendar_penalty"] = penalty

    raw_score = sum(scores) / len(scores) if scores else 0.0
    final_score = max(0.0, min(1.0, raw_score + penalty))
    return {"score": round(final_score, 3), "details": details}
