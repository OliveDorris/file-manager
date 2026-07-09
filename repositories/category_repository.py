from __future__ import annotations

import sqlite3


def create_category(conn: sqlite3.Connection, name: str, description: str, created_at: str) -> int:
    cursor = conn.execute(
        "INSERT OR IGNORE INTO categories (name, description, created_at) VALUES (?, ?, ?)",
        (name, description, created_at),
    )
    return int(cursor.lastrowid or 0)


def get_category(conn: sqlite3.Connection, category_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, name FROM categories WHERE id = ?",
        (category_id,),
    ).fetchone()


def count_documents_in_category(conn: sqlite3.Connection, category_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM documents WHERE category_id = ?",
        (category_id,),
    ).fetchone()
    return int(row["total"])


def delete_category(conn: sqlite3.Connection, category_id: int) -> None:
    conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
