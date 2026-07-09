from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path


def parse_selected_document_ids(values: Iterable[object]) -> list[int]:
    document_ids: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            document_id = int(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("选择的文件无效") from exc
        if document_id <= 0:
            raise ValueError("选择的文件无效")
        if document_id not in seen:
            document_ids.append(document_id)
            seen.add(document_id)
    if not document_ids:
        raise ValueError("请选择文件")
    return document_ids


def remove_document_files(upload_dir: Path, document_id: int) -> None:
    document_dir = upload_dir / str(document_id)
    if document_dir.exists():
        shutil.rmtree(document_dir)


def remove_many_document_files(upload_dir: Path, document_ids: Iterable[int]) -> None:
    for document_id in document_ids:
        remove_document_files(upload_dir, document_id)


def build_batch_download_zip(
    documents: list[sqlite3.Row],
    upload_dir: Path,
    temp_dir: Path,
) -> tuple[Path, str]:
    if not documents:
        raise ValueError("请选择文件")

    temp_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="selected-documents-", suffix=".zip", dir=temp_dir)
    os.close(fd)
    zip_path = Path(temp_name)
    used_names: dict[str, int] = {}

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for document in documents:
                source = upload_dir / str(document["id"]) / document["stored_filename"]
                if not source.exists():
                    raise FileNotFoundError(source)
                archive.write(source, _unique_archive_name(document, used_names))
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise

    download_name = f"selected-documents-{datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
    return zip_path, download_name


def _unique_archive_name(document: sqlite3.Row, used_names: dict[str, int]) -> str:
    original_name = str(document["original_filename"] or "").strip() or f"document-{document['id']}.bin"
    path = Path(original_name)
    stem = path.stem or f"document-{document['id']}"
    suffix = path.suffix
    count = used_names.get(original_name, 0) + 1
    used_names[original_name] = count
    if count == 1:
        return original_name
    return f"{stem} ({count}){suffix}"
