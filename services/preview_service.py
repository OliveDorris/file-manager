from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


MAX_TEXT_PREVIEW_BYTES = 200_000

IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
TEXT_EXTENSIONS = {".csv", ".log", ".md", ".txt"}


def detect_preview_kind(filename: str, content_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    normalized_content_type = (content_type or "").split(";", 1)[0].lower()

    if normalized_content_type == "application/pdf" or suffix == ".pdf":
        return "pdf"
    if normalized_content_type.startswith("image/") or suffix in IMAGE_EXTENSIONS:
        return "image"
    if normalized_content_type.startswith("text/") or suffix in TEXT_EXTENSIONS:
        return "text"
    return "unsupported"


def read_text_preview(file_path: Path, max_bytes: int = MAX_TEXT_PREVIEW_BYTES) -> dict[str, Any]:
    with file_path.open("rb") as source:
        data = source.read(max_bytes + 1)

    truncated = len(data) > max_bytes
    data = data[:max_bytes]

    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")

    return {"text": text, "truncated": truncated}


def build_preview_context(
    document_id: int,
    version: Mapping[str, Any],
    upload_dir: Path,
) -> dict[str, Any]:
    original_filename = str(version["original_filename"])
    content_type = str(version["content_type"] or "application/octet-stream")
    file_path = upload_dir / str(document_id) / str(version["stored_filename"])

    if not file_path.exists():
        raise FileNotFoundError(file_path)

    kind = detect_preview_kind(original_filename, content_type)
    preview: dict[str, Any] = {
        "kind": kind,
        "inline_url": f"/documents/{document_id}/preview/file",
        "message": "",
        "text": "",
        "truncated": False,
    }

    if kind == "text":
        preview.update(read_text_preview(file_path))
    elif kind == "unsupported":
        preview["message"] = "当前文件类型暂不支持在线预览，请下载后查看。"

    return preview
