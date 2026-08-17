"""CompressInnerLoop — inner loop for model compression research."""

from __future__ import annotations

from pathlib import Path

import structlog

from factory.compress.evaluator import CompressEvaluator
from factory.inner_loop import InnerLoop
from factory.workflow.primitives import Workflow

log = structlog.get_logger()

_DEFAULT_FROZEN_NODES: frozenset[str] = frozenset({
    "study",
    "gate_research",
    "gate_strategy",
    "gate_build",
    "gate_qa",
    "gate_doc_freshness",
    "gate_precheck",
    "finalize",
})


class CompressInnerLoop(InnerLoop):
    """Inner loop for model compression research.

    Inherits step/collect/history from InnerLoop. Adds compression-specific
    tracking: technique history, best technique, compression trajectory.
    """

    def __init__(
        self,
        project_dir: Path,
        evaluator: CompressEvaluator | None = None,
        workflow: Workflow | None = None,
        frozen_nodes: frozenset[str] | None = None,
    ) -> None:
        if evaluator is None:
            evaluator = CompressEvaluator()
        if frozen_nodes is None:
            frozen_nodes = _DEFAULT_FROZEN_NODES
        super().__init__(
            project_dir=project_dir,
            mode="compress",
            evaluator=evaluator,
            workflow=workflow,
            frozen_nodes=frozen_nodes,
        )

    def technique_history(self) -> list[dict]:
        """Per-cycle {cycle, technique, score} from eval artifacts."""
        results: list[dict] = []
        for record in self._history:
            technique = None
            score = record.score_end
            if record.experiments:
                for exp in record.experiments:
                    for artifact_path in exp.eval_artifacts:
                        technique = self._extract_technique(Path(artifact_path))
                        if technique:
                            break
                    if technique:
                        break
            results.append({
                "cycle": record.cycle_number,
                "technique": technique,
                "score": score,
            })
        return results

    def best_technique(self) -> dict | None:
        """Highest-scoring technique from history."""
        history = self.technique_history()
        if not history:
            return None
        scored = [h for h in history if h["score"] is not None]
        if not scored:
            return None
        return max(scored, key=lambda h: h["score"])

    def compression_trajectory(self) -> list[dict]:
        """Per-cycle {ratio, quality, technique} from eval artifacts."""
        results: list[dict] = []
        for record in self._history:
            ratio = None
            quality = None
            technique = None
            if record.experiments:
                for exp in record.experiments:
                    for artifact_path in exp.eval_artifacts:
                        data = self._read_artifact(Path(artifact_path))
                        if data:
                            ratio = data.get("compression_ratio")
                            quality = data.get("quality_retention")
                            technique = data.get("technique")
                            break
                    if ratio is not None:
                        break
            results.append({
                "ratio": ratio,
                "quality": quality,
                "technique": technique,
            })
        return results

    @staticmethod
    def _extract_technique(artifact_path: Path) -> str | None:
        try:
            import json
            data = json.loads(artifact_path.read_text())
            return data.get("technique")
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _read_artifact(artifact_path: Path) -> dict | None:
        try:
            import json
            return json.loads(artifact_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
