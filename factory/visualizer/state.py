"""State inference from factory event streams.

Replays events.jsonl entries to compute the current live state of a factory
project — active agents, pipeline phase, operating mode, and current experiment.
Nothing is persisted; state is always recomputed from events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PHASES = [
    "Detect",
    "Discover",
    "Research",
    "Strategize",
    "Build",
    "Review",
    "Eval",
    "Archive",
]

# Mode-specific phase definitions: (display_name, builder_key, is_loop_phase)
MODE_PHASES: dict[str, list[tuple[str, str, bool]]] = {
    "improve": [
        ("Observe", "research", False),
        ("Hypothesize", "strategize", False),
        ("Build", "build", True),
        ("Review", "review", True),
        ("Eval", "eval", True),
        ("Archive", "archive", False),
    ],
    "research": [
        ("Baseline", "eval", False),
        ("Analyze", "research", False),
        ("Research", "research", False),
        ("Hypothesize", "strategize", False),
        ("Build", "build", True),
        ("Run", "eval", True),
        ("Archive", "archive", False),
    ],
    "build": [
        ("Research", "research", False),
        ("Plan", "strategize", False),
        ("Build", "build", True),
        ("Verify", "eval", False),
        ("Archive", "archive", False),
    ],
    "discover": [
        ("Detect", "detect", False),
        ("Discover", "discover", False),
    ],
    "meta": [
        ("Observe", "research", False),
        ("Hypothesize", "strategize", False),
        ("Build", "build", True),
        ("Review", "review", True),
        ("Eval", "eval", True),
        ("Archive", "archive", False),
        ("ACE", "archive", False),
    ],
}

MODE_AGENT_TO_PHASE: dict[str, dict[str, str]] = {
    "improve": {
        "researcher": "Observe",
        "strategist": "Hypothesize",
        "builder": "Build",
        "reviewer": "Review",
        "evaluator": "Eval",
        "archivist": "Archive",
    },
    "research": {
        "failure_analyst": "Analyze",
        "researcher": "Research",
        "strategist": "Hypothesize",
        "builder": "Build",
        "evaluator": "Run",
        "archivist": "Archive",
    },
    "build": {
        "researcher": "Research",
        "strategist": "Plan",
        "builder": "Build",
        "evaluator": "Verify",
        "archivist": "Archive",
    },
    "discover": {
        "researcher": "Discover",
    },
    "meta": {
        "researcher": "Observe",
        "strategist": "Hypothesize",
        "builder": "Build",
        "reviewer": "Review",
        "evaluator": "Eval",
        "archivist": "Archive",
    },
}

MODE_EVENT_TO_PHASE: dict[str, dict[str, str]] = {
    "improve": {
        "study.started": "Observe",
        "study.completed": "Observe",
        "insights.started": "Observe",
        "insights.completed": "Observe",
        "eval.started": "Eval",
        "eval.completed": "Eval",
        "guard.completed": "Eval",
        "archive.completed": "Archive",
        "ace.started": "Archive",
        "ace.completed": "Archive",
    },
    "research": {
        "eval.started": "Run",
        "eval.completed": "Run",
        "guard.completed": "Run",
        "archive.completed": "Archive",
    },
    "build": {
        "study.started": "Research",
        "study.completed": "Research",
        "eval.started": "Verify",
        "eval.completed": "Verify",
        "archive.completed": "Archive",
    },
    "discover": {
        "detect": "Detect",
        "discover.started": "Discover",
        "discover.completed": "Discover",
    },
    "meta": {
        "study.started": "Observe",
        "study.completed": "Observe",
        "insights.started": "Observe",
        "insights.completed": "Observe",
        "eval.started": "Eval",
        "eval.completed": "Eval",
        "guard.completed": "Eval",
        "archive.completed": "Archive",
        "ace.started": "ACE",
        "ace.completed": "ACE",
    },
}

# Generic fallbacks (used when mode is unknown)
_EVENT_TO_PHASE: dict[str, str] = {
    "discover.started": "Discover",
    "discover.completed": "Discover",
    "study.started": "Research",
    "study.completed": "Research",
    "insights.started": "Research",
    "insights.completed": "Research",
    "eval.started": "Eval",
    "eval.completed": "Eval",
    "guard.completed": "Eval",
    "archive.completed": "Archive",
    "ace.started": "Archive",
    "ace.completed": "Archive",
}

_AGENT_TO_PHASE: dict[str, str] = {
    "researcher": "Research",
    "strategist": "Strategize",
    "builder": "Build",
    "reviewer": "Review",
    "evaluator": "Eval",
    "archivist": "Archive",
}


def get_phases_for_mode(mode: str | None) -> list[str]:
    """Return the phase display-name list for a mode, falling back to PHASES."""
    mode_lower = (mode or "").lower()
    defs = MODE_PHASES.get(mode_lower)
    if defs:
        return [p[0] for p in defs]
    return PHASES


def infer_mode_from_artifacts(factory_dir: Path) -> str | None:
    """Infer mode from .factory/ artifacts when no events are available."""
    if not factory_dir.exists():
        return None
    config_path = factory_dir / "config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            if config.get("research_target") is not None:
                return "research"
        except (json.JSONDecodeError, OSError):
            pass
        return "improve"
    if (factory_dir / "eval_profile.json").exists():
        return "discover"
    return None


@dataclass
class AgentActivity:
    role: str
    task: str
    started_at: str


@dataclass
class FactoryLiveState:
    active_agents: dict[str, AgentActivity] = field(default_factory=dict)
    current_phase: str | None = None
    current_mode: str | None = None
    current_experiment: dict[str, Any] | None = None
    hypothesis_number: int = 0

    def to_dict(self) -> dict[str, Any]:
        mode_lower = (self.current_mode or "").lower()
        phase_defs = MODE_PHASES.get(mode_lower)
        phases_list = [p[0] for p in phase_defs] if phase_defs else PHASES
        loop_phases = [p[0] for p in phase_defs if p[2]] if phase_defs else []
        return {
            "active_agents": {
                role: {"role": a.role, "task": a.task, "started_at": a.started_at}
                for role, a in self.active_agents.items()
            },
            "current_phase": self.current_phase,
            "current_mode": self.current_mode,
            "current_experiment": self.current_experiment,
            "hypothesis_number": self.hypothesis_number,
            "phases": phases_list,
            "loop_phases": loop_phases,
        }


def infer_state(events: list[dict[str, Any]]) -> FactoryLiveState:
    """Replay a list of events to compute the current live state."""
    state = FactoryLiveState()
    for event in events:
        state = update_state(state, event)
    return state


def update_state(state: FactoryLiveState, event: dict[str, Any]) -> FactoryLiveState:
    """Apply a single event to update the live state."""
    event_type = event.get("type", "")
    agent = event.get("agent")
    data = event.get("data") or {}
    timestamp = event.get("timestamp", "")

    # --- Mode detection first (affects phase lookups below) ---
    if event_type == "cycle.started":
        mode = data.get("mode")
        if mode:
            state.current_mode = mode
        state.hypothesis_number = 0

    if event_type == "detect":
        detected = data.get("state", "")
        mode_map = {
            "new": "Build",
            "init": "Build",
            "discovered": "Improve",
            "running": "Improve",
            "stale": "Improve",
        }
        inferred = mode_map.get(detected)
        if inferred and not state.current_mode:
            state.current_mode = inferred
        if state.current_phase is None:
            mode_phases = get_phases_for_mode(state.current_mode)
            state.current_phase = mode_phases[0]

    # --- Resolve mode-specific lookup tables ---
    mode_lower = (state.current_mode or "").lower()
    agent_phase_map = MODE_AGENT_TO_PHASE.get(mode_lower, _AGENT_TO_PHASE)
    event_phase_map = MODE_EVENT_TO_PHASE.get(mode_lower, _EVENT_TO_PHASE)

    # --- Agent tracking ---
    if event_type == "agent.started" and agent:
        state.active_agents[agent] = AgentActivity(
            role=agent,
            task=(data.get("task") or "")[:100],
            started_at=timestamp,
        )
        phase = agent_phase_map.get(agent)
        if phase:
            state.current_phase = phase

    elif event_type in ("agent.completed", "agent.failed", "agent.timeout") and agent:
        state.active_agents.pop(agent, None)

    elif event_type in event_phase_map:
        state.current_phase = event_phase_map[event_type]

    # --- Experiment / hypothesis tracking ---
    if event_type == "experiment.begin":
        state.hypothesis_number += 1
        state.current_experiment = {
            "id": data.get("exp_id"),
            "hypothesis": data.get("hypothesis", ""),
            "hypothesis_number": state.hypothesis_number,
        }

    elif event_type == "experiment.finalize":
        state.current_experiment = None

    return state


def phase_index(phase: str | None, mode: str | None = None) -> int:
    """Return the 0-based index of a phase in the mode's pipeline, or -1."""
    if phase is None:
        return -1
    phases = get_phases_for_mode(mode)
    try:
        return phases.index(phase)
    except ValueError:
        return -1


def completed_phases(state: FactoryLiveState) -> list[str]:
    """Return the list of phases completed before the current one."""
    phases = get_phases_for_mode(state.current_mode)
    idx = phase_index(state.current_phase, state.current_mode)
    if idx <= 0:
        return []
    return phases[:idx]


def active_agent_count(state: FactoryLiveState) -> int:
    """Return the number of currently active agents."""
    return len(state.active_agents)


def format_elapsed(started_at: str) -> str:
    """Format elapsed time since started_at as a human-readable string."""
    from datetime import datetime, timezone

    if not started_at:
        return "0s"
    try:
        start = datetime.fromisoformat(started_at)
    except (ValueError, TypeError):
        return "0s"
    now = datetime.now(timezone.utc)
    elapsed = int((now - start).total_seconds())
    if elapsed < 0:
        elapsed = 0
    if elapsed < 60:
        return f"{elapsed}s"
    minutes, seconds = divmod(elapsed, 60)
    return f"{minutes}m{seconds}s"
