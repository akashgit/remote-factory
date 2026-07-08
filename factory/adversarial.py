"""Adversarial (GAN-style) eval loop — phase-aware state management.

Alternates between optimizing a generator and discriminator, each scored
by its own metric.  Automatic phase switching when a component exceeds
its threshold for N consecutive rounds (hysteresis).  Convergence is
detected when both sides sustain above-threshold performance.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

import structlog

from factory.models import (
    AdversarialComponent,
    AdversarialConfig,
    AdversarialPhaseRecord,
    AdversarialState,
)

log = structlog.get_logger()

_STATE_FILE = "adversarial_state.json"


def _state_path(project_path: Path) -> Path:
    return project_path / ".factory" / _STATE_FILE


# ── state persistence ───────────────────────────────────────────


def load_adversarial_state(project_path: Path) -> AdversarialState:
    """Load state from .factory/adversarial_state.json, returning defaults if missing."""
    path = _state_path(project_path)
    if not path.exists():
        return AdversarialState()
    try:
        data = json.loads(path.read_text())
        return AdversarialState(**data)
    except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        log.warning("adversarial_state_corrupt", path=str(path), error=str(exc))
        return AdversarialState()


def save_adversarial_state(project_path: Path, state: AdversarialState) -> None:
    """Persist state to .factory/adversarial_state.json."""
    path = _state_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.model_dump(), indent=2) + "\n")
    log.debug("adversarial_state_saved", round=state.current_round, active=state.active_role)


def reset_adversarial_state(project_path: Path) -> None:
    """Delete the state file, resetting to defaults."""
    path = _state_path(project_path)
    if path.exists():
        path.unlink()
        log.info("adversarial_state_reset", path=str(path))


# ── phase queries ───────────────────────────────────────────────


def get_active_phase(state: AdversarialState) -> Literal["generator", "discriminator"]:
    """Return the currently active role."""
    return state.active_role


def get_active_component(
    config: AdversarialConfig,
    state: AdversarialState,
) -> AdversarialComponent:
    """Return the AdversarialComponent for the currently active phase."""
    if state.active_role == "generator":
        return config.generator
    return config.discriminator


# ── phase transition ────────────────────────────────────────────


def should_switch_phase(
    state: AdversarialState,
    config: AdversarialConfig,
    current_score: float,
) -> bool:
    """Check whether the active phase should switch.

    Returns True when the active component has scored at or above its
    threshold for ``config.hysteresis`` consecutive rounds.

    Does NOT mutate state — the caller updates counters.
    """
    component = get_active_component(config, state)
    if current_score < component.threshold:
        return False
    return (state.consecutive_above + 1) >= config.hysteresis


# ── convergence ─────────────────────────────────────────────────


def detect_convergence(
    state: AdversarialState,
    config: AdversarialConfig,
) -> bool:
    """Check if both sides have sustained above-threshold performance.

    Convergence requires both per-role consecutive counters to
    independently reach ``config.convergence_window``.
    """
    return (
        state.generator_consecutive_above >= config.convergence_window
        and state.discriminator_consecutive_above >= config.convergence_window
    )


# ── record + transition ────────────────────────────────────────


def record_phase_result(
    project_path: Path,
    config: AdversarialConfig,
    score: float,
) -> AdversarialPhaseRecord:
    """Record a phase result, potentially transitioning phases.

    1. Load state, increment round
    2. Update consecutive counters for the active role
    3. Switch phase if hysteresis threshold met
    4. Check convergence
    5. Save state and return the record
    """
    state = load_adversarial_state(project_path)
    state.current_round += 1

    component = get_active_component(config, state)
    active_role = state.active_role

    # Update counters
    if score >= component.threshold:
        state.consecutive_above += 1
        if active_role == "generator":
            state.generator_consecutive_above += 1
        else:
            state.discriminator_consecutive_above += 1
    else:
        state.consecutive_above = 0
        if active_role == "generator":
            state.generator_consecutive_above = 0
        else:
            state.discriminator_consecutive_above = 0

    # Phase switch check
    switched = state.consecutive_above >= config.hysteresis
    if switched:
        state.active_role = "discriminator" if active_role == "generator" else "generator"
        state.consecutive_above = 0
        log.info(
            "adversarial_phase_switch",
            from_role=active_role,
            to_role=state.active_role,
            round=state.current_round,
        )

    # Convergence check
    if not state.converged and detect_convergence(state, config):
        state.converged = True
        log.info("adversarial_converged", round=state.current_round)

    record = AdversarialPhaseRecord(
        round=state.current_round,
        active_role=active_role,
        score=score,
        metric_name=component.metric_name,
        timestamp=datetime.now().isoformat(),
        switched=switched,
    )
    state.history.append(record)

    save_adversarial_state(project_path, state)
    return record


# ── formatting ──────────────────────────────────────────────────


def format_adversarial_state(state: AdversarialState) -> str:
    """Human-readable summary for CLI output."""
    lines = [
        f"Active phase: {state.active_role}",
        f"Current round: {state.current_round}",
        f"Consecutive above threshold: {state.consecutive_above}",
        f"Generator streak: {state.generator_consecutive_above}",
        f"Discriminator streak: {state.discriminator_consecutive_above}",
        f"Converged: {state.converged}",
    ]

    if state.history:
        lines.append(f"\nHistory ({len(state.history)} entries):")
        for rec in state.history[-10:]:
            switch_marker = " [SWITCH]" if rec.switched else ""
            lines.append(
                f"  Round {rec.round}: {rec.active_role} "
                f"score={rec.score:.4f} ({rec.metric_name}){switch_marker}"
            )
        if len(state.history) > 10:
            lines.append(f"  ... ({len(state.history) - 10} earlier entries omitted)")

    return "\n".join(lines)
