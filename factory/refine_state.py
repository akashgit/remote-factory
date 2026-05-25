"""Post-cycle refinement state tracking and identity regrounding."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import structlog
from pydantic import ValidationError

from factory.agents.runner import IDENTITY_REANCHOR
from factory.models import RefinementEntry, RefinementState

log = structlog.get_logger()

_STATE_DIR = "state"
_STATE_FILE = "refinements.json"


def _state_path(project_path: Path) -> Path:
    return project_path / ".factory" / _STATE_DIR / _STATE_FILE


def read_state(project_path: Path) -> RefinementState:
    """Read refinement state from disk. Returns empty state if file missing or corrupted."""
    path = _state_path(project_path)
    if not path.exists():
        return RefinementState()
    try:
        data = json.loads(path.read_text())
        return RefinementState(**data)
    except (json.JSONDecodeError, ValidationError) as exc:
        log.warning("corrupted_refinement_state", path=str(path), error=str(exc))
        return RefinementState()


def begin_refinement(project_path: Path, request: str) -> RefinementEntry:
    """Create a new refinement entry and persist to disk."""
    state = read_state(project_path)
    sequence = len(state.entries) + 1
    entry = RefinementEntry(
        sequence=sequence,
        request=request,
        started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    state.entries.append(entry)
    path = _state_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.model_dump(), indent=2) + "\n")
    return entry


def complete_refinement(project_path: Path, verdict: str) -> bool:
    """Update the last refinement entry with verdict and completion timestamp.

    Returns True if state was mutated, False if already completed or no entries.
    """
    state = read_state(project_path)
    if not state.entries:
        return False
    last = state.entries[-1]
    if last.verdict is not None:
        log.warning("refinement_already_completed", sequence=last.sequence, existing_verdict=last.verdict)
        return False
    last.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    last.verdict = verdict
    path = _state_path(project_path)
    path.write_text(json.dumps(state.model_dump(), indent=2) + "\n")
    return True


def format_status(state: RefinementState) -> str:
    """Produce structured regrounding output for the CEO."""
    lines = [
        "═══ REFINEMENT STATUS ═══",
        "Role: Factory CEO — Executive Orchestrator (refinement router)",
        "Sacred Rule 8: ACTIVE — do NOT implement changes directly",
        "Sacred Rule 9: ACTIVE — full review pipeline for every refinement",
        "",
    ]

    if not state.entries:
        lines.append("No refinements recorded yet. Entering post-cycle refinement loop.")
        lines.append("Route ALL change requests through Refiner → Builder (Sacred Rule 8).")
    else:
        lines.append(f"Refinements recorded: {len(state.entries)}")
        for e in state.entries:
            if e.verdict:
                lines.append(f'  #{e.sequence}: "{e.request}" → {e.verdict.upper()}')
            else:
                lines.append(f'  #{e.sequence}: "{e.request}" → IN PROGRESS')
        lines.append("")
        lines.append("REMINDER: Route ALL change requests through Refiner → Builder.")
        lines.append("Questions and approvals may be handled directly.")

    lines.append("═══════════════════════════")
    lines.append(IDENTITY_REANCHOR)
    return "\n".join(lines)


def format_begin(entry: RefinementEntry) -> str:
    """Produce begin output with regrounding for the CEO."""
    lines = [
        f'Refinement #{entry.sequence} registered: "{entry.request}"',
        "You are the CEO. Proceed with Mode: Refine pipeline (R0-R12).",
        "Do NOT implement this yourself — spawn the Refiner agent.",
    ]

    count = entry.sequence
    if count >= 10:
        lines.append("")
        lines.append(
            f"⚠ Advisory: This is refinement #{count}. Consider starting a fresh session."
        )
        lines.append(
            "Context window is significantly loaded. Quality of agent outputs may be affected."
        )
    elif count >= 5:
        lines.append("")
        lines.append(
            f"⚠ Advisory: This is refinement #{count} in this session."
        )
        lines.append(
            "Extended sessions may experience quality degradation from context growth."
        )
        lines.append("This is an advisory — you may continue as long as needed.")

    lines.append(IDENTITY_REANCHOR)
    return "\n".join(lines)
