from __future__ import annotations

import sqlite3
from typing import Any


DOCUMENT_PAGE_SIZE = 10


def document_filter_clause(category_id: int | None, q: str) -> tuple[str, list[Any]]:
    filters: list[str] = []
    params: list[Any] = []

    if category_id:
        filters.append("d.category_id = ?")
        params.append(category_id)

    cleaned_query = q.strip()
    if cleaned_query:
        filters.append("(d.title LIKE ? OR v.original_filename LIKE ?)")
        like = f"%{cleaned_query}%"
        params.extend([like, like])

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    return where_clause, params


def count_documents(conn: sqlite3.Connection, category_id: int | None, q: str) -> int:
    where_clause, params = document_filter_clause(category_id, q)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM documents d
        LEFT JOIN document_versions v ON v.id = d.current_version_id
        {where_clause}
        """,
        params,
    ).fetchone()
    return int(row["total"])


def list_documents(
    conn: sqlite3.Connection,
    category_id: int | None,
    q: str,
    page: int,
    page_size: int = DOCUMENT_PAGE_SIZE,
) -> list[sqlite3.Row]:
    where_clause, params = document_filter_clause(category_id, q)
    offset = (max(page, 1) - 1) * page_size
    return conn.execute(
        f"""
        SELECT
            d.id, d.title, d.created_at, d.updated_at,
            c.name AS category_name,
            v.original_filename, v.size_bytes, v.version_number,
            v.created_at AS current_version_created_at
        FROM documents d
        LEFT JOIN categories c ON c.id = d.category_id
        LEFT JOIN document_versions v ON v.id = d.current_version_id
        {where_clause}
        ORDER BY v.created_at DESC, d.updated_at DESC, d.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, offset],
    ).fetchall()


def list_categories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()


def get_document_detail(conn: sqlite3.Connection, document_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT d.*, c.name AS category_name, u.username AS owner_name
        FROM documents d
        LEFT JOIN categories c ON c.id = d.category_id
        LEFT JOIN users u ON u.id = d.owner_id
        WHERE d.id = ?
        """,
        (document_id,),
    ).fetchone()


def list_document_versions(conn: sqlite3.Connection, document_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT v.*, u.username AS uploaded_by_name
        FROM document_versions v
        LEFT JOIN users u ON u.id = v.uploaded_by
        WHERE v.document_id = ?
        ORDER BY v.version_number DESC
        """,
        (document_id,),
    ).fetchall()


def get_current_version(conn: sqlite3.Connection, document_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT v.*
        FROM documents d
        JOIN document_versions v ON v.id = d.current_version_id
        WHERE d.id = ?
        """,
        (document_id,),
    ).fetchone()


def get_version(conn: sqlite3.Connection, document_id: int, version_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM document_versions WHERE id = ? AND document_id = ?",
        (version_id, document_id),
    ).fetchone()
