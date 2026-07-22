from __future__ import annotations

import sqlite3


def initialize_share_link_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS share_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            password_hash TEXT,
            expires_at TEXT,
            allow_download INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT,
            FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_share_links_token
        ON share_links(token);
        """
    )


def create_share_link(
    conn: sqlite3.Connection,
    document_id: int,
    token: str,
    password_hash: str | None,
    expires_at: str | None,
    allow_download: bool,
    created_by: int,
    created_at: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO share_links (
            document_id, token, password_hash, expires_at, allow_download, created_by, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (document_id, token, password_hash, expires_at, int(allow_download), created_by, created_at),
    )
    return int(cursor.lastrowid)


def get_share_link_by_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            sl.*,
            d.title AS document_title,
            d.deleted_at AS document_deleted_at,
            d.current_version_id AS document_current_version_id
        FROM share_links sl
        JOIN documents d ON d.id = sl.document_id
        WHERE sl.token = ?
        """,
        (token,),
    ).fetchone()


def get_share_link(conn: sqlite3.Connection, share_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            sl.*,
            d.title AS document_title,
            creator.username AS creator_name
        FROM share_links sl
        JOIN documents d ON d.id = sl.document_id
        LEFT JOIN users creator ON creator.id = sl.created_by
        WHERE sl.id = ?
        """,
        (share_id,),
    ).fetchone()


def list_share_links_for_document(conn: sqlite3.Connection, document_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            sl.*,
            creator.username AS creator_name
        FROM share_links sl
        LEFT JOIN users creator ON creator.id = sl.created_by
        WHERE sl.document_id = ?
        ORDER BY sl.id DESC
        """,
        (document_id,),
    ).fetchall()


def revoke_share_link(conn: sqlite3.Connection, share_id: int, revoked_at: str) -> bool:
    cursor = conn.execute(
        "UPDATE share_links SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
        (revoked_at, share_id),
    )
    return cursor.rowcount == 1
