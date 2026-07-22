from __future__ import annotations

import sqlite3
from typing import Any


DOCUMENT_PAGE_SIZE = 10


def document_filter_clause(
    category_id: int | None,
    q: str,
    matched_ids: list[int] | None = None,
) -> tuple[str, list[Any]]:
    filters: list[str] = ["d.deleted_at IS NULL"]
    params: list[Any] = []

    if category_id:
        filters.append(
            """
            d.category_id IN (
                WITH RECURSIVE category_tree(id) AS (
                    SELECT id FROM categories WHERE id = ?
                    UNION ALL
                    SELECT c.id
                    FROM categories c
                    JOIN category_tree ON c.parent_id = category_tree.id
                )
                SELECT id FROM category_tree
            )
            """
        )
        params.append(category_id)

    if matched_ids is not None:
        if matched_ids:
            filters.append(f"d.id IN ({id_placeholders(matched_ids)})")
            params.extend(matched_ids)
        else:
            filters.append("1 = 0")
    else:
        cleaned_query = q.strip()
        if cleaned_query:
            filters.append("(d.title LIKE ? OR v.original_filename LIKE ?)")
            like = f"%{cleaned_query}%"
            params.extend([like, like])

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    return where_clause, params


def id_placeholders(values: list[int]) -> str:
    return ",".join("?" for _ in values)


def count_documents(
    conn: sqlite3.Connection,
    category_id: int | None,
    q: str,
    matched_ids: list[int] | None = None,
) -> int:
    where_clause, params = document_filter_clause(category_id, q, matched_ids)
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
    matched_ids: list[int] | None = None,
) -> list[sqlite3.Row]:
    where_clause, params = document_filter_clause(category_id, q, matched_ids)
    offset = (max(page, 1) - 1) * page_size
    return conn.execute(
        f"""
        SELECT
            d.id, d.title, d.owner_id, d.created_at, d.updated_at,
            c.name AS category_name,
            u.username AS owner_name,
            v.original_filename, v.size_bytes, v.version_number,
            v.created_at AS current_version_created_at
        FROM documents d
        LEFT JOIN categories c ON c.id = d.category_id
        LEFT JOIN users u ON u.id = d.owner_id
        LEFT JOIN document_versions v ON v.id = d.current_version_id
        {where_clause}
        ORDER BY v.created_at DESC, d.updated_at DESC, d.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, offset],
    ).fetchall()


def list_documents_by_ids(conn: sqlite3.Connection, document_ids: list[int]) -> list[sqlite3.Row]:
    if not document_ids:
        return []
    return conn.execute(
        f"""
        SELECT id, title, owner_id
        FROM documents
        WHERE deleted_at IS NULL AND id IN ({id_placeholders(document_ids)})
        ORDER BY id
        """,
        document_ids,
    ).fetchall()


def list_current_versions_for_documents(
    conn: sqlite3.Connection,
    document_ids: list[int],
) -> list[sqlite3.Row]:
    if not document_ids:
        return []
    return conn.execute(
        f"""
        SELECT
            d.id, d.title, d.owner_id,
            v.original_filename, v.stored_filename, v.content_type
        FROM documents d
        JOIN document_versions v ON v.id = d.current_version_id
        WHERE d.deleted_at IS NULL AND d.id IN ({id_placeholders(document_ids)})
        ORDER BY d.id
        """,
        document_ids,
    ).fetchall()


def soft_delete_documents_by_ids(
    conn: sqlite3.Connection,
    document_ids: list[int],
    deleted_at: str,
) -> int:
    if not document_ids:
        return 0
    cursor = conn.execute(
        f"""
        UPDATE documents
        SET deleted_at = ?
        WHERE deleted_at IS NULL AND id IN ({id_placeholders(document_ids)})
        """,
        [deleted_at, *document_ids],
    )
    return int(cursor.rowcount)


def restore_document(conn: sqlite3.Connection, document_id: int) -> bool:
    cursor = conn.execute(
        "UPDATE documents SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
        (document_id,),
    )
    return cursor.rowcount == 1


def purge_documents_by_ids(conn: sqlite3.Connection, document_ids: list[int]) -> int:
    """彻底删除已软删除的文档记录（版本和申请记录随外键级联删除）。"""
    if not document_ids:
        return 0
    cursor = conn.execute(
        f"""
        DELETE FROM documents
        WHERE deleted_at IS NOT NULL AND id IN ({id_placeholders(document_ids)})
        """,
        document_ids,
    )
    return int(cursor.rowcount)


def list_deleted_documents(
    conn: sqlite3.Connection,
    owner_id: int | None = None,
) -> list[sqlite3.Row]:
    filters = ["d.deleted_at IS NOT NULL"]
    params: list[Any] = []
    if owner_id is not None:
        filters.append("d.owner_id = ?")
        params.append(owner_id)
    return conn.execute(
        f"""
        SELECT
            d.id, d.title, d.owner_id, d.deleted_at,
            c.name AS category_name,
            u.username AS owner_name
        FROM documents d
        LEFT JOIN categories c ON c.id = d.category_id
        LEFT JOIN users u ON u.id = d.owner_id
        WHERE {' AND '.join(filters)}
        ORDER BY d.deleted_at DESC, d.id DESC
        """,
        params,
    ).fetchall()


def list_retention_expired_document_ids(conn: sqlite3.Connection, cutoff: str) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM documents WHERE deleted_at IS NOT NULL AND deleted_at < ?",
        (cutoff,),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def list_categories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, name, parent_id FROM categories ORDER BY name, id"
    ).fetchall()


def get_document_detail(
    conn: sqlite3.Connection,
    document_id: int,
    include_deleted: bool = False,
) -> sqlite3.Row | None:
    deleted_filter = "" if include_deleted else "AND d.deleted_at IS NULL"
    return conn.execute(
        f"""
        SELECT d.*, c.name AS category_name, u.username AS owner_name
        FROM documents d
        LEFT JOIN categories c ON c.id = d.category_id
        LEFT JOIN users u ON u.id = d.owner_id
        WHERE d.id = ? {deleted_filter}
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
