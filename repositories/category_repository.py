from __future__ import annotations

import sqlite3


def create_category(
    conn: sqlite3.Connection,
    name: str,
    description: str,
    parent_id: int | None,
    created_at: str,
) -> int:
    cursor = conn.execute(
        "INSERT INTO categories (name, description, parent_id, created_at) VALUES (?, ?, ?, ?)",
        (name, description, parent_id, created_at),
    )
    return int(cursor.lastrowid)


def get_category(conn: sqlite3.Connection, category_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, name, parent_id FROM categories WHERE id = ?",
        (category_id,),
    ).fetchone()


def list_categories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, name, parent_id FROM categories ORDER BY name, id"
    ).fetchall()


def get_category_depth(conn: sqlite3.Connection, category_id: int) -> int | None:
    row = conn.execute(
        """
        WITH RECURSIVE ancestors(id, parent_id, depth) AS (
            SELECT id, parent_id, 1
            FROM categories
            WHERE id = ?
            UNION ALL
            SELECT c.id, c.parent_id, ancestors.depth + 1
            FROM categories c
            JOIN ancestors ON c.id = ancestors.parent_id
        )
        SELECT MAX(depth) AS depth FROM ancestors
        """,
        (category_id,),
    ).fetchone()
    if not row or row["depth"] is None:
        return None
    return int(row["depth"])


def count_documents_in_category(conn: sqlite3.Connection, category_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM documents WHERE category_id = ? AND deleted_at IS NULL",
        (category_id,),
    ).fetchone()
    return int(row["total"])


def count_child_categories(conn: sqlite3.Connection, category_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM categories WHERE parent_id = ?",
        (category_id,),
    ).fetchone()
    return int(row["total"])


def delete_category(conn: sqlite3.Connection, category_id: int) -> None:
    conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
