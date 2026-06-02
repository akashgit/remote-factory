"""RunnerRegistry — centralized runner registration and lookup."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)


class RunnerRegistry:
    """Registry for runner factory functions.

    Replaces the dict-based _RUNNERS with proper lookup, health checking,
    and discoverability.
    """

    def __init__(self) -> None:
        self._runners: dict[str, Callable[..., Any]] = {}
        self._default = "claude"

    def register(self, name: str, factory: Callable[..., Any]) -> None:
        """Register a runner factory function."""
        self._runners[name] = factory

    def get(self, name: str | None = None, **kwargs: Any) -> Any:
        """Resolve and instantiate a runner.

        Resolution order: explicit name > FACTORY_RUNNER env var > default.
        """
        resolved = name or os.environ.get("FACTORY_RUNNER") or self._default
        resolved = resolved.lower().strip()
        if resolved not in self._runners:
            available = ", ".join(sorted(self._runners.keys()))
            raise ValueError(f"Unknown runner: {resolved}. Available: {available}")
        return self._runners[resolved](**kwargs)

    def list_available(self) -> list[str]:
        """Return sorted list of registered runner names."""
        return sorted(self._runners.keys())

    async def check_all(self) -> dict[str, tuple[bool, str]]:
        """Health-check all registered runners."""
        results: dict[str, tuple[bool, str]] = {}
        for name in sorted(self._runners.keys()):
            try:
                runner = self._runners[name]()
                if hasattr(runner, "check_health"):
                    ok, msg = await runner.check_health()
                    results[name] = (ok, msg)
                else:
                    results[name] = (True, "no health check")
            except Exception as e:
                results[name] = (False, str(e))
        return results

    async def check_one(self, name: str) -> tuple[bool, str]:
        """Health-check a single runner by name."""
        if name not in self._runners:
            available = ", ".join(sorted(self._runners.keys()))
            raise ValueError(f"Unknown runner: {name}. Available: {available}")
        try:
            runner = self._runners[name]()
            if hasattr(runner, "check_health"):
                return await runner.check_health()
            return (True, "no health check")
        except Exception as e:
            return (False, str(e))
