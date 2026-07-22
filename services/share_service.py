from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from repositories.share_link_repository import (
    create_share_link,
    get_share_link,
    get_share_link_by_token,
    revoke_share_link,
)
from services.permission_service import can_manage_document, is_admin, parse_validity_days


def _hash_share_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_share_password(stored_hash: str, password: str) -> bool:
    try:
        algorithm, salt, expected_digest = stored_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = _hash_share_password(password, salt).split("$", 2)[2]
    return hmac.compare_digest(candidate, expected_digest)


def build_share_expires_at(validity: str, now: datetime) -> str | None:
    days = parse_validity_days(validity)
    if not days:
        return None
    return (now + timedelta(days=days)).isoformat(timespec="seconds")


def create_document_share_link(
    conn: sqlite3.Connection,
    user: Mapping[str, Any],
    document: Mapping[str, Any] | sqlite3.Row,
    password: str,
    validity: str,
    allow_download: bool,
    created_at: str,
) -> str:
    """创建分享链接：仅文件所有者或管理员，返回公开访问 token。"""
    if not can_manage_document(user, document):
        raise PermissionError("只能分享自己上传的文件")
    expires_at = build_share_expires_at(validity, datetime.now(timezone.utc))
    token = secrets.token_urlsafe(24)
    create_share_link(
        conn,
        int(document["id"]),
        token,
        _hash_share_password(password) if password else None,
        expires_at,
        allow_download,
        int(user["id"]),
        created_at,
    )
    return token


def get_active_share_link(
    conn: sqlite3.Connection,
    token: str,
    now: str,
) -> sqlite3.Row | None:
    """解析公开访问的分享链接：不存在、已撤销、已过期或文档已删除时返回 None。"""
    share = get_share_link_by_token(conn, token)
    if not share:
        return None
    if share["revoked_at"]:
        return None
    if share["expires_at"] and str(share["expires_at"]) <= now:
        return None
    if share["document_deleted_at"]:
        return None
    return share


def revoke_document_share_link(
    conn: sqlite3.Connection,
    share_id: int,
    user: Mapping[str, Any],
    revoked_at: str,
) -> sqlite3.Row:
    """撤销分享链接：创建人或管理员可操作。"""
    share = get_share_link(conn, share_id)
    if not share:
        raise ValueError("分享链接不存在")
    if not is_admin(user) and int(share["created_by"]) != int(user["id"]):
        raise PermissionError("没有权限撤销该分享链接")
    if share["revoked_at"]:
        raise ValueError("分享链接已撤销")
    if not revoke_share_link(conn, share_id, revoked_at):
        raise ValueError("分享链接已撤销")
    return share


def share_link_status_label(share: Mapping[str, Any] | sqlite3.Row, now: str) -> str:
    if share["revoked_at"]:
        return "已撤销"
    if share["expires_at"] and str(share["expires_at"]) <= now:
        return "已过期"
    return "有效"
