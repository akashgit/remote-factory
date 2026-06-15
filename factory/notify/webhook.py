"""Webhook event dispatcher — POSTs factory events to an external URL using stdlib only."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request

import structlog

log = structlog.get_logger()

_MAX_RETRIES = 3
_TIMEOUT_SECONDS = 5
_BACKOFF_BASE = 1  # seconds: 1, 2, 4


class WebhookDispatcher:
    """Dispatches factory events as JSON POST requests to a configured URL.

    Requires FACTORY_WEBHOOK_URL env var (or config.toml event_webhook_url).
    Optionally signs payloads with HMAC-SHA256 when FACTORY_WEBHOOK_SECRET is set.
    Optionally filters event types via FACTORY_WEBHOOK_EVENTS (comma-separated).
    """

    def __init__(self) -> None:
        from factory.user_config import resolve

        self._url = resolve(
            "event_webhook_url", env_var="FACTORY_WEBHOOK_URL", default="",
        ) or ""
        self._secret = resolve(
            "event_webhook_secret", env_var="FACTORY_WEBHOOK_SECRET", default="",
        ) or ""
        self._allowed_events: set[str] | None = None

        raw_events = (resolve(
            "event_webhook_events", env_var="FACTORY_WEBHOOK_EVENTS", default="",
        ) or "").strip()
        if raw_events:
            self._allowed_events = {e.strip() for e in raw_events.split(",") if e.strip()}

    @property
    def is_configured(self) -> bool:
        return bool(self._url)

    def dispatch(self, event: dict) -> None:
        """POST event JSON to the webhook URL in a background daemon thread.

        Silently returns if not configured or the event type is filtered out.
        """
        if not self.is_configured:
            return

        if self._allowed_events is not None:
            event_type = event.get("type", "")
            if event_type not in self._allowed_events:
                return

        thread = threading.Thread(target=self._send, args=(event,), daemon=True)
        thread.start()

    def _send(self, event: dict) -> None:
        payload = json.dumps(event).encode()
        headers: dict[str, str] = {"Content-Type": "application/json"}

        if self._secret:
            signature = hmac.new(
                self._secret.encode(), payload, hashlib.sha256,
            ).hexdigest()
            headers["X-Factory-Signature"] = f"sha256={signature}"

        for attempt in range(_MAX_RETRIES):
            try:
                req = urllib.request.Request(
                    self._url,
                    data=payload,
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
                    resp.read()
                log.debug("webhook_dispatched", url=self._url, event_type=event.get("type"))
                return
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    log.warning("webhook_client_error", status=exc.code, url=self._url)
                    return
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))
                else:
                    log.warning("webhook_failed", url=self._url, error=str(exc))
            except (urllib.error.URLError, OSError) as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))
                else:
                    log.warning("webhook_failed", url=self._url, error=str(exc))
            except Exception as exc:
                log.warning("webhook_unexpected_error", url=self._url, error=str(exc))
                return
