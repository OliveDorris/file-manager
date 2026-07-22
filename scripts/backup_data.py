from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.backup_status_service import STATUS_KIND_UPLOADS, now_iso, record_backup_status


logger = logging.getLogger("file_manager.backup_uploads")

SNAPSHOT_PREFIX = "uploads-"


def _next_snapshot_dir(output_dir: Path, timestamp: str) -> Path:
    candidate = output_dir / f"{SNAPSHOT_PREFIX}{timestamp}"
    suffix = 1
    while candidate.exists():
        candidate = output_dir / f"{SNAPSHOT_PREFIX}{timestamp}-{suffix}"
        suffix += 1
    return candidate


def list_snapshots(output_dir: Path) -> list[Path]:
    if not output_dir.is_dir():
        return []
    return sorted(
        (path for path in output_dir.iterdir() if path.is_dir() and path.name.startswith(SNAPSHOT_PREFIX)),
        key=lambda path: path.name,
    )


def _remove_expired_snapshots(output_dir: Path, keep: int) -> None:
    snapshots = list_snapshots(output_dir)
    for expired_snapshot in snapshots[:-keep] if keep < len(snapshots) else []:
        shutil.rmtree(expired_snapshot)


def _same_file(first: Path, second: Path) -> bool:
    first_stat = first.stat()
    second_stat = second.stat()
    return first_stat.st_size == second_stat.st_size and first_stat.st_mtime_ns == second_stat.st_mtime_ns


def backup_uploads(
    uploads_dir: Path,
    output_dir: Path,
    keep: int = 7,
    created_at: datetime | None = None,
) -> Path:
    """按日期目录增量备份上传文件：未变化文件与上一份快照硬链接，节省空间。"""
    source_dir = uploads_dir.expanduser().resolve()
    destination_dir = output_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"上传文件目录不存在：{source_dir}")
    if keep < 1:
        raise ValueError("备份保留数量必须大于 0")

    destination_dir.mkdir(parents=True, exist_ok=True)
    previous_snapshots = list_snapshots(destination_dir)
    previous_snapshot = previous_snapshots[-1] if previous_snapshots else None

    timestamp = (created_at or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    snapshot_dir = _next_snapshot_dir(destination_dir, timestamp)
    snapshot_dir.mkdir()

    try:
        for current_dir, _dirnames, filenames in os.walk(source_dir):
            for filename in filenames:
                source_file = Path(current_dir) / filename
                relative_path = source_file.relative_to(source_dir)
                destination_file = snapshot_dir / relative_path
                destination_file.parent.mkdir(parents=True, exist_ok=True)
                previous_file = previous_snapshot / relative_path if previous_snapshot else None
                if previous_file and previous_file.is_file() and _same_file(source_file, previous_file):
                    os.link(previous_file, destination_file)
                else:
                    shutil.copy2(source_file, destination_file)
        _remove_expired_snapshots(destination_dir, keep)
    except Exception:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        logger.exception("上传文件备份失败：source=%s output_dir=%s", source_dir, destination_dir)
        raise

    logger.info("上传文件备份完成：source=%s backup=%s", source_dir, snapshot_dir)
    return snapshot_dir


def resolve_snapshot(output_dir: Path, snapshot: str) -> Path:
    candidate = Path(snapshot)
    if not candidate.is_absolute():
        candidate = output_dir / snapshot
    candidate = candidate.expanduser().resolve()
    if not candidate.is_dir():
        raise FileNotFoundError(f"备份快照不存在：{candidate}")
    return candidate


def restore_uploads(snapshot: str, output_dir: Path, target_dir: Path) -> Path:
    """把指定快照完整还原到目标目录（覆盖目标目录现有内容）。"""
    snapshot_dir = resolve_snapshot(output_dir, snapshot)
    destination_dir = target_dir.expanduser().resolve()
    if destination_dir.exists():
        shutil.rmtree(destination_dir)
    shutil.copytree(snapshot_dir, destination_dir)
    logger.info("上传文件恢复完成：snapshot=%s target=%s", snapshot_dir, destination_dir)
    return destination_dir


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("必须是大于 0 的整数")
    return parsed


def default_uploads_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "data")) / "uploads"


def default_status_path(uploads_dir: Path) -> Path:
    configured = os.getenv("BACKUP_STATUS_FILE")
    if configured:
        return Path(configured)
    return uploads_dir.parent / "backup_status.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="增量备份文件管理系统的上传文件目录")
    parser.add_argument(
        "--uploads-dir",
        type=Path,
        default=default_uploads_dir(),
        help="上传文件目录，默认读取 DATA_DIR 后拼接 uploads",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.getenv("UPLOADS_BACKUP_DIR", "backups")),
        help="备份输出目录，默认读取 UPLOADS_BACKUP_DIR",
    )
    parser.add_argument(
        "--keep",
        type=positive_int,
        default=positive_int(os.getenv("UPLOADS_BACKUP_KEEP", "7")),
        help="保留最近多少份备份，默认 7",
    )
    parser.add_argument(
        "--restore",
        metavar="SNAPSHOT",
        help="恢复指定快照（快照目录名或路径），不执行备份",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=None,
        help="恢复目标目录，默认为 --uploads-dir",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args()

    if args.restore:
        target_dir = args.target_dir or args.uploads_dir
        try:
            restored = restore_uploads(args.restore, args.output_dir, target_dir)
        except (FileNotFoundError, OSError) as exc:
            logger.error("%s", exc)
            return 1
        print(restored)
        return 0

    status_path = default_status_path(args.uploads_dir)
    started_at = now_iso()
    try:
        snapshot_dir = backup_uploads(args.uploads_dir, args.output_dir, args.keep)
    except (FileNotFoundError, OSError, ValueError) as exc:
        try:
            record_backup_status(
                status_path,
                STATUS_KIND_UPLOADS,
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
            STATUS_KIND_UPLOADS,
            True,
            artifact=str(snapshot_dir),
            started_at=started_at,
            finished_at=now_iso(),
        )
    except OSError as status_exc:
        logger.warning("备份状态记录失败：%s", status_exc)
    print(snapshot_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
