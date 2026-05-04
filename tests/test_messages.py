"""Tests for factory.messages — user-to-CEO message channel."""

from pathlib import Path

from factory.messages import mark_read, read_pending, write_message


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
