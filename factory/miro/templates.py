"""Pure-data module: colors, dimensions, spacing, and connector style constants for Miro boards."""

# ── agent role colors ────────────────────────────────────────────

AGENT_COLORS: dict[str, str] = {
    "researcher": "#4A90D9",
    "strategist": "#2ECC71",
    "builder": "#7B68EE",
    "reviewer": "#E74C3C",
    "evaluator": "#F39C12",
    "archivist": "#9B59B6",
    "distiller": "#1ABC9C",
    "ceo": "#34495E",
}

# ── drift analysis colors ────────────────────────────────────────

DRIFT_COLORS: dict[str, str] = {
    "undocumented": "#FF4444",
    "phantom": "#CCCCCC",
    "drifted": "#FF8C00",
}

# ── component category colors ────────────────────────────────────

COMPONENT_COLORS: dict[str, str] = {
    "agent": "#4A90D9",
    "data": "#27AE60",
    "eval": "#F39C12",
    "cli": "#8E44AD",
    "config": "#95A5A6",
    "test": "#E67E22",
}

# ── experiment verdict colors ────────────────────────────────────

VERDICT_COLORS: dict[str, str] = {
    "keep": "#27AE60",
    "revert": "#E74C3C",
    "error": "#95A5A6",
}

# ── frame and shape dimensions ───────────────────────────────────

FRAME_WIDTH = 800
FRAME_HEIGHT = 600

SHAPE_WIDTH = 200
SHAPE_HEIGHT = 80

SHAPE_SMALL_WIDTH = 150
SHAPE_SMALL_HEIGHT = 60

# ── grid layout constants ────────────────────────────────────────

GRID_GAP = 40
GRID_PADDING = 20

# ── connector style constants ────────────────────────────────────

CONNECTOR_STYLE = "straight"
CONNECTOR_STROKE_WIDTH = 2
CONNECTOR_COLOR = "#333333"
