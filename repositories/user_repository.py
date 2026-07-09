from __future__ import annotations

import sqlite3


def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, username, is_admin, created_at FROM users ORDER BY id"
    ).fetchall()


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, username, password_hash, is_admin, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()


def get_user_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, username, password_hash, is_admin, created_at FROM users WHERE username = ?",
        (username,),
    ).fetchone()


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password_hash: str,
    is_admin: bool,
    created_at: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO users (username, password_hash, is_admin, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (username, password_hash, int(is_admin), created_at),
    )
    return int(cursor.lastrowid)


def update_user_password(conn: sqlite3.Connection, user_id: int, password_hash: str) -> None:
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))


def update_user_admin_status(conn: sqlite3.Connection, user_id: int, is_admin: bool) -> None:
    conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(is_admin), user_id))


def count_admin_users(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS total FROM users WHERE is_admin = 1").fetchone()
    return int(row["total"])
