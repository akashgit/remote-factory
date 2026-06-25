"""Extract verifiable acceptance criteria from parsed hypotheses."""

from __future__ import annotations

import re

from factory.plan_check.models import AcceptanceCriterion
from factory.plan_check.parser import ParsedHypothesis

_ARROW_RE = re.compile(r"(\w+)\s+[\d.]+\s*[→\->]+\s*([\d.]+)")
_PLUS_RE = re.compile(r"(\w+)\s+\+([\d.]+)")
_FILE_PATH_RE = re.compile(r"`([\w][\w.\-]*/[\w][\w.\-/]*\.\w+)`")
_FUNCTION_RE = re.compile(r"`(\w+)\(")
_TEST_NAME_RE = re.compile(r"`(test_\w+)`")


def extract_criteria(hypothesis: ParsedHypothesis) -> list[AcceptanceCriterion]:
    criteria: list[AcceptanceCriterion] = []
    criteria.extend(_extract_eval_targets(hypothesis))
    criteria.extend(_extract_file_deliverables(hypothesis))
    criteria.extend(_extract_function_deliverables(hypothesis))
    criteria.extend(_extract_test_requirements(hypothesis))
    return criteria


def _extract_eval_targets(hypothesis: ParsedHypothesis) -> list[AcceptanceCriterion]:
    if not hypothesis.expected_impact:
        return []

    criteria: list[AcceptanceCriterion] = []
    text = hypothesis.expected_impact
    seen: set[str] = set()

    for m in _ARROW_RE.finditer(text):
        dim = m.group(1)
        target_val = float(m.group(2))
        if dim not in seen:
            seen.add(dim)
            criteria.append(AcceptanceCriterion(
                criterion_id=f"{hypothesis.id}.eval.{dim}",
                hypothesis_id=hypothesis.id,
                criterion_type="eval_target",
                description=f"{dim} score reaches {target_val}",
                verification_method="eval_score",
                target={"dimension": dim, "min_expected": target_val},
            ))

    for m in _PLUS_RE.finditer(text):
        dim = m.group(1)
        delta = float(m.group(2))
        if dim not in seen:
            seen.add(dim)
            criteria.append(AcceptanceCriterion(
                criterion_id=f"{hypothesis.id}.eval.{dim}",
                hypothesis_id=hypothesis.id,
                criterion_type="eval_target",
                description=f"{dim} score improves by +{delta}",
                verification_method="eval_score",
                target={"dimension": dim, "delta": delta},
            ))

    return criteria


def _extract_file_deliverables(hypothesis: ParsedHypothesis) -> list[AcceptanceCriterion]:
    if not hypothesis.what:
        return []

    criteria: list[AcceptanceCriterion] = []
    seen: set[str] = set()

    for m in _FILE_PATH_RE.finditer(hypothesis.what):
        path = m.group(1)
        if path not in seen:
            seen.add(path)
            slug = path.replace("/", "_").replace(".", "_")
            criteria.append(AcceptanceCriterion(
                criterion_id=f"{hypothesis.id}.deliverable.{slug}",
                hypothesis_id=hypothesis.id,
                criterion_type="deliverable",
                description=f"{path} exists",
                verification_method="file_exists",
                target={"path": path},
            ))

    return criteria


def _extract_function_deliverables(hypothesis: ParsedHypothesis) -> list[AcceptanceCriterion]:
    if not hypothesis.what:
        return []

    criteria: list[AcceptanceCriterion] = []
    seen: set[str] = set()
    last_file_path = ""

    for line in hypothesis.what.split("\n"):
        file_matches = list(_FILE_PATH_RE.finditer(line))
        if file_matches:
            last_file_path = file_matches[-1].group(1)

        if "function" not in line.lower():
            continue

        for func_match in _FUNCTION_RE.finditer(line):
            func_name = func_match.group(1)
            if func_name in seen:
                continue
            seen.add(func_name)
            associated_path = last_file_path
            if file_matches:
                func_pos = func_match.start()
                best = file_matches[0]
                for fm in file_matches:
                    if fm.start() <= func_pos:
                        best = fm
                associated_path = best.group(1)
            criteria.append(AcceptanceCriterion(
                criterion_id=f"{hypothesis.id}.deliverable.{func_name}",
                hypothesis_id=hypothesis.id,
                criterion_type="deliverable",
                description=f"function {func_name} exists",
                verification_method="function_exists",
                target={"path": associated_path, "symbol": func_name},
            ))

    return criteria


def _extract_test_requirements(hypothesis: ParsedHypothesis) -> list[AcceptanceCriterion]:
    if not hypothesis.what:
        return []

    criteria: list[AcceptanceCriterion] = []
    seen: set[str] = set()

    for m in _TEST_NAME_RE.finditer(hypothesis.what):
        test_name = m.group(1)
        if test_name not in seen:
            seen.add(test_name)
            criteria.append(AcceptanceCriterion(
                criterion_id=f"{hypothesis.id}.test.{test_name}",
                hypothesis_id=hypothesis.id,
                criterion_type="test_requirement",
                description=f"test {test_name} passes",
                verification_method="test_passes",
                target={"test_name": test_name},
            ))

    return criteria


def parse_and_extract(
    content: str,
) -> list[tuple[ParsedHypothesis, list[AcceptanceCriterion]]]:
    from factory.plan_check.parser import parse_strategy_plan

    hypotheses = parse_strategy_plan(content)
    return [(h, extract_criteria(h)) for h in hypotheses]
