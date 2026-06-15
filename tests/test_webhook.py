"""Tests for factory/notify/webhook.py and webhook integration with emit_event."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import urllib.error
from argparse import Namespace
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest


class TestWebhookDispatcher:
    """Tests for WebhookDispatcher in isolation."""

    def test_not_configured_without_env(self):
        from factory.notify.webhook import WebhookDispatcher
        d = WebhookDispatcher()
        assert d.is_configured is False

    @patch.dict("os.environ", {"FACTORY_WEBHOOK_URL": "https://example.com/hook"})
    def test_configured_with_env(self):
        from factory.notify.webhook import WebhookDispatcher
        d = WebhookDispatcher()
        assert d.is_configured is True

    @patch.dict("os.environ", {"FACTORY_WEBHOOK_URL": "https://example.com/hook"})
    @patch("factory.notify.webhook.urllib.request.urlopen")
    def test_dispatch_posts_correct_payload(self, mock_urlopen):
        from factory.notify.webhook import WebhookDispatcher

        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        d = WebhookDispatcher()
        event = {"type": "test.event", "data": {"key": "value"}}
        d._send(event)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://example.com/hook"
        assert req.get_header("Content-type") == "application/json"
        body = json.loads(req.data)
        assert body["type"] == "test.event"
        assert body["data"]["key"] == "value"

    @patch.dict("os.environ", {
        "FACTORY_WEBHOOK_URL": "https://example.com/hook",
        "FACTORY_WEBHOOK_SECRET": "mysecret",
    })
    @patch("factory.notify.webhook.urllib.request.urlopen")
    def test_hmac_signature_when_secret_set(self, mock_urlopen):
        from factory.notify.webhook import WebhookDispatcher

        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        d = WebhookDispatcher()
        event = {"type": "test.event"}
        d._send(event)

        req = mock_urlopen.call_args[0][0]
        sig_header = req.get_header("X-factory-signature")
        assert sig_header is not None
        assert sig_header.startswith("sha256=")

        expected = hmac.new(
            b"mysecret", req.data, hashlib.sha256,
        ).hexdigest()
        assert sig_header == f"sha256={expected}"

    @patch.dict("os.environ", {"FACTORY_WEBHOOK_URL": "https://example.com/hook"})
    @patch("factory.notify.webhook.urllib.request.urlopen")
    def test_no_signature_without_secret(self, mock_urlopen):
        from factory.notify.webhook import WebhookDispatcher

        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        d = WebhookDispatcher()
        d._send({"type": "test"})

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("X-factory-signature") is None

    @patch.dict("os.environ", {"FACTORY_WEBHOOK_URL": "https://example.com/hook"})
    def test_dispatch_is_fire_and_forget(self):
        """dispatch() returns immediately without blocking on the HTTP call."""
        from factory.notify.webhook import WebhookDispatcher

        d = WebhookDispatcher()
        call_started = threading.Event()
        call_blocked = threading.Event()

        def blocking_send(event):
            call_started.set()
            call_blocked.wait(timeout=5)

        d._send = blocking_send

        d.dispatch({"type": "test"})
        assert call_started.wait(timeout=2), "Background thread should have started"
        call_blocked.set()

    @patch.dict("os.environ", {"FACTORY_WEBHOOK_URL": "https://example.com/hook"})
    @patch("factory.notify.webhook.urllib.request.urlopen")
    def test_errors_are_swallowed(self, mock_urlopen):
        from factory.notify.webhook import WebhookDispatcher

        mock_urlopen.side_effect = urllib.error.URLError("connection refused")

        d = WebhookDispatcher()
        d._send({"type": "test"})

    @patch.dict("os.environ", {"FACTORY_WEBHOOK_URL": "https://example.com/hook"})
    @patch("factory.notify.webhook.urllib.request.urlopen")
    @patch("factory.notify.webhook.time.sleep")
    def test_retries_on_5xx(self, mock_sleep, mock_urlopen):
        from factory.notify.webhook import WebhookDispatcher

        error_resp = MagicMock()
        error_resp.read.return_value = b""
        http_error = urllib.error.HTTPError(
            "https://example.com/hook", 500, "Server Error", {}, BytesIO(b""),
        )

        ok_resp = MagicMock()
        ok_resp.__enter__ = MagicMock(return_value=ok_resp)
        ok_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [http_error, ok_resp]

        d = WebhookDispatcher()
        d._send({"type": "test"})

        assert mock_urlopen.call_count == 2
        mock_sleep.assert_called_once_with(1)

    @patch.dict("os.environ", {"FACTORY_WEBHOOK_URL": "https://example.com/hook"})
    @patch("factory.notify.webhook.urllib.request.urlopen")
    def test_no_retry_on_4xx(self, mock_urlopen):
        from factory.notify.webhook import WebhookDispatcher

        http_error = urllib.error.HTTPError(
            "https://example.com/hook", 400, "Bad Request", {}, BytesIO(b""),
        )
        mock_urlopen.side_effect = http_error

        d = WebhookDispatcher()
        d._send({"type": "test"})

        assert mock_urlopen.call_count == 1

    @patch.dict("os.environ", {
        "FACTORY_WEBHOOK_URL": "https://example.com/hook",
        "FACTORY_WEBHOOK_EVENTS": "agent.started,agent.completed",
    })
    @patch("factory.notify.webhook.urllib.request.urlopen")
    def test_event_type_filtering(self, mock_urlopen):
        from factory.notify.webhook import WebhookDispatcher

        d = WebhookDispatcher()
        assert d._allowed_events == {"agent.started", "agent.completed"}

        send_called = threading.Event()
        original_send = d._send

        def tracking_send(event):
            send_called.set()
            original_send(event)

        d._send = tracking_send

        d.dispatch({"type": "cycle.started"})
        assert not send_called.wait(timeout=0.2), "Filtered event should not trigger _send"

    @patch.dict("os.environ", {
        "FACTORY_WEBHOOK_URL": "https://example.com/hook",
        "FACTORY_WEBHOOK_EVENTS": "agent.started",
    })
    @patch("factory.notify.webhook.urllib.request.urlopen")
    def test_allowed_event_passes_filter(self, mock_urlopen):
        from factory.notify.webhook import WebhookDispatcher

        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        d = WebhookDispatcher()
        d._send({"type": "agent.started"})
        mock_urlopen.assert_called_once()

    def test_dispatch_noop_when_not_configured(self):
        from factory.notify.webhook import WebhookDispatcher
        d = WebhookDispatcher()
        d.dispatch({"type": "test"})

    @patch.dict("os.environ", {"FACTORY_WEBHOOK_URL": "https://example.com/hook"})
    def test_empty_events_filter_forwards_all(self):
        from factory.notify.webhook import WebhookDispatcher
        d = WebhookDispatcher()
        assert d._allowed_events is None


class TestEmitEventWebhookIntegration:
    """Tests that emit_event calls webhook dispatch when configured."""

    def test_webhook_called_when_configured(self, tmp_path):
        import factory.events as events_mod

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        mock_dispatcher = MagicMock()
        mock_dispatcher.is_configured = True
        old = events_mod._webhook_dispatcher
        events_mod._webhook_dispatcher = mock_dispatcher
        try:
            events_mod.emit_event(project, "test.event", data={"x": 1})
            mock_dispatcher.dispatch.assert_called_once()
            dispatched_event = mock_dispatcher.dispatch.call_args[0][0]
            assert dispatched_event["type"] == "test.event"
            assert dispatched_event["data"]["x"] == 1
        finally:
            events_mod._webhook_dispatcher = old

    def test_webhook_not_called_when_unconfigured(self, tmp_path):
        import factory.events as events_mod

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        mock_dispatcher = MagicMock()
        mock_dispatcher.is_configured = False
        old = events_mod._webhook_dispatcher
        events_mod._webhook_dispatcher = mock_dispatcher
        try:
            events_mod.emit_event(project, "test.event")
            mock_dispatcher.dispatch.assert_not_called()
        finally:
            events_mod._webhook_dispatcher = old

    def test_webhook_error_does_not_break_emit(self, tmp_path):
        import factory.events as events_mod

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        mock_dispatcher = MagicMock()
        mock_dispatcher.is_configured = True
        mock_dispatcher.dispatch.side_effect = RuntimeError("boom")
        old = events_mod._webhook_dispatcher
        events_mod._webhook_dispatcher = mock_dispatcher
        try:
            event = events_mod.emit_event(project, "test.event")
            assert event["type"] == "test.event"
        finally:
            events_mod._webhook_dispatcher = old


class TestEnrichedFinalizeEvent:
    """Test that experiment.finalize event includes enriched data."""

    def test_finalize_event_includes_enriched_fields(self, tmp_path):
        from factory.cli import cmd_finalize
        from factory.store import ensure_factory_dir

        project = tmp_path / "proj"
        project.mkdir()
        factory_dir = project / ".factory"
        ensure_factory_dir(factory_dir)
        (factory_dir / "config.json").write_text(json.dumps({
            "goal": "test", "eval_command": "echo ok", "eval_threshold": 0.5,
        }))
        experiments_dir = factory_dir / "experiments"
        experiments_dir.mkdir()
        (experiments_dir / "001").mkdir()
        (experiments_dir / "001" / "hypothesis.md").write_text("test hypothesis")
        (factory_dir / "results.tsv").write_text(
            "id\ttimestamp\thypothesis\tchange_summary\tissue_number\tpr_number\t"
            "score_before\tscore_after\tdelta\tverdict\tcost_usd\tnotes\tresearch_citations\n"
        )

        args = Namespace(
            path=str(project),
            id=1,
            hypothesis="test hypothesis",
            verdict="keep",
            summary="did stuff",
            notes="",
            issue=42,
            pr=99,
            score_before=0.7,
            score_after=0.85,
            cost=1.50,
            force=True,
        )

        cmd_finalize(args)

        events_file = factory_dir / "events.jsonl"
        assert events_file.exists()
        all_events = [json.loads(line) for line in events_file.read_text().strip().splitlines()]
        finalize_events = [e for e in all_events if e["type"] == "experiment.finalize"]
        assert len(finalize_events) == 1
        data = finalize_events[0]["data"]
        assert data["pr_number"] == 99
        assert data["issue_number"] == 42
        assert data["score_before"] == 0.7
        assert data["score_after"] == 0.85
        assert data["delta"] == round(0.85 - 0.7, 6)
        assert data["cost_usd"] == 1.50


@pytest.mark.real_worktree
class TestWorktreeEvents:
    """Test that worktree create/remove emit events."""

    def _init_git_project(self, tmp_path, project):
        import subprocess
        env = {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        }
        subprocess.run(["git", "init", "-b", "main"], cwd=project, capture_output=True, check=True)
        (project / "README.md").write_text("test")
        (project / ".gitignore").write_text(".factory/\n")
        subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=project, capture_output=True, check=True, env=env,
        )

    def test_create_worktree_emits_event(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()
        self._init_git_project(tmp_path, project)

        from factory.worktree import create_worktree
        wt_path, branch = create_worktree(project)

        events_file = project / ".factory" / "events.jsonl"
        assert events_file.exists()
        all_events = [json.loads(line) for line in events_file.read_text().strip().splitlines()]
        created_events = [e for e in all_events if e["type"] == "worktree.created"]
        assert len(created_events) == 1
        data = created_events[0]["data"]
        assert "run_id" in data
        assert data["branch"] == branch
        assert data["base_branch"] == "main"
        assert data["worktree_path"] == str(wt_path)

    def test_remove_worktree_emits_event(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()
        self._init_git_project(tmp_path, project)

        from factory.worktree import create_worktree, remove_worktree
        wt_path, branch = create_worktree(project)

        remove_worktree(project, wt_path, branch)

        events_file = project / ".factory" / "events.jsonl"
        all_events = [json.loads(line) for line in events_file.read_text().strip().splitlines()]
        removed_events = [e for e in all_events if e["type"] == "worktree.removed"]
        assert len(removed_events) == 1
        data = removed_events[0]["data"]
        assert data["branch"] == branch
        assert "run_id" in data


class TestBacklogEvents:
    """Test that backlog add/remove emit events."""

    def test_backlog_add_emits_event(self, tmp_path):
        from factory.cli import cmd_backlog_add

        project = tmp_path / "proj"
        project.mkdir()
        strategy_dir = project / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "backlog.md").write_text("# Backlog\n")

        events_file = project / ".factory" / "events.jsonl"

        args = Namespace(path=str(project), item="Add widget feature")
        rc = cmd_backlog_add(args)
        assert rc == 0

        assert events_file.exists()
        events = [json.loads(line) for line in events_file.read_text().strip().splitlines()]
        backlog_events = [e for e in events if e["type"] == "backlog.added"]
        assert len(backlog_events) == 1
        assert backlog_events[0]["data"]["item"] == "Add widget feature"

    def test_backlog_remove_emits_event(self, tmp_path):
        from factory.cli import cmd_backlog_add, cmd_backlog_remove

        project = tmp_path / "proj"
        project.mkdir()
        strategy_dir = project / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "backlog.md").write_text("# Backlog\n")

        cmd_backlog_add(Namespace(path=str(project), item="Remove me"))

        events_file = project / ".factory" / "events.jsonl"
        events_file.write_text("")

        args = Namespace(path=str(project), item="Remove me")
        rc = cmd_backlog_remove(args)
        assert rc == 0

        events = [json.loads(line) for line in events_file.read_text().strip().splitlines()]
        removed_events = [e for e in events if e["type"] == "backlog.removed"]
        assert len(removed_events) == 1
        assert removed_events[0]["data"]["item"] == "Remove me"

    def test_backlog_add_no_event_on_duplicate(self, tmp_path):
        from factory.cli import cmd_backlog_add

        project = tmp_path / "proj"
        project.mkdir()
        strategy_dir = project / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "backlog.md").write_text("# Backlog\n")

        cmd_backlog_add(Namespace(path=str(project), item="Dup item"))

        events_file = project / ".factory" / "events.jsonl"
        events_file.write_text("")

        rc = cmd_backlog_add(Namespace(path=str(project), item="Dup item"))
        assert rc == 1

        content = events_file.read_text().strip()
        if content:
            events = [json.loads(line) for line in content.splitlines()]
            backlog_events = [e for e in events if e["type"] == "backlog.added"]
            assert len(backlog_events) == 0

    def test_backlog_remove_no_event_on_missing(self, tmp_path):
        from factory.cli import cmd_backlog_remove

        project = tmp_path / "proj"
        project.mkdir()
        strategy_dir = project / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "backlog.md").write_text("# Backlog\n")
        (project / ".factory" / "events.jsonl").write_text("")

        rc = cmd_backlog_remove(Namespace(path=str(project), item="nonexistent"))
        assert rc == 1

        events_file = project / ".factory" / "events.jsonl"
        content = events_file.read_text().strip()
        if content:
            events = [json.loads(line) for line in content.splitlines()]
            removed_events = [e for e in events if e["type"] == "backlog.removed"]
            assert len(removed_events) == 0
