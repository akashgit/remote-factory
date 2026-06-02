"""Usage ledger — append-only JSONL log of runner token usage."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from factory.runners.types import UsageStats

logger = logging.getLogger(__name__)


def log_usage(
    project_path: Path,
    runner_name: str,
    role: str,
    usage: UsageStats | None,
    exit_code: int = 0,
) -> None:
    if usage is None:
        return
    usage_file = project_path / ".factory" / "usage.jsonl"
    usage_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "runner": runner_name,
        "role": role,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cost_usd": usage.cost_usd,
        "duration_seconds": usage.duration_seconds,
        "model": usage.model_used,
        "exit_code": exit_code,
    }
    with open(usage_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_usage(project_path: Path) -> list[dict]:
    usage_file = project_path / ".factory" / "usage.jsonl"
    if not usage_file.exists():
        return []
    entries = []
    for line in usage_file.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries
