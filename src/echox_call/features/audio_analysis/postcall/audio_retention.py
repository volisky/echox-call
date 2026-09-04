"""Retention cleanup for downloaded postcall audio files."""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioCleanupResult:
    scanned_files: int
    expired_files: int
    deleted_files: int
    reclaimable_bytes: int
    deleted_bytes: int
    removed_directories: int
    failed_files: int


def cleanup_expired_audio_files(
    *,
    storage_dir: Path,
    retention_days: int,
    dry_run: bool = False,
    now: float | None = None,
) -> AudioCleanupResult:
    """Delete regular audio-storage files older than the retention period."""

    if retention_days <= 0:
        raise ValueError("retention_days must be greater than 0")

    if not storage_dir.exists():
        return AudioCleanupResult(0, 0, 0, 0, 0, 0, 0)
    if not storage_dir.is_dir():
        raise ValueError(f"storage_dir is not a directory: {storage_dir}")

    cutoff = (time.time() if now is None else now) - retention_days * 86400
    scanned_files = 0
    expired_files = 0
    deleted_files = 0
    reclaimable_bytes = 0
    deleted_bytes = 0
    removed_directories = 0
    failed_files = 0

    for root, _, filenames in os.walk(storage_dir, topdown=False, followlinks=False):
        root_path = Path(root)
        for filename in filenames:
            path = root_path / filename
            try:
                file_stat = path.stat(follow_symlinks=False)
                if not stat.S_ISREG(file_stat.st_mode):
                    continue
                scanned_files += 1
                if file_stat.st_mtime >= cutoff:
                    continue

                expired_files += 1
                reclaimable_bytes += file_stat.st_size
                if dry_run:
                    continue

                path.unlink()
                deleted_files += 1
                deleted_bytes += file_stat.st_size
            except FileNotFoundError:
                continue
            except OSError:
                failed_files += 1

        if dry_run or root_path == storage_dir:
            continue
        try:
            root_path.rmdir()
            removed_directories += 1
        except OSError:
            pass

    return AudioCleanupResult(
        scanned_files=scanned_files,
        expired_files=expired_files,
        deleted_files=deleted_files,
        reclaimable_bytes=reclaimable_bytes,
        deleted_bytes=deleted_bytes,
        removed_directories=removed_directories,
        failed_files=failed_files,
    )
