"""Tests for factory.messages — user-to-CEO message channel."""

from pathlib import Path

import pytest

from factory.messages import (
    MAX_MESSAGE_CHARS,
    MAX_PENDING_MESSAGES,
    MAX_TOTAL_CHARS,
    mark_read,
    read_pending,
    write_message,
)


class TestWriteMessage:
    def test_creates_message_file(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        msg = write_message(project, "focus on quality")
        assert msg.text == "focus on quality"
        assert msg.id

        msg_dir = project / ".factory" / "messages"
        assert msg_dir.exists()
        files = list(msg_dir.glob("*.md"))
        assert len(files) == 1
        assert "focus on quality" in files[0].read_text()

    def test_multiple_messages(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        write_message(project, "msg 1")
        write_message(project, "msg 2")

        files = list((project / ".factory" / "messages").glob("*.md"))
        assert len(files) == 2


class TestReadPending:
    def test_empty_when_no_messages(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        assert read_pending(project) == []

    def test_reads_written_messages(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        write_message(project, "hello CEO")
        pending = read_pending(project)
        assert len(pending) == 1
        assert pending[0].text == "hello CEO"

    def test_ordered_by_filename(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        write_message(project, "first")
        write_message(project, "second")
        pending = read_pending(project)
        assert len(pending) == 2
        assert pending[0].text == "first"
        assert pending[1].text == "second"


class TestMarkRead:
    def test_moves_to_read_dir(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        msg = write_message(project, "done reading")
        mark_read(project, [msg.id])

        assert read_pending(project) == []
        read_dir = project / ".factory" / "messages" / "read"
        assert read_dir.exists()
        files = list(read_dir.glob("*.md"))
        assert len(files) == 1

    def test_partial_mark_read(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        msg1 = write_message(project, "msg 1")
        write_message(project, "msg 2")
        mark_read(project, [msg1.id])

        pending = read_pending(project)
        assert len(pending) == 1
        assert pending[0].text == "msg 2"

    def test_idempotent(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        msg = write_message(project, "test")
        mark_read(project, [msg.id])
        mark_read(project, [msg.id])
        assert read_pending(project) == []


class TestMessageCLI:
    def test_cmd_message_writes_file(self, tmp_path: Path) -> None:
        from argparse import Namespace

        from factory.cli import cmd_message

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()
        args = Namespace(path=str(project), text="focus on quality gates")
        result = cmd_message(args)
        assert result == 0
        msg_dir = project / ".factory" / "messages"
        files = list(msg_dir.glob("*.md"))
        assert len(files) == 1
        assert "focus on quality gates" in files[0].read_text()

    def test_message_subcommand_parsing(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["message", "/tmp/project", "hello CEO"])
        assert args.path == "/tmp/project"
        assert args.text == "hello CEO"


class TestMessageInjection:
    def test_build_ceo_task_includes_messages(self, tmp_path: Path) -> None:
        from factory.cli import _build_ceo_task

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()
        write_message(project, "fix quality gates first")

        pending = read_pending(project)
        task = _build_ceo_task(project, "improve", messages=pending)
        assert "User Messages" in task
        assert "fix quality gates first" in task
        assert "HIGH PRIORITY" in task

    def test_build_ceo_task_no_messages(self, tmp_path: Path) -> None:
        from factory.cli import _build_ceo_task

        project = tmp_path / "proj"
        project.mkdir()
        task = _build_ceo_task(project, "improve")
        assert "User Messages" not in task

    def test_build_ceo_task_does_not_mark_read(self, tmp_path: Path) -> None:
        from factory.cli import _build_ceo_task

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()
        write_message(project, "test message")
        pending = read_pending(project)
        assert len(pending) == 1

        _build_ceo_task(project, "improve", messages=pending)
        assert len(read_pending(project)) == 1


class TestMessageValidation:
    def test_cmd_message_rejects_nonexistent_path(self, tmp_path: Path) -> None:
        from argparse import Namespace

        from factory.cli import cmd_message

        args = Namespace(path=str(tmp_path / "nonexistent"), text="hello")
        assert cmd_message(args) == 1

    def test_cmd_message_rejects_non_factory_project(self, tmp_path: Path) -> None:
        from argparse import Namespace

        from factory.cli import cmd_message

        project = tmp_path / "proj"
        project.mkdir()
        args = Namespace(path=str(project), text="hello")
        assert cmd_message(args) == 1

    def test_cmd_message_rejects_empty_text(self, tmp_path: Path) -> None:
        from argparse import Namespace

        from factory.cli import cmd_message

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()
        args = Namespace(path=str(project), text="")
        assert cmd_message(args) == 1

    def test_cmd_message_rejects_whitespace_text(self, tmp_path: Path) -> None:
        from argparse import Namespace

        from factory.cli import cmd_message

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()
        args = Namespace(path=str(project), text="   \n  ")
        assert cmd_message(args) == 1


class TestReadPendingCaps:
    def test_read_pending_caps_at_max_messages(self, tmp_path: Path) -> None:
        """Writing 25 messages, read_pending returns only 20 (MAX_PENDING_MESSAGES)."""
        project = tmp_path / "proj"
        project.mkdir()
        msg_dir = project / ".factory" / "messages"
        msg_dir.mkdir(parents=True)

        # Write 25 messages directly to bypass rate limiting
        for i in range(25):
            (msg_dir / f"20260504T120000{i:06d}-abcd{i:04d}.md").write_text(
                f"timestamp: 2026-05-04T12:00:00.{i:06d}+00:00\n\nhello {i}\n"
            )

        pending = read_pending(project)
        assert len(pending) == MAX_PENDING_MESSAGES  # 20

    def test_read_pending_truncates_at_max_total_chars(self, tmp_path: Path) -> None:
        """Messages exceeding MAX_TOTAL_CHARS are truncated."""
        project = tmp_path / "proj"
        project.mkdir()
        msg_dir = project / ".factory" / "messages"
        msg_dir.mkdir(parents=True)

        # Each message is ~10k chars; at 50k cap we should get ~5
        for i in range(10):
            text = "x" * 10_000
            (msg_dir / f"20260504T120000{i:06d}-abcd{i:04d}.md").write_text(
                f"timestamp: 2026-05-04T12:00:00.{i:06d}+00:00\n\n{text}\n"
            )

        pending = read_pending(project)
        total = sum(len(m.text) for m in pending)
        assert total <= MAX_TOTAL_CHARS
        assert len(pending) < 10

    def test_single_large_message_bypasses_char_cap(self, tmp_path: Path) -> None:
        """First message is always included even if it alone exceeds max_chars."""
        project = tmp_path / "proj"
        project.mkdir()
        msg_dir = project / ".factory" / "messages"
        msg_dir.mkdir(parents=True)

        # Single message larger than MAX_TOTAL_CHARS — the `and messages` guard
        # means the first message always passes through.
        big_text = "y" * (MAX_TOTAL_CHARS + 1_000)
        (msg_dir / "20260504T120000000000-aaaaaaaa.md").write_text(
            f"timestamp: 2026-05-04T12:00:00+00:00\n\n{big_text}\n"
        )

        pending = read_pending(project)
        assert len(pending) == 1
        assert len(pending[0].text) > MAX_TOTAL_CHARS


class TestMessageSizeValidation:
    def test_write_rejects_empty_text(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        with pytest.raises(ValueError, match="empty"):
            write_message(project, "")

    def test_write_rejects_whitespace_only_text(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        with pytest.raises(ValueError, match="empty"):
            write_message(project, "   \n\t  ")

    def test_write_rejects_oversized_message(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        with pytest.raises(ValueError, match="exceeds maximum"):
            write_message(project, "x" * (MAX_MESSAGE_CHARS + 1))


class TestRateLimiting:
    def test_write_rejects_when_too_many_pending(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        msg_dir = project / ".factory" / "messages"
        msg_dir.mkdir(parents=True)

        # Create MAX_PENDING_MESSAGES files directly
        for i in range(MAX_PENDING_MESSAGES):
            (msg_dir / f"20260504T120000{i:06d}-abcd{i:04d}.md").write_text(
                f"timestamp: 2026-05-04T12:00:00.{i:06d}+00:00\n\nmsg {i}\n"
            )

        with pytest.raises(ValueError, match="Too many pending"):
            write_message(project, "one more")


class TestMalformedMessages:
    def test_malformed_file_no_timestamp_header(self, tmp_path: Path) -> None:
        """A message file without a valid timestamp header is still read gracefully."""
        project = tmp_path / "proj"
        project.mkdir()
        msg_dir = project / ".factory" / "messages"
        msg_dir.mkdir(parents=True)

        (msg_dir / "20260504T120000000000-badbadbad.md").write_text(
            "no timestamp here\n\njust some text\n"
        )

        pending = read_pending(project)
        assert len(pending) == 1
        # The text should still be extracted from line index 2
        assert pending[0].text == "just some text"
