"""Async Miro API client with rate limiting, dry-run support, and event emission."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import structlog

from factory.events import emit_event
from factory.user_config import resolve

log = structlog.get_logger()

_DRY_RUN_COUNTER = 0


def _next_dry_run_id() -> str:
    """Generate a sequential dry-run ID."""
    global _DRY_RUN_COUNTER  # noqa: PLW0603
    _DRY_RUN_COUNTER += 1
    return f"dry-run-{_DRY_RUN_COUNTER}"


class MiroClient:
    """Wrapper around miro_api.MiroApi with rate limiting and observability.

    Token resolution uses the factory config system:
      resolve("miro_token", env_var="FACTORY_MIRO_TOKEN")

    Set FACTORY_MIRO_DRY_RUN=1 to skip actual API calls (returns stub data).
    """

    def __init__(self, *, project_path: Path | None = None) -> None:
        self._token = resolve("miro_token", env_var="FACTORY_MIRO_TOKEN")
        self._project_path = project_path or Path.cwd()
        self._dry_run = os.environ.get("FACTORY_MIRO_DRY_RUN", "").strip() == "1"
        self._api: Any = None

        if not self._token:
            log.debug("miro_no_token", hint="Set FACTORY_MIRO_TOKEN or miro_token in config.toml")
            return

        if self._dry_run:
            log.info("miro_dry_run_enabled")
            return

        try:
            from miro_api import MiroApi
            self._api = MiroApi(self._token)
        except ImportError:
            log.warning("miro_api_not_installed", hint="pip install miro-api")
        except Exception as exc:
            log.warning("miro_init_failed", error=str(exc))

    @property
    def available(self) -> bool:
        """True if the client can make API calls (or is in dry-run mode)."""
        return self._dry_run or self._api is not None

    async def _call_with_backoff(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Call an API method with exponential backoff on HTTP 429."""
        if not self.available:
            log.debug("miro_unavailable", method=method_name)
            return None

        if self._dry_run:
            stub_id = _next_dry_run_id()
            log.info("miro_dry_run", method=method_name, stub_id=stub_id)
            emit_event(
                self._project_path,
                f"miro.api.{method_name}",
                data={"dry_run": True, "stub_id": stub_id},
            )
            return {"id": stub_id, "type": "dry_run"}

        max_retries = 5
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                method = getattr(self._api, method_name)
                result = method(*args, **kwargs)
                emit_event(
                    self._project_path,
                    f"miro.api.{method_name}",
                    data={"success": True},
                )
                return result
            except Exception as exc:
                status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
                if status == 429:
                    reset_after = _parse_reset_header(exc)
                    delay = reset_after if reset_after else base_delay * (2 ** attempt)
                    log.warning(
                        "miro_rate_limited",
                        method=method_name,
                        attempt=attempt + 1,
                        delay=delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                log.error("miro_api_error", method=method_name, error=str(exc))
                emit_event(
                    self._project_path,
                    f"miro.api.{method_name}",
                    data={"success": False, "error": str(exc)},
                )
                return None

        log.error("miro_rate_limit_exhausted", method=method_name, max_retries=max_retries)
        return None

    async def create_board(self, name: str, description: str = "") -> Any:
        """Create a new Miro board."""
        return await self._call_with_backoff(
            "create_board",
            {"name": name, "description": description},
        )

    async def create_frame_item(self, board_id: str, **kwargs: Any) -> Any:
        """Create a frame on a board."""
        return await self._call_with_backoff("create_frame_item", board_id, **kwargs)

    async def create_shape_item(self, board_id: str, **kwargs: Any) -> Any:
        """Create a shape on a board."""
        return await self._call_with_backoff("create_shape_item", board_id, **kwargs)

    async def create_connector(self, board_id: str, **kwargs: Any) -> Any:
        """Create a connector between items on a board."""
        return await self._call_with_backoff("create_connector", board_id, **kwargs)

    async def get_board(self, board_id: str) -> Any:
        """Get board details."""
        return await self._call_with_backoff("get_board", board_id)

    async def get_items(self, board_id: str, **kwargs: Any) -> Any:
        """Get all items on a board."""
        return await self._call_with_backoff("get_items_on_board", board_id, **kwargs)

    async def update_shape_item(self, board_id: str, item_id: str, **kwargs: Any) -> Any:
        """Update an existing shape item."""
        return await self._call_with_backoff(
            "update_shape_item", board_id, item_id, **kwargs,
        )

    async def delete_item(self, board_id: str, item_id: str) -> Any:
        """Delete an item from a board."""
        return await self._call_with_backoff("delete_item", board_id, item_id)


def _parse_reset_header(exc: Exception) -> float | None:
    """Extract retry delay from X-RateLimit-Reset header if available."""
    headers = getattr(exc, "headers", None)
    if not headers:
        return None
    reset = headers.get("X-RateLimit-Reset")
    if reset is None:
        return None
    try:
        reset_time = float(reset)
        # If it looks like a Unix timestamp, compute delay
        if reset_time > 1_000_000_000:
            return max(0.1, reset_time - time.time())
        # Otherwise treat as seconds to wait
        return max(0.1, reset_time)
    except (ValueError, TypeError):
        return None
