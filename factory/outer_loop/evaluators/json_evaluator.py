"""JSON evaluator — extract a metric from JSON output.

Used by benchmarks that produce structured JSON results with a
configurable metric path (e.g. "pass_rate" or "stats.resolve_rate").
"""

from __future__ import annotations

import json
from pathlib import Path

from factory.inner_loop import EvalResult


class JSONEvaluator:
    """Extracts a numeric metric from JSON output via a dotted path.

    The metric_path supports dotted notation for nested fields:
    e.g. "stats.resolve_rate" reads data["stats"]["resolve_rate"].
    """

    def __init__(self, metric_path: str = "score", **kwargs: object) -> None:
        self.metric_path = metric_path

    def parse(self, artifact_path: Path) -> EvalResult:
        try:
            data = json.loads(Path(artifact_path).read_text(errors="replace"))
        except (json.JSONDecodeError, OSError):
            return EvalResult(score=0.0, valid=False)

        value = self._extract(data, self.metric_path)
        if value is None:
            return EvalResult(score=0.0, valid=False)

        try:
            score = float(value)
        except (TypeError, ValueError):
            return EvalResult(score=0.0, valid=False)

        metrics: dict[str, float] = {self.metric_path: score}
        for k, v in data.items():
            if isinstance(v, (int, float)) and k != self.metric_path:
                metrics[k] = float(v)

        return EvalResult(
            score=score,
            metrics=metrics,
            valid=True,
            artifacts=[str(artifact_path)],
        )

    def parse_many(self, artifact_paths: list[Path]) -> EvalResult:
        best = EvalResult(score=0.0, valid=False)
        for p in artifact_paths:
            result = self.parse(p)
            if result.valid and result.score > best.score:
                best = result
        return best

    def get_info(self) -> dict:
        return {
            "test_format": "json",
            "scoring": "metric_extraction",
            "metric_path": self.metric_path,
        }

    @staticmethod
    def _extract(data: dict, path: str) -> object:
        parts = path.split(".")
        current: object = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current
