"""Pluggable test output parsers for multi-benchmark support.

Each evaluator implements the Evaluator protocol from factory.inner_loop,
parsing output artifacts produced by test_command subprocess execution.
"""

from __future__ import annotations

from factory.inner_loop import Evaluator
from factory.outer_loop.evaluators.pytest_evaluator import PytestEvaluator
from factory.outer_loop.evaluators.exit_code import ExitCodeEvaluator
from factory.outer_loop.evaluators.json_evaluator import JSONEvaluator
from factory.outer_loop.evaluators.exact_match import ExactMatchEvaluator

_REGISTRY: dict[str, type[Evaluator]] = {
    "pytest": PytestEvaluator,
    "exit_code": ExitCodeEvaluator,
    "json": JSONEvaluator,
    "exact_match": ExactMatchEvaluator,
}


def get_evaluator(test_format: str, **kwargs: object) -> Evaluator:
    """Create an evaluator for the given test output format.

    Raises ValueError for unknown formats.
    """
    cls = _REGISTRY.get(test_format)
    if cls is None:
        raise ValueError(
            f"Unknown test_format {test_format!r}. "
            f"Available: {sorted(_REGISTRY.keys())}"
        )
    return cls(**kwargs)  # type: ignore[arg-type]


def list_formats() -> list[str]:
    """Return all registered test format names."""
    return sorted(_REGISTRY.keys())


__all__ = [
    "ExactMatchEvaluator",
    "ExitCodeEvaluator",
    "JSONEvaluator",
    "PytestEvaluator",
    "get_evaluator",
    "list_formats",
]
