from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from repositories.search_repository import (
    delete_document_index,
    initialize_search_schema,
    list_indexed_contents,
    list_unindexed_document_ids,
    search_document_ids,
    upsert_document_index,
)


logger = logging.getLogger("file_manager.search")

DEFAULT_TEXT_INDEX_EXTENSIONS = {".txt", ".md", ".csv", ".log", ".json", ".xml", ".html", ".htm"}
DEFAULT_MAX_INDEX_BYTES = 2 * 1024 * 1024
SNIPPET_CONTEXT_CHARS = 30

FTS_ENABLED = False


def text_index_extensions() -> set[str]:
    configured = os.getenv("SEARCH_INDEX_EXTENSIONS", "").strip()
    if not configured:
        return set(DEFAULT_TEXT_INDEX_EXTENSIONS)
    return {
        extension.strip().lower()
        for extension in configured.split(",")
        if extension.strip()
    }


def max_index_bytes() -> int:
    try:
        return max(int(os.getenv("SEARCH_INDEX_MAX_BYTES", str(DEFAULT_MAX_INDEX_BYTES))), 1)
    except ValueError:
        return DEFAULT_MAX_INDEX_BYTES


def configure_search(conn: sqlite3.Connection, upload_dir: Path) -> bool:
    """启动时初始化全文索引：FTS5 不可用时降级为仅文件名搜索；对存量文档幂等补齐索引。"""
    global FTS_ENABLED
    FTS_ENABLED = initialize_search_schema(conn)
    if FTS_ENABLED:
        backfilled = backfill_missing_indexes(conn, upload_dir)
        if backfilled:
            logger.info("全文索引补齐完成：count=%s", backfilled)
    return FTS_ENABLED


def read_indexable_content(
    upload_dir: Path,
    document_id: int,
    original_filename: str,
    stored_filename: str,
) -> str:
    suffix = Path(original_filename).suffix.lower()
    if suffix not in text_index_extensions():
        return ""
    file_path = upload_dir / str(document_id) / stored_filename
    try:
        file_size = file_path.stat().st_size
    except OSError as exc:
        logger.warning("全文索引读取文件失败：document_id=%s error=%s", document_id, exc)
        return ""
    if file_size > max_index_bytes():
        return ""
    try:
        data = file_path.read_bytes()
    except OSError as exc:
        logger.warning("全文索引读取文件失败：document_id=%s error=%s", document_id, exc)
        return ""
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def index_document(conn: sqlite3.Connection, upload_dir: Path, document_id: int) -> None:
    document = conn.execute(
        """
        SELECT d.title, v.original_filename, v.stored_filename
        FROM documents d
        LEFT JOIN document_versions v ON v.id = d.current_version_id
        WHERE d.id = ? AND d.deleted_at IS NULL
        """,
        (document_id,),
    ).fetchone()
    if not document:
        delete_document_index(conn, document_id)
        return
    content = ""
    if document["stored_filename"]:
        content = read_indexable_content(
            upload_dir,
            document_id,
            str(document["original_filename"]),
            str(document["stored_filename"]),
        )
    upsert_document_index(conn, document_id, str(document["title"]), content)


def remove_document_index(conn: sqlite3.Connection, document_id: int) -> None:
    delete_document_index(conn, document_id)


def backfill_missing_indexes(conn: sqlite3.Connection, upload_dir: Path) -> int:
    count = 0
    for document_id in list_unindexed_document_ids(conn):
        index_document(conn, upload_dir, document_id)
        count += 1
    return count


def build_match_query(q: str) -> str | None:
    terms = q.split()
    if not terms:
        return None
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def find_matching_document_ids(conn: sqlite3.Connection, q: str) -> list[int] | None:
    """返回 FTS 命中的文档 id；FTS 不可用或查询为空时返回 None，调用方降级为文件名搜索。"""
    if not FTS_ENABLED or not q.strip():
        return None
    match_query = build_match_query(q)
    if not match_query:
        return None
    try:
        return search_document_ids(conn, match_query)
    except sqlite3.OperationalError as exc:
        logger.warning("全文搜索失败，降级为文件名搜索：query=%s error=%s", q, exc)
        return None


def _extract_snippet(content: str, terms: list[str]) -> str:
    lowered = content.lower()
    position = -1
    matched_term = ""
    for term in terms:
        position = lowered.find(term.lower())
        if position >= 0:
            matched_term = term
            break
    if position < 0:
        return ""
    start = max(position - SNIPPET_CONTEXT_CHARS, 0)
    end = min(position + len(matched_term) + SNIPPET_CONTEXT_CHARS, len(content))
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


def build_search_contexts(
    conn: sqlite3.Connection,
    document_ids: list[int],
    q: str,
) -> dict[int, dict[str, str]]:
    """为当前页结果生成命中类型和上下文片段（模板负责 HTML 转义）。"""
    terms = q.split()
    if not document_ids or not terms:
        return {}
    contexts: dict[int, dict[str, str]] = {}
    for row in list_indexed_contents(conn, document_ids):
        title = str(row["title"] or "")
        content = str(row["content"] or "")
        lowered_title = title.lower()
        if any(term.lower() in lowered_title for term in terms):
            contexts[int(row["document_id"])] = {"hit": "title", "snippet": ""}
            continue
        snippet = _extract_snippet(content, terms)
        contexts[int(row["document_id"])] = {"hit": "content", "snippet": snippet}
    return contexts
