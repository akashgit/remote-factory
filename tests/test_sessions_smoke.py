"""Smoke test for factory.sessions — verify init_db creates the database file."""

from pathlib import Path

from factory.sessions import init_db


def test_init_db_creates_database(tmp_path: Path) -> None:
    db_path = init_db(tmp_path)
    assert db_path.exists()
    assert db_path == tmp_path / ".factory" / "sessions.db"
