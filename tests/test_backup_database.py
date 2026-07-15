from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from scripts.backup_database import backup_database


def create_sample_database(database_path, value: str) -> None:
    with sqlite3.connect(database_path) as conn:
        conn.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        conn.execute("INSERT INTO sample (value) VALUES (?)", (value,))
        conn.commit()


def test_backup_database_creates_readable_sqlite_copy(tmp_path):
    database_path = tmp_path / "file_manager.sqlite3"
    backup_dir = tmp_path / "backups"
    create_sample_database(database_path, "kept-data")

    backup_path = backup_database(
        database_path,
        backup_dir,
        created_at=datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc),
    )

    assert backup_path.name == "file_manager-20260715T010203Z.sqlite3"
    with sqlite3.connect(backup_path) as conn:
        assert conn.execute("SELECT value FROM sample").fetchone()[0] == "kept-data"


def test_backup_database_keeps_only_requested_number_of_backups(tmp_path):
    database_path = tmp_path / "file_manager.sqlite3"
    backup_dir = tmp_path / "backups"
    create_sample_database(database_path, "current-data")

    for hour in range(3):
        backup_database(
            database_path,
            backup_dir,
            keep=2,
            created_at=datetime(2026, 7, 15, hour, tzinfo=timezone.utc),
        )

    backup_names = sorted(path.name for path in backup_dir.glob("*.sqlite3"))
    assert backup_names == [
        "file_manager-20260715T010000Z.sqlite3",
        "file_manager-20260715T020000Z.sqlite3",
    ]


def test_backup_database_rejects_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError, match="数据库文件不存在"):
        backup_database(tmp_path / "missing.sqlite3", tmp_path / "backups")
