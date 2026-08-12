"""Tests for factory/review.py — review posting and draft PR lifecycle."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from factory.review import mark_pr_ready, post_review


class TestMarkPrReady:
    def test_success(self) -> None:
        with patch("factory.review.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            assert mark_pr_ready(42) is True
            mock_run.assert_called_once_with(
                ["gh", "pr", "ready", "42"],
                capture_output=True,
                text=True,
                timeout=30,
            )

    def test_success_with_repo(self) -> None:
        with patch("factory.review.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            assert mark_pr_ready(7, repo="owner/repo") is True
            mock_run.assert_called_once_with(
                ["gh", "pr", "ready", "7", "--repo", "owner/repo"],
                capture_output=True,
                text=True,
                timeout=30,
            )

    def test_failure_returns_false(self) -> None:
        with patch("factory.review.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stderr="not a draft"
            )
            assert mark_pr_ready(42) is False

    def test_idempotent_already_ready(self) -> None:
        with patch("factory.review.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            assert mark_pr_ready(42) is True

    def test_timeout_returns_false(self) -> None:
        with patch("factory.review.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=[], timeout=30)
            assert mark_pr_ready(42) is False

    def test_gh_not_found_returns_false(self) -> None:
        with patch("factory.review.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            assert mark_pr_ready(42) is False


class TestPostReviewDraftLifecycle:
    def test_keep_calls_mark_pr_ready(self) -> None:
        with patch("factory.review.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            post_review(10, "body", "KEEP")
            calls = mock_run.call_args_list
            assert len(calls) == 2
            assert calls[0].args[0] == ["gh", "pr", "review", "10", "--approve", "--body", "body"]
            assert calls[1].args[0] == ["gh", "pr", "ready", "10"]

    def test_keep_with_repo_passes_repo(self) -> None:
        with patch("factory.review.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            post_review(10, "body", "KEEP", repo="owner/repo")
            calls = mock_run.call_args_list
            assert len(calls) == 2
            assert calls[1].args[0] == ["gh", "pr", "ready", "10", "--repo", "owner/repo"]

    def test_revert_does_not_call_mark_pr_ready(self) -> None:
        with patch("factory.review.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            post_review(10, "body", "REVERT")
            calls = mock_run.call_args_list
            assert len(calls) == 1
            assert "review" in calls[0].args[0]

    def test_review_failure_skips_mark_pr_ready(self) -> None:
        with patch("factory.review.subprocess.run") as mock_run, \
             patch("factory.review._post_comment", return_value=False):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stderr="error"
            )
            post_review(10, "body", "KEEP")
            ready_calls = [c for c in mock_run.call_args_list if "ready" in c.args[0]]
            assert len(ready_calls) == 0

    def test_keep_fallback_comment_still_marks_ready(self) -> None:
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "review" in cmd:
                return subprocess.CompletedProcess(args=[], returncode=1, stderr="no perms")
            return subprocess.CompletedProcess(args=[], returncode=0)

        with patch("factory.review.subprocess.run", side_effect=side_effect) as mock_run, \
             patch("factory.review._post_comment", return_value=True):
            result = post_review(10, "body", "KEEP")
            assert result is True
            ready_calls = [c for c in mock_run.call_args_list if "ready" in c.args[0]]
            assert len(ready_calls) == 1
            assert ready_calls[0].args[0] == ["gh", "pr", "ready", "10"]
