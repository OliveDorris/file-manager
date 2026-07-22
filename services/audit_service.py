from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


AUDIT_LOGGER_NAME = "file_manager.audit"
DEFAULT_AUDIT_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_AUDIT_LOG_BACKUP_COUNT = 5


def audit_log_file_path(data_dir: Path) -> Path:
    return data_dir / "logs" / "audit.log"


def configure_audit_file_handler(data_dir: Path) -> None:
    """为审计日志配置文件轮转，重复调用时只在路径变化时重建 handler。"""
    log_path = audit_log_file_path(data_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_log_path = log_path.resolve()
    try:
        max_bytes = int(os.getenv("AUDIT_LOG_MAX_BYTES", str(DEFAULT_AUDIT_LOG_MAX_BYTES)))
    except ValueError:
        max_bytes = DEFAULT_AUDIT_LOG_MAX_BYTES
    try:
        backup_count = int(os.getenv("AUDIT_LOG_BACKUP_COUNT", str(DEFAULT_AUDIT_LOG_BACKUP_COUNT)))
    except ValueError:
        backup_count = DEFAULT_AUDIT_LOG_BACKUP_COUNT

    audit_log = logging.getLogger(AUDIT_LOGGER_NAME)
    for handler in audit_log.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == resolved_log_path:
            return

    for handler in list(audit_log.handlers):
        if isinstance(handler, RotatingFileHandler):
            audit_log.removeHandler(handler)
            handler.close()

    file_handler = RotatingFileHandler(
        resolved_log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    audit_log.addHandler(file_handler)
    audit_log.setLevel(logging.INFO)
