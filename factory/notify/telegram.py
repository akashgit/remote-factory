"""Telegram notifier — sends experiment digests via Bot API using stdlib only."""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.models import CompositeScore, ExperimentRecord

logger = logging.getLogger(__name__)

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """Sends formatted experiment digests to a Telegram chat.

    Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.
    If either is missing the notifier logs a warning and silently skips.
    """

    def __init__(self) -> None:
        self._token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    async def send_digest(
        self,
        project_name: str,
        records: list[ExperimentRecord],
        composite: CompositeScore | None,
    ) -> None:
        if not self._is_configured():
            logger.warning(
                "Telegram not configured (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID). "
                "Skipping digest."
            )
            return

        text = self._format_message(project_name, records, composite)
        self._post(text)

    def _is_configured(self) -> bool:
        return bool(self._token and self._chat_id)

    def _format_message(
        self,
        project_name: str,
        records: list[ExperimentRecord],
        composite: CompositeScore | None,
    ) -> str:
        lines: list[str] = []
        lines.append(f"┌─── {project_name} ── experiment digest ───┐")
        lines.append("")

        for r in records:
            delta = f"{r.delta:+.4f}" if r.delta is not None else "n/a"
            cost = f"${r.cost_usd:.2f}" if r.cost_usd is not None else "n/a"
            lines.append(f"  #{r.id}  {r.verdict.upper()}  delta={delta}  cost={cost}")
            lines.append(f"      {r.hypothesis[:80]}")
            lines.append("")

        if composite is not None:
            status = "PASS" if composite.passed else "FAIL"
            lines.append(f"  composite: {composite.total:.4f}  [{status}]")
            if composite.guard_violations:
                lines.append(f"  violations: {', '.join(composite.guard_violations)}")
            lines.append("")

        lines.append("└────────────────────────────────────────────┘")
        return "\n".join(lines)

    def _post(self, text: str) -> None:
        url = _API_URL.format(token=self._token)
        payload = json.dumps({"chat_id": self._chat_id, "text": text}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except urllib.error.URLError as exc:
            logger.error("Telegram send failed: %s", exc)
