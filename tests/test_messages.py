"""Tests for the message channel (factory/messages.py)."""

from __future__ import annotations

from pathlib import Path

from factory.messages import mark_read, read_pending, write_message


class TestWriteMessage:
    def test_creates_file(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        msg = write_message(project, "Focus on test coverage")
        assert msg.text == "Focus on test coverage"
        assert msg.id  # non-empty
        msg_file = project / ".factory" / "messages" / f"{msg.id}.md"
        assert msg_file.exists()
        assert msg_file.read_text() == "Focus on test coverage"

    def test_creates_factory_dir(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        write_message(project, "hello")
        assert (project / ".factory" / "messages").is_dir()

    def test_multiple_messages(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        m1 = write_message(project, "first")
        m2 = write_message(project, "second")
        assert m1.id != m2.id


class TestReadPending:
    def test_empty_when_no_messages(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        assert read_pending(project) == []

    def test_empty_when_no_factory_dir(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        assert read_pending(project) == []

    def test_reads_written_messages(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        write_message(project, "Do not touch eval/")
        write_message(project, "Prioritize latency")
        pending = read_pending(project)
        assert len(pending) == 2
        texts = [m.text for m in pending]
        assert "Do not touch eval/" in texts
        assert "Prioritize latency" in texts

    def test_sorted_by_timestamp(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        write_message(project, "first")
        write_message(project, "second")
        pending = read_pending(project)
        assert pending[0].id <= pending[1].id

    def test_ignores_read_subdir(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        m = write_message(project, "will be read")
        mark_read(project, [m.id])
        # Write another message after marking the first as read
        write_message(project, "still pending")
        pending = read_pending(project)
        assert len(pending) == 1
        assert pending[0].text == "still pending"


class TestMarkRead:
    def test_moves_to_read_dir(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        m = write_message(project, "test message")
        original = project / ".factory" / "messages" / f"{m.id}.md"
        assert original.exists()

        mark_read(project, [m.id])
        assert not original.exists()
        moved = project / ".factory" / "messages" / "read" / f"{m.id}.md"
        assert moved.exists()
        assert moved.read_text() == "test message"

    def test_skips_nonexistent_ids(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory" / "messages").mkdir(parents=True)
        # Should not raise
        mark_read(project, ["nonexistent_id"])

    def test_empty_list(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        # Should not raise
        mark_read(project, [])

    def test_full_lifecycle(self, tmp_path: Path):
        """Write -> read_pending -> mark_read -> read_pending returns empty."""
        project = tmp_path / "proj"
        project.mkdir()
        write_message(project, "directive one")
        write_message(project, "directive two")

        pending = read_pending(project)
        assert len(pending) == 2

        mark_read(project, [m.id for m in pending])

        pending_after = read_pending(project)
        assert len(pending_after) == 0
