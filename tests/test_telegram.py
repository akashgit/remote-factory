"""Tests for factory.notify.telegram — Telegram notifier with mocked urllib."""

from __future__ import annotations

import logging
import urllib.error
from datetime import datetime
from unittest.mock import MagicMock, patch

from factory.models import CompositeScore, ExperimentRecord
from factory.notify.telegram import TelegramNotifier


def _make_record(
    *,
    id: int = 1,
    verdict: str = "keep",
    delta: float | None = 0.05,
    cost_usd: float | None = 1.23,
    hypothesis: str = "Improve caching",
) -> ExperimentRecord:
    return ExperimentRecord(
        id=id,
        timestamp=datetime(2025, 1, 1),
        hypothesis=hypothesis,
        change_summary="Refactored cache layer",
        issue_number=None,
        pr_number=None,
        score_before=0.80,
        score_after=0.85,
        delta=delta,
        verdict=verdict,
        cost_usd=cost_usd,
        notes="",
    )


class TestTelegramNotifier:
    """Tests for TelegramNotifier."""

    def test_not_configured_skips(self, caplog):
        """When env vars are missing, send_digest logs warning and returns."""
        import asyncio

        notifier = TelegramNotifier()
        with caplog.at_level(logging.WARNING):
            asyncio.run(notifier.send_digest("my-project", [], None))
        assert "Telegram not configured" in caplog.text

    def test_is_configured_returns_false_without_env(self):
        notifier = TelegramNotifier()
        assert notifier._is_configured() is False

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    def test_is_configured_returns_true_with_env(self):
        notifier = TelegramNotifier()
        assert notifier._is_configured() is True

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    @patch("factory.notify.telegram.urllib.request.urlopen")
    def test_send_digest_with_records(self, mock_urlopen):
        """send_digest formats and posts message for experiment records."""
        import asyncio

        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        notifier = TelegramNotifier()
        records = [
            _make_record(id=1, verdict="keep", delta=0.05, cost_usd=1.23),
            _make_record(id=2, verdict="revert", delta=-0.02, cost_usd=0.50),
        ]
        asyncio.run(notifier.send_digest("my-project", records, None))

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert b"my-project" in req.data
        assert b"#1" in req.data
        assert b"#2" in req.data
        assert b"KEEP" in req.data
        assert b"REVERT" in req.data

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    @patch("factory.notify.telegram.urllib.request.urlopen")
    def test_send_digest_with_empty_records(self, mock_urlopen):
        """send_digest handles empty records list."""
        import asyncio

        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        notifier = TelegramNotifier()
        asyncio.run(notifier.send_digest("empty-project", [], None))

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert b"empty-project" in req.data

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    @patch("factory.notify.telegram.urllib.request.urlopen")
    def test_send_digest_with_composite(self, mock_urlopen):
        """send_digest includes composite score when provided."""
        import asyncio

        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        notifier = TelegramNotifier()
        composite = CompositeScore(
            total=0.85,
            results=[],
            guard_violations=[],
            passed=True,
        )
        asyncio.run(notifier.send_digest("proj", [_make_record()], composite))

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert b"PASS" in req.data
        assert b"0.8500" in req.data

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    @patch("factory.notify.telegram.urllib.request.urlopen")
    def test_send_digest_with_composite_fail_and_violations(self, mock_urlopen):
        """send_digest includes guard violations when composite fails."""
        import asyncio

        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        notifier = TelegramNotifier()
        composite = CompositeScore(
            total=0.40,
            results=[],
            guard_violations=["test_deleted", "scope_exceeded"],
            passed=False,
        )
        asyncio.run(notifier.send_digest("proj", [], composite))

        req = mock_urlopen.call_args[0][0]
        assert b"FAIL" in req.data
        assert b"test_deleted" in req.data
        assert b"scope_exceeded" in req.data

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    @patch("factory.notify.telegram.urllib.request.urlopen")
    def test_post_failure_logs_error(self, mock_urlopen, caplog):
        """_post logs error when urllib raises URLError."""
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")

        notifier = TelegramNotifier()
        with caplog.at_level(logging.ERROR):
            notifier._post("test message")

        assert "Telegram send failed" in caplog.text

    def test_format_message_with_none_delta_and_cost(self):
        """_format_message handles None delta and cost gracefully."""
        notifier = TelegramNotifier()
        notifier._token = "tok"
        notifier._chat_id = "123"

        record = _make_record(delta=None, cost_usd=None)
        msg = notifier._format_message("proj", [record], None)

        assert "n/a" in msg
        assert "#1" in msg

    def test_format_message_truncates_long_hypothesis(self):
        """_format_message truncates hypothesis to 80 chars."""
        notifier = TelegramNotifier()
        notifier._token = "tok"
        notifier._chat_id = "123"

        long_hyp = "A" * 200
        record = _make_record(hypothesis=long_hyp)
        msg = notifier._format_message("proj", [record], None)

        # The hypothesis line should contain at most 80 chars of the original
        for line in msg.split("\n"):
            if "AAAA" in line:
                # The hypothesis portion is truncated to 80
                assert len(line.strip()) <= 80 + 10  # some padding for indent
                break

    def test_format_message_different_verdicts(self):
        """_format_message correctly uppercases all verdict types."""
        notifier = TelegramNotifier()
        notifier._token = "tok"
        notifier._chat_id = "123"

        records = [
            _make_record(id=1, verdict="keep"),
            _make_record(id=2, verdict="revert"),
            _make_record(id=3, verdict="error"),
        ]
        msg = notifier._format_message("proj", records, None)

        assert "KEEP" in msg
        assert "REVERT" in msg
        assert "ERROR" in msg
