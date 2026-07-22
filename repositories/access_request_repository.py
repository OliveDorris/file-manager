from __future__ import annotations

import sqlite3
from typing import Any


ACCESS_REQUEST_PAGE_SIZE = 20


def initialize_access_request_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS access_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewer_id INTEGER,
            expires_at TEXT,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            FOREIGN KEY(requester_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
            FOREIGN KEY(reviewer_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_access_requests_pending
        ON access_requests(status, created_at);

        CREATE INDEX IF NOT EXISTS idx_access_requests_lookup
        ON access_requests(requester_id, document_id, action, status);
        """
    )
    request_columns = {row["name"] for row in conn.execute("PRAGMA table_info(access_requests)").fetchall()}
    if "expires_at" not in request_columns:
        conn.execute("ALTER TABLE access_requests ADD COLUMN expires_at TEXT")


def count_pending_access_requests(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM access_requests WHERE status = 'pending'"
    ).fetchone()
    return int(row["total"])


def list_pending_access_requests(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            ar.*,
            requester.username AS requester_name,
            d.title AS document_title
        FROM access_requests ar
        JOIN users requester ON requester.id = ar.requester_id
        JOIN documents d ON d.id = ar.document_id
        WHERE ar.status = 'pending'
        ORDER BY ar.created_at ASC, ar.id ASC
        """
    ).fetchall()


def get_access_request(conn: sqlite3.Connection, request_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            ar.*,
            requester.username AS requester_name,
            d.title AS document_title,
            d.owner_id AS document_owner_id
        FROM access_requests ar
        JOIN users requester ON requester.id = ar.requester_id
        JOIN documents d ON d.id = ar.document_id
        WHERE ar.id = ?
        """,
        (request_id,),
    ).fetchone()


def create_access_request(
    conn: sqlite3.Connection,
    requester_id: int,
    document_id: int,
    action: str,
    created_at: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO access_requests (requester_id, document_id, action, status, created_at)
        VALUES (?, ?, ?, 'pending', ?)
        """,
        (requester_id, document_id, action, created_at),
    )
    return int(cursor.lastrowid)


def update_access_request_status(
    conn: sqlite3.Connection,
    request_id: int,
    status: str,
    reviewer_id: int,
    reviewed_at: str,
    expires_at: str | None = None,
) -> bool:
    cursor = conn.execute(
        """
        UPDATE access_requests
        SET status = ?, reviewer_id = ?, reviewed_at = ?, expires_at = ?
        WHERE id = ? AND status = 'pending'
        """,
        (status, reviewer_id, reviewed_at, expires_at, request_id),
    )
    return cursor.rowcount == 1


def revoke_access_request(
    conn: sqlite3.Connection,
    request_id: int,
    reviewer_id: int,
    reviewed_at: str,
) -> bool:
    cursor = conn.execute(
        """
        UPDATE access_requests
        SET status = 'revoked', reviewer_id = ?, reviewed_at = ?
        WHERE id = ? AND status = 'approved'
        """,
        (reviewer_id, reviewed_at, request_id),
    )
    return cursor.rowcount == 1


def has_active_grant(
    conn: sqlite3.Connection,
    requester_id: int,
    document_id: int,
    action: str,
    now: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM access_requests
        WHERE requester_id = ? AND document_id = ? AND action = ?
          AND status = 'approved'
          AND (expires_at IS NULL OR expires_at > ?)
        LIMIT 1
        """,
        (requester_id, document_id, action, now),
    ).fetchone()
    return row is not None


def get_access_status_map(
    conn: sqlite3.Connection,
    requester_id: int,
    document_ids: list[int],
    now: str,
) -> dict[tuple[int, str], dict[str, bool]]:
    if not document_ids:
        return {}
    placeholders = ",".join("?" for _ in document_ids)
    rows = conn.execute(
        f"""
        SELECT
            document_id,
            action,
            MAX(CASE WHEN status = 'approved' AND (expires_at IS NULL OR expires_at > ?) THEN 1 ELSE 0 END) AS approved,
            MAX(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending
        FROM access_requests
        WHERE requester_id = ? AND document_id IN ({placeholders})
        GROUP BY document_id, action
        """,
        [now, requester_id, *document_ids],
    ).fetchall()
    return {
        (int(row["document_id"]), str(row["action"])): {
            "approved": bool(row["approved"]),
            "pending": bool(row["pending"]),
        }
        for row in rows
    }


def access_request_exists(
    conn: sqlite3.Connection,
    requester_id: int,
    document_id: int,
    action: str,
    status: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM access_requests
        WHERE requester_id = ? AND document_id = ? AND action = ? AND status = ?
        LIMIT 1
        """,
        (requester_id, document_id, action, status),
    ).fetchone()
    return row is not None


def _build_history_filters(
    requester: str,
    status: str,
    user_id: int | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if user_id is not None:
        clauses.append("(ar.requester_id = ? OR d.owner_id = ?)")
        params.extend([user_id, user_id])
    cleaned_requester = requester.strip()
    if cleaned_requester:
        clauses.append("requester.username LIKE ?")
        params.append(f"%{cleaned_requester}%")
    cleaned_status = status.strip()
    if cleaned_status:
        clauses.append("ar.status = ?")
        params.append(cleaned_status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


_HISTORY_SELECT = """
    SELECT
        ar.*,
        requester.username AS requester_name,
        reviewer.username AS reviewer_name,
        d.title AS document_title,
        d.owner_id AS document_owner_id
    FROM access_requests ar
    JOIN users requester ON requester.id = ar.requester_id
    LEFT JOIN users reviewer ON reviewer.id = ar.reviewer_id
    JOIN documents d ON d.id = ar.document_id
"""


def count_access_requests(
    conn: sqlite3.Connection,
    requester: str = "",
    status: str = "",
    user_id: int | None = None,
) -> int:
    where, params = _build_history_filters(requester, status, user_id)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM access_requests ar
        JOIN users requester ON requester.id = ar.requester_id
        JOIN documents d ON d.id = ar.document_id
        {where}
        """,
        params,
    ).fetchone()
    return int(row["total"])


def list_access_requests(
    conn: sqlite3.Connection,
    page: int,
    requester: str = "",
    status: str = "",
    user_id: int | None = None,
) -> list[sqlite3.Row]:
    where, params = _build_history_filters(requester, status, user_id)
    offset = (max(page, 1) - 1) * ACCESS_REQUEST_PAGE_SIZE
    return conn.execute(
        f"""
        {_HISTORY_SELECT}
        {where}
        ORDER BY ar.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, ACCESS_REQUEST_PAGE_SIZE, offset],
    ).fetchall()
