from __future__ import annotations

import sqlite3
from typing import Any


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
            d.title AS document_title
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
) -> bool:
    cursor = conn.execute(
        """
        UPDATE access_requests
        SET status = ?, reviewer_id = ?, reviewed_at = ?
        WHERE id = ? AND status = 'pending'
        """,
        (status, reviewer_id, reviewed_at, request_id),
    )
    return cursor.rowcount == 1


def get_access_status_map(
    conn: sqlite3.Connection,
    requester_id: int,
    document_ids: list[int],
) -> dict[tuple[int, str], dict[str, bool]]:
    if not document_ids:
        return {}
    placeholders = ",".join("?" for _ in document_ids)
    rows = conn.execute(
        f"""
        SELECT
            document_id,
            action,
            MAX(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
            MAX(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending
        FROM access_requests
        WHERE requester_id = ? AND document_id IN ({placeholders})
        GROUP BY document_id, action
        """,
        [requester_id, *document_ids],
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
