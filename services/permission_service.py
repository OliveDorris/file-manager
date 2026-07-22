from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from repositories.access_request_repository import (
    access_request_exists,
    create_access_request,
    get_access_request,
    get_access_status_map,
    has_active_grant,
    revoke_access_request,
    update_access_request_status,
)


ACTION_DOWNLOAD = "download"
ACTION_UPLOAD_VERSION = "upload_version"
VALID_ACCESS_ACTIONS = {ACTION_DOWNLOAD, ACTION_UPLOAD_VERSION}
ACCESS_ACTION_LABELS = {
    ACTION_DOWNLOAD: "下载和预览",
    ACTION_UPLOAD_VERSION: "覆盖新版本",
}
ACCESS_STATUS_LABELS = {
    "pending": "待审批",
    "approved": "已通过",
    "rejected": "已拒绝",
    "revoked": "已撤销",
}
VALID_ACCESS_STATUSES = set(ACCESS_STATUS_LABELS)


def current_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_validity_days(value: str) -> int | None:
    """审批有效期：空为永久，否则为大于 0 的天数。"""
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        days = int(cleaned)
    except ValueError as exc:
        raise ValueError("有效期无效") from exc
    if days <= 0:
        raise ValueError("有效期无效")
    return days


def _record_value(record: Mapping[str, Any] | sqlite3.Row, key: str) -> Any:
    return record[key]


def is_admin(user: Mapping[str, Any]) -> bool:
    return bool(user.get("is_admin"))


def is_document_owner(
    user: Mapping[str, Any],
    document: Mapping[str, Any] | sqlite3.Row,
) -> bool:
    return int(_record_value(document, "owner_id")) == int(user["id"])


def can_manage_document(
    user: Mapping[str, Any],
    document: Mapping[str, Any] | sqlite3.Row,
) -> bool:
    return is_admin(user) or is_document_owner(user, document)


def build_document_access_flags(
    conn: sqlite3.Connection,
    user: Mapping[str, Any],
    documents: list[Mapping[str, Any] | sqlite3.Row],
) -> list[dict[str, Any]]:
    document_ids = [int(_record_value(document, "id")) for document in documents]
    status_map = (
        {}
        if is_admin(user)
        else get_access_status_map(conn, int(user["id"]), document_ids, current_iso())
    )
    result: list[dict[str, Any]] = []

    for document in documents:
        item = dict(document)
        document_id = int(item["id"])
        owner_or_admin = can_manage_document(user, document)
        download_status = status_map.get(
            (document_id, ACTION_DOWNLOAD),
            {"approved": False, "pending": False},
        )
        upload_status = status_map.get(
            (document_id, ACTION_UPLOAD_VERSION),
            {"approved": False, "pending": False},
        )
        item.update(
            {
                "can_manage": owner_or_admin,
                "can_download": owner_or_admin or download_status["approved"],
                "can_upload_version": owner_or_admin or upload_status["approved"],
                "download_request_pending": download_status["pending"],
                "upload_version_request_pending": upload_status["pending"],
            }
        )
        result.append(item)

    return result


def build_document_access_flags_one(
    conn: sqlite3.Connection,
    user: Mapping[str, Any],
    document: Mapping[str, Any] | sqlite3.Row,
) -> dict[str, Any]:
    return build_document_access_flags(conn, user, [document])[0]


def submit_access_request(
    conn: sqlite3.Connection,
    user: Mapping[str, Any],
    document: Mapping[str, Any] | sqlite3.Row,
    action: str,
    created_at: str,
) -> tuple[int | None, str]:
    if action not in VALID_ACCESS_ACTIONS:
        raise ValueError("申请类型无效")
    if can_manage_document(user, document):
        return None, "你已经拥有该文件的操作权限"

    requester_id = int(user["id"])
    document_id = int(_record_value(document, "id"))
    if has_active_grant(conn, requester_id, document_id, action, current_iso()):
        return None, "申请已通过，可以继续操作该文件"
    if access_request_exists(conn, requester_id, document_id, action, "pending"):
        return None, "申请已提交，请等待管理员处理"

    request_id = create_access_request(conn, requester_id, document_id, action, created_at)
    return request_id, "申请已提交，请等待管理员处理"


def submit_download_access_requests(
    conn: sqlite3.Connection,
    user: Mapping[str, Any],
    documents: list[Mapping[str, Any] | sqlite3.Row],
    created_at: str,
) -> dict[str, Any]:
    flagged_documents = build_document_access_flags(conn, user, documents)
    created_request_ids: list[int] = []
    accessible_count = 0
    pending_count = 0

    for document in flagged_documents:
        if document["can_download"]:
            accessible_count += 1
            continue
        if document["download_request_pending"]:
            pending_count += 1
            continue
        request_id, _ = submit_access_request(
            conn,
            user,
            document,
            ACTION_DOWNLOAD,
            created_at,
        )
        if request_id is not None:
            created_request_ids.append(request_id)

    return {
        "created_request_ids": created_request_ids,
        "created_count": len(created_request_ids),
        "accessible_count": accessible_count,
        "pending_count": pending_count,
    }


def review_access_request(
    conn: sqlite3.Connection,
    request_id: int,
    reviewer_id: int,
    decision: str,
    reviewed_at: str,
    expires_at: str | None = None,
) -> sqlite3.Row:
    if decision not in {"approved", "rejected"}:
        raise ValueError("审批结果无效")
    access_request = get_access_request(conn, request_id)
    if not access_request:
        raise ValueError("申请不存在")
    if access_request["status"] != "pending":
        raise ValueError("该申请已经处理")
    if decision != "approved":
        expires_at = None
    if not update_access_request_status(conn, request_id, decision, reviewer_id, reviewed_at, expires_at):
        raise ValueError("该申请已经处理")
    return access_request


def revoke_access_request_grant(
    conn: sqlite3.Connection,
    request_id: int,
    user: Mapping[str, Any],
    reviewed_at: str,
) -> sqlite3.Row:
    """撤销已授予的权限：管理员或文件所有者可操作。"""
    access_request = get_access_request(conn, request_id)
    if not access_request:
        raise ValueError("申请不存在")
    if not is_admin(user) and int(access_request["document_owner_id"]) != int(user["id"]):
        raise PermissionError("没有权限撤销该授权")
    if access_request["status"] != "approved":
        raise ValueError("该授权已失效，无需撤销")
    if not revoke_access_request(conn, request_id, int(user["id"]), reviewed_at):
        raise ValueError("该授权已失效，无需撤销")
    return access_request


def access_action_label(action: str) -> str:
    return ACCESS_ACTION_LABELS.get(action, action)


def access_status_label(status: str) -> str:
    return ACCESS_STATUS_LABELS.get(status, status)


def is_grant_expired(expires_at: Any, now: str) -> bool:
    return bool(expires_at) and str(expires_at) <= now
