from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.backup_status_service import STATUS_KIND_DATABASE, now_iso, record_backup_status


logger = logging.getLogger("file_manager.backup")


def _next_backup_path(output_dir: Path, database_stem: str, timestamp: str) -> Path:
    candidate = output_dir / f"{database_stem}-{timestamp}.sqlite3"
    suffix = 1
    while candidate.exists():
        candidate = output_dir / f"{database_stem}-{timestamp}-{suffix}.sqlite3"
        suffix += 1
    return candidate


def _remove_expired_backups(output_dir: Path, database_stem: str, keep: int) -> None:
    backups = sorted(
        output_dir.glob(f"{database_stem}-*.sqlite3"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for expired_backup in backups[keep:]:
        expired_backup.unlink()


def backup_database(
    database_path: Path,
    output_dir: Path,
    keep: int = 7,
    created_at: datetime | None = None,
) -> Path:
    source = database_path.expanduser().resolve()
    destination_dir = output_dir.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"数据库文件不存在：{source}")
    if keep < 1:
        raise ValueError("备份保留数量必须大于 0")

    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (created_at or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    destination = _next_backup_path(destination_dir, source.stem, timestamp)
    temporary_destination = destination.with_suffix(f"{destination.suffix}.tmp")

    try:
        with closing(sqlite3.connect(str(source))) as source_conn:
            with closing(sqlite3.connect(str(temporary_destination))) as destination_conn:
                source_conn.backup(destination_conn)
                integrity_result = destination_conn.execute("PRAGMA integrity_check").fetchone()
                if not integrity_result or integrity_result[0] != "ok":
                    raise RuntimeError("数据库备份完整性检查失败")
        temporary_destination.replace(destination)
        _remove_expired_backups(destination_dir, source.stem, keep)
    except Exception:
        temporary_destination.unlink(missing_ok=True)
        logger.exception("数据库备份失败：source=%s output_dir=%s", source, destination_dir)
        raise

    logger.info("数据库备份完成：source=%s backup=%s", source, destination)
    return destination


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("必须是大于 0 的整数")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="备份文件管理系统的 SQLite 数据库")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.getenv("DATABASE_PATH", "data/file_manager.sqlite3")),
        help="SQLite 数据库路径，默认读取 DATABASE_PATH",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.getenv("DATABASE_BACKUP_DIR", "backups")),
        help="备份输出目录，默认读取 DATABASE_BACKUP_DIR",
    )
    parser.add_argument(
        "--keep",
        type=positive_int,
        default=os.getenv("DATABASE_BACKUP_KEEP", "7"),
        help="保留最近多少份备份，默认 7",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args()
    status_path = Path(os.getenv("BACKUP_STATUS_FILE", str(args.database.parent / "backup_status.json")))
    started_at = now_iso()
    try:
        backup_path = backup_database(args.database, args.output_dir, args.keep)
    except (FileNotFoundError, OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        try:
            record_backup_status(
                status_path,
                STATUS_KIND_DATABASE,
                False,
                error=str(exc),
                started_at=started_at,
                finished_at=now_iso(),
            )
        except OSError as status_exc:
            logger.warning("备份状态记录失败：%s", status_exc)
        logger.error("%s", exc)
        return 1
    try:
        record_backup_status(
            status_path,
            STATUS_KIND_DATABASE,
            True,
            artifact=str(backup_path),
            started_at=started_at,
            finished_at=now_iso(),
        )
    except OSError as status_exc:
        logger.warning("备份状态记录失败：%s", status_exc)
    print(backup_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
