"""Board — namespaced shared data plane for multi-mode composition."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from factory.models import BoardState


class Board:
    """Thread-safe, atomically-persisted board for cross-mode data sharing.

    Each mode writes ONLY to ``data[mode_name]``; reads are unrestricted.
    Global data is shared across all modes via ``global_data``.
    """

    def __init__(self, path: Path, run_id: str, modes: list[str]) -> None:
        self._path = path
        self._run_id = run_id
        self._modes = set(modes)
        self._lock = asyncio.Lock()
        now = datetime.now(timezone.utc).isoformat()
        self._state = BoardState(
            run_id=run_id,
            modes_requested=list(modes),
            started_at=now,
            updated_at=now,
        )

    # ── namespace reads ──────────────────────────────────────────

    def read(self, mode: str, key: str | None = None) -> Any:
        ns = self._state.data.get(mode, {})
        if key is None:
            return ns
        return ns.get(key)

    def read_global(self, key: str | None = None) -> Any:
        if key is None:
            return self._state.global_data
        return self._state.global_data.get(key)

    # ── namespace writes ─────────────────────────────────────────

    def write(self, mode: str, key: str, value: Any) -> None:
        if mode not in self._modes:
            raise ValueError(
                f"Mode {mode!r} not in allowed modes: {sorted(self._modes)}"
            )
        self._state.data.setdefault(mode, {})[key] = value
        self._state.updated_at = datetime.now(timezone.utc).isoformat()

    def write_global(self, key: str, value: Any) -> None:
        self._state.global_data[key] = value
        self._state.updated_at = datetime.now(timezone.utc).isoformat()

    # ── lifecycle ────────────────────────────────────────────────

    def mark_mode_complete(self, mode: str) -> None:
        if mode not in self._state.modes_completed:
            self._state.modes_completed.append(mode)
        self._state.updated_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self, mode: str | None = None) -> dict[str, Any]:
        if mode is not None:
            return self._state.data.get(mode, {})
        return self._state.data

    def reset(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._state = BoardState(
            run_id=self._run_id,
            modes_requested=list(self._modes),
            started_at=now,
            updated_at=now,
        )

    # ── persistence ──────────────────────────────────────────────

    def load(self) -> BoardState:
        raw = json.loads(self._path.read_text())
        self._state = BoardState.model_validate(raw)
        return self._state

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._state.model_dump(mode="json")
        dir_fd = None
        try:
            fd = tempfile.NamedTemporaryFile(
                mode="w",
                dir=self._path.parent,
                suffix=".tmp",
                delete=False,
            )
            try:
                json.dump(payload, fd, indent=2)
                fd.flush()
                os.fsync(fd.fileno())
            finally:
                fd.close()
            os.replace(fd.name, self._path)
        except BaseException:
            try:
                os.unlink(fd.name)
            except OSError:
                pass
            raise

    @property
    def state(self) -> BoardState:
        return self._state
