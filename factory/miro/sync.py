"""Sync orchestrator — full and incremental Miro board synchronization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from factory.events import emit_event
from factory.miro.analyzer import analyze
from factory.miro.board import BoardRenderer
from factory.miro.client import MiroClient
from factory.miro.drift import detect
from factory.store import ExperimentStore

log = structlog.get_logger()


async def sync_board(project_path: Path) -> dict[str, Any]:
    """Full board sync: analyze code, detect drift, render board.

    Non-blocking: Miro API failures are logged but never propagated.
    Returns a summary dict on success, empty dict on skip/failure.
    """
    project_path = project_path.resolve()
    store = ExperimentStore(project_path)

    try:
        config = await store.read_config()
    except (FileNotFoundError, ValueError):
        log.debug("miro_sync_skipped", reason="no valid config")
        return {}

    if not config.miro_board_id:
        log.debug("miro_sync_skipped", reason="no board_id configured")
        return {}

    client = MiroClient(project_path=project_path)
    if not client.available:
        log.debug("miro_sync_skipped", reason="client not available")
        return {}

    emit_event(project_path, "miro.sync.started", data={"sync_type": "full"})

    try:
        structure = analyze(project_path)
        drift_items = detect(structure, project_path)
        history_records = await store.load_history()
        history: list[dict[str, Any]] = [
            {
                "id": r.id,
                "hypothesis": r.hypothesis,
                "verdict": r.verdict,
                "delta": r.delta,
            }
            for r in history_records
        ]

        renderer = BoardRenderer(client, config.miro_board_id)
        summary = await renderer.render(
            structure,
            drift_items,
            history,
            {"goal": config.goal, "threshold": config.eval_threshold},
        )
        summary["drift_count"] = len(drift_items)

        emit_event(
            project_path,
            "miro.sync.completed",
            data={
                "items_created": summary.get("item_count", 0),
                "drift_count": len(drift_items),
            },
        )
        return summary
    except Exception as exc:
        log.error("miro_sync_failed", error=str(exc))
        emit_event(project_path, "miro.sync.failed", data={"error": str(exc)})
        return {}


async def update_experiment(project_path: Path, experiment_id: int) -> None:
    """Incremental update: refresh the board after a new experiment.

    Falls back to a full re-render via sync_board. Miro API failures
    are logged but never propagated.
    """
    project_path = project_path.resolve()
    store = ExperimentStore(project_path)

    try:
        config = await store.read_config()
    except (FileNotFoundError, ValueError):
        log.debug("miro_update_skipped", reason="no valid config")
        return

    if not config.miro_board_id:
        log.debug("miro_update_skipped", reason="no board_id")
        return

    client = MiroClient(project_path=project_path)
    if not client.available:
        return

    emit_event(
        project_path,
        "miro.sync.started",
        data={"sync_type": "incremental", "experiment_id": experiment_id},
    )

    try:
        history_records = await store.load_history()
        record = next((r for r in history_records if r.id == experiment_id), None)
        if not record:
            log.debug("miro_update_no_record", experiment_id=experiment_id)
            return

        await sync_board(project_path)
        emit_event(
            project_path,
            "miro.sync.completed",
            data={"sync_type": "incremental", "experiment_id": experiment_id},
        )
    except Exception as exc:
        log.error("miro_update_failed", error=str(exc))
        emit_event(project_path, "miro.sync.failed", data={"error": str(exc)})
