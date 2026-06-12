"""Language evaluator registry — auto-detect and dispatch to language-specific evaluators."""

from __future__ import annotations

from pathlib import Path

from factory.eval.languages.base import EvalFragment, LanguageEvaluator
from factory.eval.languages.go import register_evaluator as _reg_go
from factory.eval.languages.node import register_evaluator as _reg_node
from factory.eval.languages.python import register_evaluator as _reg_python
from factory.eval.languages.rust import register_evaluator as _reg_rust

_REGISTRY: list[LanguageEvaluator] = []


def register(evaluator: LanguageEvaluator) -> None:
    _REGISTRY.append(evaluator)


def detect_languages(project_path: Path) -> list[LanguageEvaluator]:
    return [e for e in _REGISTRY if e.detect(project_path)]


def detect_primary_language(project_path: Path) -> str:
    for evaluator in _REGISTRY:
        if evaluator.detect(project_path):
            return evaluator.name
    return "unknown"


def _aggregate(fragments: list[EvalFragment], dimension: str) -> dict:
    from factory.eval.hygiene import HYGIENE_WEIGHTS

    if dimension == "tests":
        total_passed = sum(f.passed for f in fragments)
        total_failed = sum(f.failed for f in fragments)
        total = total_passed + total_failed
        score = total_passed / total if total > 0 else 0.0
        details = "; ".join(f.details for f in fragments) or f"{total_passed} passed, {total_failed} failed"
        return {
            "name": "tests",
            "score": round(score, 4),
            "weight": HYGIENE_WEIGHTS["tests"],
            "passed": total_failed == 0,
            "details": details,
        }

    if dimension == "lint":
        total_errors = sum(f.failed for f in fragments)
        score = max(0.0, 1.0 - total_errors * 0.1)
        details = "; ".join(f.details for f in fragments)
        return {
            "name": "lint",
            "score": round(score, 4),
            "weight": HYGIENE_WEIGHTS["lint"],
            "passed": total_errors == 0,
            "details": details,
        }

    if dimension == "type_check":
        total_errors = sum(f.failed for f in fragments)
        score = max(0.0, 1.0 - total_errors * 0.05)
        details = "; ".join(f.details for f in fragments)
        return {
            "name": "type_check",
            "score": round(score, 4),
            "weight": HYGIENE_WEIGHTS["type_check"],
            "passed": total_errors == 0,
            "details": details,
        }

    if dimension == "coverage":
        coverages = [(f.details, f.coverage_pct) for f in fragments if f.coverage_pct is not None]
        avg_pct = sum(pct for _, pct in coverages) / len(coverages) if coverages else 0.0
        score = avg_pct / 100.0
        details = ", ".join(f.details for f in fragments)
        return {
            "name": "coverage",
            "score": round(score, 4),
            "weight": HYGIENE_WEIGHTS["coverage"],
            "passed": avg_pct >= 80,
            "details": f"Coverage: {details} (threshold: 80%)",
        }

    raise ValueError(f"Unknown dimension: {dimension}")


register(_reg_python())
register(_reg_node())
register(_reg_rust())
register(_reg_go())

__all__ = [
    "EvalFragment",
    "LanguageEvaluator",
    "_aggregate",
    "detect_languages",
    "detect_primary_language",
    "register",
]
