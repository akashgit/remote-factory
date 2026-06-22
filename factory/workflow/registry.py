"""Skill registry — discovers, indexes, and selects workflow skills."""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import WorkflowSkill


class SkillRegistry:
    """Registry of workflow skills with auto-discovery and selection."""

    def __init__(self) -> None:
        self._skills: dict[str, WorkflowSkill] = {}
        self._alias_map: dict[str, str] = {}

    def _discover_builtin(self) -> None:
        """Load all built-in workflows from definitions.py as WorkflowSkill objects."""
        from factory.workflow.definitions import register_all

        skills = register_all()
        for name, skill in skills.items():
            self._skills[name] = skill
            for alias in skill.aliases:
                self._alias_map[alias.lower()] = name

    @classmethod
    def create(cls) -> SkillRegistry:
        """Create a registry with all built-in skills discovered."""
        registry = cls()
        registry._discover_builtin()
        return registry

    def select(self, state: ProjectState, context: dict[str, Any] | None = None) -> WorkflowSkill | None:
        """Auto-select a workflow skill by evaluating trigger functions."""
        ctx = context or {}
        for skill in self._skills.values():
            trigger = skill.workflow.trigger
            if trigger and trigger(state, ctx):
                return skill
        return None

    def by_name(self, name: str) -> WorkflowSkill | None:
        """Explicit lookup by name or alias, case-insensitive."""
        key = name.lower()
        if key in self._skills:
            return self._skills[key]
        canonical = self._alias_map.get(key)
        if canonical:
            return self._skills.get(canonical)
        return None

    def catalog(self) -> str:
        """Generate a markdown catalog of all skills for CEO prompt injection."""
        lines: list[str] = ["# Available Workflow Skills", ""]
        for name, skill in self._skills.items():
            lines.append(f"## {name}")
            lines.append(f"**Description:** {skill.description}")
            lines.append(f"**Trigger:** {skill.trigger_description}")
            if skill.aliases:
                lines.append(f"**Aliases:** {', '.join(skill.aliases)}")
            if skill.phases:
                lines.append("**Phases:**")
                for phase in skill.phases:
                    desc = f" — {phase.description}" if phase.description else ""
                    lines.append(f"  - {phase.name}{desc}")
            if skill.inputs:
                lines.append(f"**Inputs:** {', '.join(skill.inputs)}")
            if skill.outputs:
                lines.append(f"**Outputs:** {', '.join(skill.outputs)}")
            if skill.success_criteria:
                lines.append(f"**Success criteria:** {skill.success_criteria}")
            lines.append("")
        return "\n".join(lines)

    def names(self) -> list[str]:
        """All registered skill names."""
        return list(self._skills.keys())
