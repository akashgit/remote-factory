"""CompressEvaluator — parses compression result artifacts and computes combined score."""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from factory.inner_loop import EvalResult

log = structlog.get_logger()


class CompressEvaluator:
    """Parses compression result JSON artifacts.

    Expected artifact schema:
        {compression_ratio, quality_retention, inference_latency, technique}
    """

    def __init__(
        self,
        compression_weight: float = 0.4,
        quality_weight: float = 0.5,
        latency_weight: float = 0.1,
    ) -> None:
        self.compression_weight = compression_weight
        self.quality_weight = quality_weight
        self.latency_weight = latency_weight

    def parse(self, artifact_path: Path) -> EvalResult:
        try:
            data = json.loads(Path(artifact_path).read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("compress_parse_failed", path=str(artifact_path))
            return EvalResult(score=0.0, valid=False)

        compression_ratio = data.get("compression_ratio")
        quality_retention = data.get("quality_retention")
        if compression_ratio is None or quality_retention is None:
            log.warning("compress_missing_fields", path=str(artifact_path))
            return EvalResult(score=0.0, valid=False)

        inference_latency = data.get("inference_latency", 0.0)
        score = self._compute_combined_score(
            float(compression_ratio),
            float(quality_retention),
            float(inference_latency),
        )

        metrics = {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
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
            if result.score > best.score:
                best = result
        return best

    def get_info(self) -> dict:
        return {
            "benchmark": "compression",
            "weights": {
                "compression_ratio": self.compression_weight,
                "quality_retention": self.quality_weight,
                "latency": self.latency_weight,
            },
            "metrics": [
                "compression_ratio",
                "quality_retention",
                "inference_latency",
                "technique",
            ],
        }

    def _compute_combined_score(
        self,
        compression_ratio: float,
        quality_retention: float,
        inference_latency: float,
    ) -> float:
        latency_penalty = 1.0 / (1.0 + inference_latency / 1000.0)
        return (
            compression_ratio * self.compression_weight
            + quality_retention * self.quality_weight
            - (1.0 - latency_penalty) * self.latency_weight
        )
