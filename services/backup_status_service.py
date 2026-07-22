from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger("file_manager.backup_status")

STATUS_KIND_DATABASE = "database"
STATUS_KIND_UPLOADS = "uploads"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_backup_status(status_path: Path) -> dict[str, Any]:
    if not status_path.is_file():
        return {}
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("备份状态文件读取失败：path=%s error=%s", status_path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def record_backup_status(
    status_path: Path,
    kind: str,
    success: bool,
    artifact: str = "",
    error: str = "",
    started_at: str = "",
    finished_at: str = "",
) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    data = load_backup_status(status_path)
    data[kind] = {
        "success": bool(success),
        "artifact": artifact,
        "error": error,
        "started_at": started_at or now_iso(),
        "finished_at": finished_at or now_iso(),
    }
    temporary_path = status_path.with_suffix(status_path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(status_path)


def format_bytes(size: int | None) -> str:
    if size is None:
        return "未知"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def build_backup_overview(status_path: Path, data_dir: Path) -> dict[str, Any]:
    status = load_backup_status(status_path)
    try:
        usage = shutil.disk_usage(data_dir)
        disk_free = usage.free
        disk_total = usage.total
    except OSError as exc:
        logger.warning("磁盘空间读取失败：path=%s error=%s", data_dir, exc)
        disk_free = None
        disk_total = None
    return {
        "database": status.get(STATUS_KIND_DATABASE),
        "uploads": status.get(STATUS_KIND_UPLOADS),
        "disk_free": disk_free,
        "disk_total": disk_total,
        "disk_free_display": format_bytes(disk_free),
        "disk_total_display": format_bytes(disk_total),
    }
