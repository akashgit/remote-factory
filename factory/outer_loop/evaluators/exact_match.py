"""Exact match evaluator — compare output to expected answer.

Used by math benchmarks like AIME where the answer is a single value
that must match exactly (after optional regex extraction).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from factory.inner_loop import EvalResult


class ExactMatchEvaluator:
    """Compares extracted output to expected answer.

    If answer_extraction regex is provided, applies it to extract the
    answer from the output (e.g. r"\\boxed{(\\d+)}" for LaTeX math).
    Score 1.0 on match, 0.0 otherwise.
    """

    def __init__(
        self,
        answer_extraction: str = "",
        **kwargs: object,
    ) -> None:
        self.answer_extraction = answer_extraction
        self._pattern: re.Pattern[str] | None = None
        if answer_extraction:
            try:
                self._pattern = re.compile(answer_extraction)
            except re.error:
                self._pattern = None

    def parse(self, artifact_path: Path) -> EvalResult:
        try:
            data = json.loads(Path(artifact_path).read_text(errors="replace"))
        except (json.JSONDecodeError, OSError):
            return EvalResult(score=0.0, valid=False)

        output = str(data.get("output", ""))
        expected = str(data.get("expected", ""))

        if not expected:
            return EvalResult(score=0.0, valid=False)

        extracted = self._extract_answer(output)
        match = extracted.strip() == expected.strip()
        score = 1.0 if match else 0.0

        return EvalResult(
            score=score,
            metrics={"match": score, "extracted": 1.0 if extracted != output else 0.0},
            valid=True,
            artifacts=[str(artifact_path)],
        )

    def parse_many(self, artifact_paths: list[Path]) -> EvalResult:
        if not artifact_paths:
            return EvalResult(score=0.0, valid=False)
        total = 0
        correct = 0
        for p in artifact_paths:
            result = self.parse(p)
            if result.valid:
                total += 1
                if result.score > 0:
                    correct += 1
        if total == 0:
            return EvalResult(score=0.0, valid=False)
        score = correct / total
        return EvalResult(
            score=score,
            metrics={"correct": float(correct), "total": float(total), "accuracy": score},
            valid=True,
        )

    def get_info(self) -> dict:
        return {
            "test_format": "exact_match",
            "scoring": "exact_match",
            "answer_extraction": self.answer_extraction,
        }

    def _extract_answer(self, text: str) -> str:
        if self._pattern is None:
            return text
        match = self._pattern.search(text)
        if match and match.groups():
            return match.group(1)
        if match:
            return match.group(0)
        return text
