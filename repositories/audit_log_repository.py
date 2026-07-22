from __future__ import annotations

import sqlite3


AUDIT_LOG_PAGE_SIZE = 20


def initialize_audit_log_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            ip TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at
        ON audit_logs(created_at);

        CREATE INDEX IF NOT EXISTS idx_audit_logs_username
        ON audit_logs(username);

        CREATE INDEX IF NOT EXISTS idx_audit_logs_action
        ON audit_logs(action);
        """
    )


def insert_audit_log(
    conn: sqlite3.Connection,
    username: str,
    ip: str,
    action: str,
    detail: str,
    created_at: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO audit_logs (username, ip, action, detail, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (username, ip, action, detail, created_at),
    )
    return int(cursor.lastrowid)


def _build_filters(
    username: str,
    action: str,
    start_date: str,
    end_date: str,
) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    cleaned_username = username.strip()
    if cleaned_username:
        clauses.append("username LIKE ?")
        params.append(f"%{cleaned_username}%")
    cleaned_action = action.strip()
    if cleaned_action:
        clauses.append("action = ?")
        params.append(cleaned_action)
    if start_date.strip():
        clauses.append("date(created_at) >= date(?)")
        params.append(start_date.strip())
    if end_date.strip():
        clauses.append("date(created_at) <= date(?)")
        params.append(end_date.strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def count_audit_logs(
    conn: sqlite3.Connection,
    username: str = "",
    action: str = "",
    start_date: str = "",
    end_date: str = "",
) -> int:
    where, params = _build_filters(username, action, start_date, end_date)
    row = conn.execute(f"SELECT COUNT(*) AS total FROM audit_logs {where}", params).fetchone()
    return int(row["total"])


def list_audit_logs(
    conn: sqlite3.Connection,
    page: int,
    username: str = "",
    action: str = "",
    start_date: str = "",
    end_date: str = "",
) -> list[sqlite3.Row]:
    where, params = _build_filters(username, action, start_date, end_date)
    offset = (max(page, 1) - 1) * AUDIT_LOG_PAGE_SIZE
    return conn.execute(
        f"""
        SELECT id, username, ip, action, detail, created_at
        FROM audit_logs
        {where}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, AUDIT_LOG_PAGE_SIZE, offset],
    ).fetchall()


def list_audit_actions(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT action FROM audit_logs ORDER BY action"
    ).fetchall()
    return [str(row["action"]) for row in rows]
