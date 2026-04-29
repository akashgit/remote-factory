"""Hook-driven checkpoint reconstruction from disk artifacts.

Reconstructs CheckpointState by reading events.jsonl, reviews, experiments,
and strategy files. Called by Claude Code hooks and the heartbeat loop.
Idempotent — calling N times produces the same result.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import structlog

from factory.checkpoint import CheckpointState, save_checkpoint

log = structlog.get_logger()

AGENT_ROLES = ("researcher", "strategist", "builder", "reviewer", "evaluator")


def _load_recent_events(project_path: Path) -> list[dict]:
    """Load events from the current cycle (since last cycle.started or all)."""
    events_file = project_path / ".factory" / "events.jsonl"
    if not events_file.exists():
        return []

    all_events: list[dict] = []
    for line in events_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            all_events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    last_cycle_idx = -1
    for i, event in enumerate(all_events):
        if event.get("type") == "cycle.started":
            last_cycle_idx = i

    if last_cycle_idx >= 0:
        return all_events[last_cycle_idx:]
    return all_events


def _detect_completed_agents(project_path: Path, events: list[dict]) -> list[str]:
    """Determine which agent roles have completed from events and review files."""
    completed = []
    reviews_dir = project_path / ".factory" / "reviews"

    for role in AGENT_ROLES:
        agent_completed = any(
            e.get("type") == "agent.completed" and e.get("agent") == role
            for e in events
        )
        review_exists = (reviews_dir / f"{role}-latest.md").exists()
        verdict_exists = (reviews_dir / f"ceo-verdict-{role}.md").exists()

        if agent_completed or review_exists or verdict_exists:
            completed.append(role)

    return completed


def _detect_active_experiment(project_path: Path) -> tuple[int | None, str | None, list[int]]:
    """Find the active experiment (has hypothesis but no verdict) and completed ones."""
    experiments_dir = project_path / ".factory" / "experiments"
    if not experiments_dir.exists():
        return None, None, []

    active_id: int | None = None
    active_hypothesis: str | None = None
    completed_ids: list[int] = []

    for exp_dir in sorted(experiments_dir.iterdir()):
        if not exp_dir.is_dir():
            continue
        try:
            exp_id = int(exp_dir.name)
        except ValueError:
            continue

        has_hypothesis = (exp_dir / "hypothesis.md").exists()
        has_verdict = (exp_dir / "verdict.json").exists()

        if has_hypothesis and has_verdict:
            completed_ids.append(exp_id)
        elif has_hypothesis and not has_verdict:
            active_id = exp_id
            active_hypothesis = (exp_dir / "hypothesis.md").read_text().strip()

    return active_id, active_hypothesis, completed_ids


def _get_last_eval_scores(project_path: Path, events: list[dict]) -> dict[str, float]:
    """Extract the most recent eval scores from events."""
    last_scores: dict[str, float] = {}
    for event in events:
        if event.get("type") == "eval.completed":
            data = event.get("data", {})
            if "composite" in data:
                last_scores["composite"] = data["composite"]
            if "dimensions" in data:
                last_scores["dimensions"] = data["dimensions"]
    return last_scores


def _detect_mode(project_path: Path) -> str:
    """Detect the current operating mode from config or existing checkpoint."""
    checkpoint_path = project_path / ".factory" / "checkpoint.json"
    if checkpoint_path.exists():
        try:
            data = json.loads(checkpoint_path.read_text())
            return data.get("mode", "improve")
        except (json.JSONDecodeError, KeyError):
            pass
    return "improve"


def reconstruct_state(project_path: Path) -> CheckpointState:
    """Reconstruct CheckpointState from disk truth sources."""
    events = _load_recent_events(project_path)
    completed_agents = _detect_completed_agents(project_path, events)
    active_id, hypothesis, completed_ids = _detect_active_experiment(project_path)
    scores = _get_last_eval_scores(project_path, events)
    mode = _detect_mode(project_path)

    # Only compute pending agents if a cycle is actively in progress
    cycle_active = any(
        e.get("type") in ("cycle.started", "agent.started", "experiment.begin")
        for e in events
    )
    pending = [r for r in AGENT_ROLES if r not in completed_agents] if cycle_active else []

    return CheckpointState(
        mode=mode,
        active_experiment_id=active_id,
        completed_agents=completed_agents,
        pending_agents=pending,
        last_eval_scores=scores,
        current_hypothesis=hypothesis,
        completed_hypotheses=completed_ids,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def reconstruct_and_save(project_path: Path) -> CheckpointState | None:
    """Reconstruct state and save checkpoint. Returns the state or None on error."""
    factory_dir = project_path / ".factory"
    if not factory_dir.is_dir():
        return None
    try:
        state = reconstruct_state(project_path)
        save_checkpoint(project_path, state)
        log.info("checkpoint_hook.saved", project=str(project_path))
        return state
    except Exception as exc:
        log.warning("checkpoint_hook.error", error=str(exc))
        return None


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m factory.checkpoint_hook <project_path>", file=sys.stderr)
        sys.exit(1)

    project_path = Path(sys.argv[1])
    result = reconstruct_and_save(project_path)
    sys.exit(0 if result is not None else 1)
