from __future__ import annotations

import sqlite3
import sys

import app
from conftest import admin_user, authenticated_client, configure_temp_app
from services.backup_status_service import (
    build_backup_overview,
    load_backup_status,
    record_backup_status,
)


def create_sample_database(database_path, value: str) -> None:
    with sqlite3.connect(database_path) as conn:
        conn.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        conn.execute("INSERT INTO sample (value) VALUES (?)", (value,))
        conn.commit()


def test_record_backup_status_writes_and_preserves_entries(tmp_path):
    status_path = tmp_path / "backup_status.json"

    record_backup_status(
        status_path,
        "database",
        True,
        artifact="/backups/file_manager-20260715T010000Z.sqlite3",
        started_at="2026-07-15T01:00:00+00:00",
        finished_at="2026-07-15T01:00:05+00:00",
    )
    record_backup_status(
        status_path,
        "uploads",
        False,
        error="磁盘空间不足",
        started_at="2026-07-15T02:00:00+00:00",
        finished_at="2026-07-15T02:00:01+00:00",
    )

    status = load_backup_status(status_path)
    database_entry = status["database"]
    assert database_entry["success"] is True
    assert database_entry["artifact"] == "/backups/file_manager-20260715T010000Z.sqlite3"
    assert database_entry["finished_at"] == "2026-07-15T01:00:05+00:00"

    uploads_entry = status["uploads"]
    assert uploads_entry["success"] is False
    assert uploads_entry["error"] == "磁盘空间不足"


def test_build_backup_overview_includes_disk_space(tmp_path):
    status_path = tmp_path / "backup_status.json"
    record_backup_status(status_path, "database", True, artifact="/backups/db.sqlite3")

    overview = build_backup_overview(status_path, tmp_path)

    assert overview["database"]["success"] is True
    assert overview["uploads"] is None
    assert overview["disk_free"] is not None
    assert overview["disk_free"] > 0
    assert "未知" not in overview["disk_free_display"]


def test_backup_database_main_records_success_status(tmp_path, monkeypatch):
    database_path = tmp_path / "data" / "file_manager.sqlite3"
    database_path.parent.mkdir(parents=True)
    create_sample_database(database_path, "kept-data")
    status_path = tmp_path / "data" / "backup_status.json"

    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("DATABASE_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(sys, "argv", ["backup_database.py"])

    from scripts.backup_database import main

    assert main() == 0

    status = load_backup_status(status_path)
    entry = status["database"]
    assert entry["success"] is True
    assert entry["artifact"].endswith(".sqlite3")
    assert entry["started_at"]
    assert entry["finished_at"]


def test_backup_database_main_records_failure_status(tmp_path, monkeypatch):
    database_path = tmp_path / "data" / "missing.sqlite3"
    status_path = tmp_path / "data" / "backup_status.json"

    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("DATABASE_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(sys, "argv", ["backup_database.py"])

    from scripts.backup_database import main

    assert main() == 1

    status = load_backup_status(status_path)
    entry = status["database"]
    assert entry["success"] is False
    assert "数据库文件不存在" in entry["error"]


def test_backup_data_main_records_success_status(tmp_path, monkeypatch):
    uploads_dir = tmp_path / "data" / "uploads"
    uploads_dir.mkdir(parents=True)
    (uploads_dir / "a.txt").write_text("alpha", encoding="utf-8")
    status_path = tmp_path / "data" / "backup_status.json"

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("UPLOADS_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(sys, "argv", ["backup_data.py"])

    from scripts.backup_data import main

    assert main() == 0

    status = load_backup_status(status_path)
    entry = status["uploads"]
    assert entry["success"] is True
    assert "uploads-" in entry["artifact"]


def test_admin_account_page_shows_backup_status_and_disk_space(tmp_path, monkeypatch):
    configure_temp_app(tmp_path, monkeypatch)
    user = admin_user()
    status_path = app.DATA_DIR / "backup_status.json"
    record_backup_status(
        status_path,
        "database",
        True,
        artifact="/backups/file_manager-20260715T010000Z.sqlite3",
        started_at="2026-07-15T01:00:00+00:00",
        finished_at="2026-07-15T01:00:05+00:00",
    )
    record_backup_status(
        status_path,
        "uploads",
        False,
        error="磁盘空间不足",
        started_at="2026-07-15T02:00:00+00:00",
        finished_at="2026-07-15T02:00:01+00:00",
    )

    client = authenticated_client(user["id"])
    response = client.get("/account")

    assert response.status_code == 200
    assert "备份状态与磁盘空间" in response.text
    assert "数据库备份" in response.text
    assert "file_manager-20260715T010000Z.sqlite3" in response.text
    assert "上传文件备份" in response.text
    assert "磁盘空间不足" in response.text
    assert "磁盘剩余空间" in response.text
