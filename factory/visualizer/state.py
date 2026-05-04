"""State inference from factory event streams.

Replays events.jsonl entries to compute the current live state of a factory
project — active agents, pipeline phase, operating mode, and current experiment.
Nothing is persisted; state is always recomputed from events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

_EVENT_TO_PHASE: dict[str, str] = {
    "detect": "Detect",
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
    "distiller": "Research",
}


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_agents": {
                role: {"role": a.role, "task": a.task, "started_at": a.started_at}
                for role, a in self.active_agents.items()
            },
            "current_phase": self.current_phase,
            "current_mode": self.current_mode,
            "current_experiment": self.current_experiment,
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

    if event_type == "agent.started" and agent:
        state.active_agents[agent] = AgentActivity(
            role=agent,
            task=(data.get("task") or "")[:100],
            started_at=timestamp,
        )
        phase = _AGENT_TO_PHASE.get(agent)
        if phase:
            state.current_phase = phase

    elif event_type in ("agent.completed", "agent.failed", "agent.timeout") and agent:
        state.active_agents.pop(agent, None)

    elif event_type in _EVENT_TO_PHASE:
        state.current_phase = _EVENT_TO_PHASE[event_type]

    if event_type == "experiment.begin":
        state.current_experiment = {
            "id": data.get("exp_id"),
            "hypothesis": data.get("hypothesis", ""),
        }

    elif event_type == "experiment.finalize":
        state.current_experiment = None

    if event_type == "cycle.started":
        mode = data.get("mode")
        if mode:
            state.current_mode = mode

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

    return state
