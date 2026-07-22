from __future__ import annotations

import logging
import sqlite3


logger = logging.getLogger("file_manager.search")


def fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.fts5_probe USING fts5(probe)")
    except sqlite3.OperationalError as exc:
        logger.warning("SQLite FTS5 不可用，全文搜索降级为文件名搜索：%s", exc)
        return False
    conn.execute("DROP TABLE temp.fts5_probe")
    return True


def initialize_search_schema(conn: sqlite3.Connection) -> bool:
    if not fts5_available(conn):
        return False
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS document_fts
        USING fts5(title, content, document_id UNINDEXED)
        """
    )
    return True


def upsert_document_index(
    conn: sqlite3.Connection,
    document_id: int,
    title: str,
    content: str,
) -> None:
    conn.execute("DELETE FROM document_fts WHERE document_id = ?", (document_id,))
    conn.execute(
        "INSERT INTO document_fts (title, content, document_id) VALUES (?, ?, ?)",
        (title, content, document_id),
    )


def delete_document_index(conn: sqlite3.Connection, document_id: int) -> None:
    conn.execute("DELETE FROM document_fts WHERE document_id = ?", (document_id,))


def search_document_ids(conn: sqlite3.Connection, match_query: str) -> list[int]:
    rows = conn.execute(
        "SELECT document_id FROM document_fts WHERE document_fts MATCH ?",
        (match_query,),
    ).fetchall()
    return [int(row["document_id"]) for row in rows]


def list_indexed_contents(conn: sqlite3.Connection, document_ids: list[int]) -> list[sqlite3.Row]:
    if not document_ids:
        return []
    placeholders = ",".join("?" for _ in document_ids)
    return conn.execute(
        f"SELECT document_id, title, content FROM document_fts WHERE document_id IN ({placeholders})",
        document_ids,
    ).fetchall()


def list_unindexed_document_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        """
        SELECT id FROM documents
        WHERE deleted_at IS NULL
          AND id NOT IN (SELECT document_id FROM document_fts)
        ORDER BY id
        """
    ).fetchall()
    return [int(row["id"]) for row in rows]
