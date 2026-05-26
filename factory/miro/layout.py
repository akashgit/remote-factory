"""Pure coordinate math engine for Miro board layout — no API calls."""

from __future__ import annotations

from dataclasses import dataclass

from factory.miro.templates import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    GRID_GAP,
    GRID_PADDING,
    SHAPE_HEIGHT,
    SHAPE_WIDTH,
)

# Grid configuration: 3 columns x 2 rows
_GRID_COLS = 3
_GRID_ROWS = 2


@dataclass
class FramePosition:
    """Computed position and size for a frame."""

    x: int
    y: int
    width: int
    height: int


@dataclass
class ItemPosition:
    """Computed position for an item within a frame."""

    x: int
    y: int
    width: int
    height: int
    index: int  # original item index


@dataclass
class ConnectorRoute:
    """Computed coordinates for a connector between two items."""

    start_x: int
    start_y: int
    end_x: int
    end_y: int


def compute_frame_positions(section_count: int) -> list[tuple[int, int, int, int]]:
    """Compute (x, y, width, height) for each frame in a 3x2 grid layout.

    Frames are arranged left-to-right, top-to-bottom.
    """
    positions: list[tuple[int, int, int, int]] = []
    for i in range(section_count):
        col = i % _GRID_COLS
        row = i // _GRID_COLS
        x = col * (FRAME_WIDTH + GRID_GAP)
        y = row * (FRAME_HEIGHT + GRID_GAP)
        positions.append((x, y, FRAME_WIDTH, FRAME_HEIGHT))
    return positions


def layout_items_in_frame(
    items: list[str],
    frame_x: int,
    frame_y: int,
    frame_w: int,
    frame_h: int,
) -> list[ItemPosition]:
    """Position items top-down within a frame with padding.

    Each item is placed below the previous one with GRID_PADDING spacing.
    Items are horizontally centered within the frame.
    """
    positioned: list[ItemPosition] = []
    item_x = frame_x + GRID_PADDING
    available_width = frame_w - 2 * GRID_PADDING
    item_width = min(SHAPE_WIDTH, available_width)

    # Center items horizontally
    item_x = frame_x + (frame_w - item_width) // 2

    current_y = frame_y + GRID_PADDING

    for i, _item in enumerate(items):
        positioned.append(ItemPosition(
            x=item_x,
            y=current_y,
            width=item_width,
            height=SHAPE_HEIGHT,
            index=i,
        ))
        current_y += SHAPE_HEIGHT + GRID_PADDING

    return positioned


def route_connector(
    from_pos: tuple[int, int, int, int],
    to_pos: tuple[int, int, int, int],
) -> ConnectorRoute:
    """Compute connector coordinates between two positioned items.

    Each position is (x, y, width, height). The connector goes from the
    center-bottom of the source to the center-top of the target.
    """
    from_x, from_y, from_w, from_h = from_pos
    to_x, to_y, to_w, _to_h = to_pos

    return ConnectorRoute(
        start_x=from_x + from_w // 2,
        start_y=from_y + from_h,
        end_x=to_x + to_w // 2,
        end_y=to_y,
    )
