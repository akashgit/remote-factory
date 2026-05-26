"""Board renderer — transforms analyzed project data into Miro board elements."""

from __future__ import annotations

from typing import Any

import structlog

from factory.miro.analyzer import ProjectStructure
from factory.miro.client import MiroClient
from factory.miro.drift import DriftItem
from factory.miro.layout import compute_frame_positions, layout_items_in_frame
from factory.miro.templates import (
    AGENT_COLORS,
    COMPONENT_COLORS,
    CONNECTOR_COLOR,
    CONNECTOR_STROKE_WIDTH,
    CONNECTOR_STYLE,
    DRIFT_COLORS,
    SHAPE_HEIGHT,
    SHAPE_WIDTH,
    VERDICT_COLORS,
)

log = structlog.get_logger()

# The six board frames in display order
_FRAME_NAMES = [
    "Project Overview",
    "Agent Pipeline",
    "Architecture Map",
    "Drift Report",
    "Experiment Timeline",
    "Strategy State",
]

# Agent pipeline display order
_AGENT_ORDER = [
    "researcher", "strategist", "builder", "reviewer", "evaluator", "archivist",
]


def _classify_module(path: str) -> str:
    """Classify a module path into a component category for coloring."""
    if "agent" in path:
        return "agent"
    if "eval" in path:
        return "eval"
    if "cli" in path or path.endswith("cli.py"):
        return "cli"
    if "config" in path or "model" in path:
        return "config"
    if "test" in path:
        return "test"
    return "data"


class BoardRenderer:
    """Renders project analysis data onto a Miro board.

    Creates six frames in a 3x2 grid layout, then populates each with
    shapes and connectors representing the project structure.
    """

    def __init__(self, client: MiroClient, board_id: str) -> None:
        self._client = client
        self._board_id = board_id

    async def render(
        self,
        structure: ProjectStructure,
        drift_items: list[DriftItem],
        history: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Render the full board with all six frames.

        Args:
            structure: Analyzed project structure from analyzer.
            drift_items: Architecture drift findings.
            history: List of experiment records (dicts with 'id', 'verdict', etc.).
            config: Project config dict (used for overview and strategy).

        Returns:
            Summary dict with frame_ids, item_count, and connector_count.
        """
        # Phase 1: Create all frames FIRST (April 2025 Miro bug —
        # items created before their parent frame are orphaned)
        frame_positions = compute_frame_positions(len(_FRAME_NAMES))
        frame_ids: dict[str, str] = {}

        for i, name in enumerate(_FRAME_NAMES):
            x, y, w, h = frame_positions[i]
            result = await self._client.create_frame_item(
                self._board_id,
                title=name,
                x=x,
                y=y,
                width=w,
                height=h,
            )
            frame_id = _extract_id(result)
            frame_ids[name] = frame_id
            log.debug("board_frame_created", name=name, frame_id=frame_id)

        # Phase 2: Populate each frame with items
        item_count = 0
        connector_count = 0

        item_count += await self._render_overview(
            frame_ids["Project Overview"], frame_positions[0], structure, config,
        )
        agent_ids = await self._render_agent_pipeline(
            frame_ids["Agent Pipeline"], frame_positions[1],
        )
        item_count += len(agent_ids)
        arch_ids = await self._render_architecture_map(
            frame_ids["Architecture Map"], frame_positions[2], structure,
        )
        item_count += len(arch_ids)
        item_count += await self._render_drift_report(
            frame_ids["Drift Report"], frame_positions[3], drift_items,
        )
        item_count += await self._render_experiment_timeline(
            frame_ids["Experiment Timeline"], frame_positions[4], history,
        )
        item_count += await self._render_strategy_state(
            frame_ids["Strategy State"], frame_positions[5], config,
        )

        # Phase 3: Draw connectors
        connector_count += await self._draw_agent_connectors(agent_ids)
        connector_count += await self._draw_dependency_connectors(
            arch_ids, structure,
        )

        summary = {
            "frame_ids": frame_ids,
            "item_count": item_count,
            "connector_count": connector_count,
        }
        log.info(
            "board_render_complete",
            items=item_count,
            connectors=connector_count,
            frames=len(frame_ids),
        )
        return summary

    async def _render_overview(
        self,
        frame_id: str,
        frame_pos: tuple[int, int, int, int],
        structure: ProjectStructure,
        config: dict[str, Any],
    ) -> int:
        """Render project overview: name, module count, goal."""
        fx, fy, fw, fh = frame_pos
        items = [
            config.get("goal", "Software project"),
            f"Modules: {len(structure.modules)}",
            f"Dependencies: {len(structure.dependencies)}",
        ]
        positions = layout_items_in_frame(items, fx, fy, fw, fh)
        for pos, text in zip(positions, items):
            await self._client.create_shape_item(
                self._board_id,
                content=text,
                x=pos.x,
                y=pos.y,
                width=pos.width,
                height=pos.height,
                parent=frame_id,
            )
        return len(items)

    async def _render_agent_pipeline(
        self,
        frame_id: str,
        frame_pos: tuple[int, int, int, int],
    ) -> dict[str, str]:
        """Render agent pipeline: one shape per agent role, returns {role: item_id}."""
        fx, fy, fw, fh = frame_pos
        positions = layout_items_in_frame(_AGENT_ORDER, fx, fy, fw, fh)
        agent_ids: dict[str, str] = {}

        for pos, role in zip(positions, _AGENT_ORDER):
            color = AGENT_COLORS.get(role, "#888888")
            result = await self._client.create_shape_item(
                self._board_id,
                content=role.capitalize(),
                x=pos.x,
                y=pos.y,
                width=pos.width,
                height=pos.height,
                parent=frame_id,
                fill_color=color,
            )
            agent_ids[role] = _extract_id(result)

        return agent_ids

    async def _render_architecture_map(
        self,
        frame_id: str,
        frame_pos: tuple[int, int, int, int],
        structure: ProjectStructure,
    ) -> dict[str, str]:
        """Render architecture map: one shape per module, colored by type."""
        fx, fy, fw, fh = frame_pos
        module_names = [m.path for m in structure.modules]
        positions = layout_items_in_frame(module_names, fx, fy, fw, fh)
        module_ids: dict[str, str] = {}

        for pos, module in zip(positions, structure.modules):
            category = _classify_module(module.path)
            color = COMPONENT_COLORS.get(category, "#95A5A6")
            label = module.path.rsplit("/", 1)[-1]  # show filename only

            result = await self._client.create_shape_item(
                self._board_id,
                content=label,
                x=pos.x,
                y=pos.y,
                width=pos.width,
                height=pos.height,
                parent=frame_id,
                fill_color=color,
            )
            module_ids[module.path] = _extract_id(result)

        return module_ids

    async def _render_drift_report(
        self,
        frame_id: str,
        frame_pos: tuple[int, int, int, int],
        drift_items: list[DriftItem],
    ) -> int:
        """Render drift report: color-coded drift items."""
        if not drift_items:
            # Show an "All clear" shape when no drift
            fx, fy, fw, fh = frame_pos
            await self._client.create_shape_item(
                self._board_id,
                content="No architecture drift detected",
                x=fx + fw // 2 - SHAPE_WIDTH // 2,
                y=fy + fh // 2 - SHAPE_HEIGHT // 2,
                width=SHAPE_WIDTH,
                height=SHAPE_HEIGHT,
                parent=frame_id,
                fill_color="#27AE60",
            )
            return 1

        fx, fy, fw, fh = frame_pos
        labels = [f"[{d.category}] {d.name}" for d in drift_items]
        positions = layout_items_in_frame(labels, fx, fy, fw, fh)

        for pos, drift in zip(positions, drift_items):
            color = DRIFT_COLORS.get(drift.category, "#CCCCCC")
            await self._client.create_shape_item(
                self._board_id,
                content=f"[{drift.category}] {drift.name}\n{drift.description}",
                x=pos.x,
                y=pos.y,
                width=pos.width,
                height=pos.height,
                parent=frame_id,
                fill_color=color,
            )

        return len(drift_items)

    async def _render_experiment_timeline(
        self,
        frame_id: str,
        frame_pos: tuple[int, int, int, int],
        history: list[dict[str, Any]],
    ) -> int:
        """Render experiment timeline: cards from experiment records."""
        if not history:
            fx, fy, fw, fh = frame_pos
            await self._client.create_shape_item(
                self._board_id,
                content="No experiments yet",
                x=fx + fw // 2 - SHAPE_WIDTH // 2,
                y=fy + fh // 2 - SHAPE_HEIGHT // 2,
                width=SHAPE_WIDTH,
                height=SHAPE_HEIGHT,
                parent=frame_id,
            )
            return 1

        fx, fy, fw, fh = frame_pos
        labels = [f"Exp {e.get('id', '?')}" for e in history]
        positions = layout_items_in_frame(labels, fx, fy, fw, fh)

        for pos, exp in zip(positions, history):
            verdict = str(exp.get("verdict", "error")).lower()
            color = VERDICT_COLORS.get(verdict, "#95A5A6")
            exp_id = exp.get("id", "?")
            label = f"#{exp_id}: {verdict}"

            await self._client.create_shape_item(
                self._board_id,
                content=label,
                x=pos.x,
                y=pos.y,
                width=pos.width,
                height=pos.height,
                parent=frame_id,
                fill_color=color,
            )

        return len(history)

    async def _render_strategy_state(
        self,
        frame_id: str,
        frame_pos: tuple[int, int, int, int],
        config: dict[str, Any],
    ) -> int:
        """Render strategy state: current focus, threshold, constraints."""
        fx, fy, fw, fh = frame_pos
        items = [
            f"Threshold: {config.get('threshold', 0.8)}",
            f"Focus: {config.get('focus', 'general improvement')}",
            f"Mode: {config.get('mode', 'improve')}",
        ]
        positions = layout_items_in_frame(items, fx, fy, fw, fh)

        for pos, text in zip(positions, items):
            await self._client.create_shape_item(
                self._board_id,
                content=text,
                x=pos.x,
                y=pos.y,
                width=pos.width,
                height=pos.height,
                parent=frame_id,
            )

        return len(items)

    async def _draw_agent_connectors(
        self, agent_ids: dict[str, str],
    ) -> int:
        """Draw connectors between agents in pipeline order."""
        count = 0
        for i in range(len(_AGENT_ORDER) - 1):
            from_role = _AGENT_ORDER[i]
            to_role = _AGENT_ORDER[i + 1]
            from_id = agent_ids.get(from_role)
            to_id = agent_ids.get(to_role)
            if from_id and to_id:
                await self._client.create_connector(
                    self._board_id,
                    start_item=from_id,
                    end_item=to_id,
                    style=CONNECTOR_STYLE,
                    stroke_width=CONNECTOR_STROKE_WIDTH,
                    stroke_color=CONNECTOR_COLOR,
                )
                count += 1
        return count

    async def _draw_dependency_connectors(
        self,
        module_ids: dict[str, str],
        structure: ProjectStructure,
    ) -> int:
        """Draw connectors for import dependencies between modules."""
        count = 0
        for dep in structure.dependencies:
            from_id = module_ids.get(dep.source)
            # Try to resolve the target to an actual module path
            target_path = dep.target.replace(".", "/") + ".py"
            to_id = module_ids.get(target_path)
            # Also try direct match
            if not to_id:
                to_id = module_ids.get(dep.target)

            if from_id and to_id and from_id != to_id:
                await self._client.create_connector(
                    self._board_id,
                    start_item=from_id,
                    end_item=to_id,
                    style=CONNECTOR_STYLE,
                    stroke_width=CONNECTOR_STROKE_WIDTH,
                    stroke_color=CONNECTOR_COLOR,
                )
                count += 1
        return count


def _extract_id(result: Any) -> str:
    """Extract an ID from a Miro API response (dict or object)."""
    if result is None:
        return ""
    if isinstance(result, dict):
        return str(result.get("id", ""))
    return str(getattr(result, "id", ""))
