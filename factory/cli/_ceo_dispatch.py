"""CEO session dispatch — worktree setup, tailer management, session execution."""
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import structlog

log = structlog.get_logger()


def _start_ceo_tailer(
    wt_path: Path, cycle_span_id: str | None, start_time: float,
    on_line: Callable[[bytes], None] | None = None,
    is_headless: bool = False,
) -> object | None:
    """Create the CEO span eagerly and start a TranscriptTailer.

    When *is_headless* is True, skip span creation -- headless runs manage
    their own telemetry via the completion guard.
    """
    try:
        from factory.telemetry import TranscriptTailer, begin_span, flush, is_enabled

        trace_id = ""
        ceo_span_id = ""

        if cycle_span_id and is_enabled() and not is_headless:
            trace_id = os.environ.get("FACTORY_TRACE_ID", "")
            if trace_id:
                span = begin_span(trace_id, cycle_span_id, "ceo")
                if span:
                    ceo_span_id = span
                    flush()

        if not trace_id and not on_line:
            return None

        tailer = TranscriptTailer(
            trace_id=trace_id,
            span_id=ceo_span_id,
            project_path=wt_path,
            session_start=start_time,
            on_line=on_line,
        )
        tailer.start()
        return tailer
    except Exception:
        return None


def _stop_ceo_tailer(tailer: object | None) -> None:
    """Stop the tailer, drain remaining lines, and end the CEO span.

    Uses the observation object directly when available so that output
    metadata (line count) is attached before the span closes.
    """
    if tailer is None:
        return
    try:
        from factory.telemetry import _observations, end_span, flush

        count = tailer.stop_and_drain()  # type: ignore[attr-defined]
        span_id = getattr(tailer, "span_id", None)
        if span_id:
            obs = _observations.get(span_id)
            if obs is not None:
                obs.update(
                    output=f"CEO session completed ({count} observations ingested)",
                    metadata={"status": "completed", "observations_count": count},
                )
                obs.end()
                _observations.pop(span_id, None)
            else:
                trace_id = os.environ.get("FACTORY_TRACE_ID", "")
                end_span(trace_id, span_id, status="completed")
            flush()
    except Exception:
        pass
