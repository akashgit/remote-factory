"""Append-only progress tracking for outer loop observability."""

from __future__ import annotations

import json
import time
from pathlib import Path

import structlog

log = structlog.get_logger()

_ISO_FMT = "%Y-%m-%dT%H:%M:%S"


class ProgressTracker:
    """Writes structured events to a JSONL file for live monitoring via tail -f."""

    def __init__(self, progress_dir: Path) -> None:
        self._dir = progress_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "progress.jsonl"

    @property
    def path(self) -> Path:
        return self._path

    def _emit(self, event: dict[str, object]) -> None:
        event["timestamp"] = time.strftime(_ISO_FMT, time.gmtime())
        line = json.dumps(event, default=str)
        with self._path.open("a") as f:
            f.write(line + "\n")

    def generation_start(self, generation: int, budget_remaining: int) -> None:
        self._emit({
            "event_type": "generation_start",
            "generation": generation,
            "budget_remaining": budget_remaining,
        })

    def generation_complete(
        self,
        generation: int,
        best_score: float,
        mean_score: float,
        duration_seconds: float,
    ) -> None:
        self._emit({
            "event_type": "generation_complete",
            "generation": generation,
            "best_score": best_score,
            "mean_score": mean_score,
            "duration_seconds": round(duration_seconds, 2),
        })

    def agent_start(
        self,
        generation: int,
        instance_id: str,
        workflow_id: str,
        node_id: str,
    ) -> None:
        self._emit({
            "event_type": "agent_start",
            "generation": generation,
            "instance_id": instance_id,
            "workflow_id": workflow_id,
            "node_id": node_id,
        })

    def agent_complete(
        self,
        generation: int,
        instance_id: str,
        workflow_id: str,
        node_id: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        self._emit({
            "event_type": "agent_complete",
            "generation": generation,
            "instance_id": instance_id,
            "workflow_id": workflow_id,
            "node_id": node_id,
            "status": status,
            "duration_seconds": round(duration_seconds, 2),
        })

    def eval_start(
        self,
        generation: int,
        workflow_id: str,
        instance_id: str,
    ) -> None:
        self._emit({
            "event_type": "eval_start",
            "generation": generation,
            "workflow_id": workflow_id,
            "instance_id": instance_id,
        })

    def eval_complete(
        self,
        generation: int,
        workflow_id: str,
        instance_id: str,
        score: float,
        status: str,
        duration_seconds: float,
    ) -> None:
        self._emit({
            "event_type": "eval_complete",
            "generation": generation,
            "workflow_id": workflow_id,
            "instance_id": instance_id,
            "score": score,
            "status": status,
            "duration_seconds": round(duration_seconds, 2),
        })

    def checkpoint_saved(self, generation: int, path: str) -> None:
        self._emit({
            "event_type": "checkpoint_saved",
            "generation": generation,
            "path": path,
        })

    def timeout_event(
        self,
        generation: int,
        instance_id: str,
        node_id: str,
        original_timeout: int,
        retry: bool,
    ) -> None:
        self._emit({
            "event_type": "timeout",
            "generation": generation,
            "instance_id": instance_id,
            "node_id": node_id,
            "original_timeout": original_timeout,
            "retry": retry,
        })
