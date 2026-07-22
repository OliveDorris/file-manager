from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.backup_data import backup_uploads, list_snapshots, restore_uploads


def create_uploads_tree(uploads_dir) -> None:
    (uploads_dir / "1").mkdir(parents=True)
    (uploads_dir / "2").mkdir(parents=True)
    (uploads_dir / "1" / "a.txt").write_text("alpha", encoding="utf-8")
    (uploads_dir / "2" / "b.txt").write_text("beta", encoding="utf-8")


def test_backup_uploads_creates_readable_snapshot(tmp_path):
    uploads_dir = tmp_path / "data" / "uploads"
    backup_dir = tmp_path / "backups"
    create_uploads_tree(uploads_dir)

    snapshot = backup_uploads(
        uploads_dir,
        backup_dir,
        created_at=datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc),
    )

    assert snapshot.name == "uploads-20260715T010203Z"
    assert (snapshot / "1" / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert (snapshot / "2" / "b.txt").read_text(encoding="utf-8") == "beta"


def test_backup_uploads_keeps_only_requested_number_of_snapshots(tmp_path):
    uploads_dir = tmp_path / "data" / "uploads"
    backup_dir = tmp_path / "backups"
    create_uploads_tree(uploads_dir)

    for hour in range(3):
        backup_uploads(
            uploads_dir,
            backup_dir,
            keep=2,
            created_at=datetime(2026, 7, 15, hour, tzinfo=timezone.utc),
        )

    snapshot_names = [path.name for path in list_snapshots(backup_dir)]
    assert snapshot_names == [
        "uploads-20260715T010000Z",
        "uploads-20260715T020000Z",
    ]


def test_backup_uploads_incremental_snapshot_tracks_changes(tmp_path):
    uploads_dir = tmp_path / "data" / "uploads"
    backup_dir = tmp_path / "backups"
    create_uploads_tree(uploads_dir)

    first = backup_uploads(
        uploads_dir,
        backup_dir,
        created_at=datetime(2026, 7, 15, 1, tzinfo=timezone.utc),
    )
    (uploads_dir / "1" / "a.txt").write_text("alpha-v2", encoding="utf-8")
    second = backup_uploads(
        uploads_dir,
        backup_dir,
        created_at=datetime(2026, 7, 15, 2, tzinfo=timezone.utc),
    )

    assert (first / "1" / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert (second / "1" / "a.txt").read_text(encoding="utf-8") == "alpha-v2"
    assert (second / "2" / "b.txt").read_text(encoding="utf-8") == "beta"


def test_restore_uploads_restores_files_to_target(tmp_path):
    uploads_dir = tmp_path / "data" / "uploads"
    backup_dir = tmp_path / "backups"
    create_uploads_tree(uploads_dir)

    snapshot = backup_uploads(
        uploads_dir,
        backup_dir,
        created_at=datetime(2026, 7, 15, 1, tzinfo=timezone.utc),
    )
    (uploads_dir / "1" / "a.txt").write_text("broken", encoding="utf-8")
    (uploads_dir / "2" / "b.txt").unlink()

    restored = restore_uploads(snapshot.name, backup_dir, uploads_dir)

    assert restored == uploads_dir.resolve()
    assert (uploads_dir / "1" / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert (uploads_dir / "2" / "b.txt").read_text(encoding="utf-8") == "beta"


def test_backup_uploads_rejects_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError, match="上传文件目录不存在"):
        backup_uploads(tmp_path / "missing", tmp_path / "backups")


def test_restore_uploads_rejects_missing_snapshot(tmp_path):
    with pytest.raises(FileNotFoundError, match="备份快照不存在"):
        restore_uploads("uploads-20990101T000000Z", tmp_path / "backups", tmp_path / "target")
