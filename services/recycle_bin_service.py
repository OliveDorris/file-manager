from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from repositories.document_repository import (
    list_retention_expired_document_ids,
    purge_documents_by_ids,
)
from services.document_service import remove_many_document_files
from services.permission_service import can_manage_document


def recycle_retention_days() -> int:
    """回收站保留天数，0 表示不自动清理。"""
    try:
        return max(int(os.getenv("RECYCLE_RETENTION_DAYS", "0")), 0)
    except ValueError:
        return 0


def can_recycle_document(user: Mapping[str, Any], document: Mapping[str, Any] | sqlite3.Row) -> bool:
    """恢复或彻底删除回收站文件：文件所有者或管理员，且文件确实在回收站。"""
    return bool(document["deleted_at"]) and can_manage_document(user, document)


def purge_documents_completely(
    conn: sqlite3.Connection,
    upload_dir: Path,
    document_ids: list[int],
) -> int:
    """彻底删除回收站中的文档：提交数据库删除（级联删版本和申请）后清理上传目录。"""
    purged_count = purge_documents_by_ids(conn, document_ids)
    if purged_count:
        conn.commit()
        remove_many_document_files(upload_dir, document_ids)
    return purged_count


def purge_retention_expired_documents(
    conn: sqlite3.Connection,
    upload_dir: Path,
    retention_days: int,
) -> int:
    """清理回收站中超过保留天数的文档，返回清理数量。"""
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat(timespec="seconds")
    expired_ids = list_retention_expired_document_ids(conn, cutoff)
    if not expired_ids:
        return 0
    return purge_documents_completely(conn, upload_dir, expired_ids)
