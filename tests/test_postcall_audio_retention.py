from __future__ import annotations

import os
from pathlib import Path

from echox_call.features.audio_analysis.postcall.audio_retention import (
    cleanup_expired_audio_files,
)


def _write_file(path: Path, *, size: int, modified_at: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (modified_at, modified_at))


def test_cleanup_deletes_only_files_older_than_retention(tmp_path: Path) -> None:
    now = 2_000_000_000.0
    old_file = tmp_path / "2026" / "07" / "source.wav"
    recent_file = tmp_path / "2026" / "08" / "normalized.wav"
    _write_file(old_file, size=10, modified_at=now - 16 * 86400)
    _write_file(recent_file, size=20, modified_at=now - 14 * 86400)

    result = cleanup_expired_audio_files(
        storage_dir=tmp_path,
        retention_days=15,
        now=now,
    )

    assert not old_file.exists()
    assert recent_file.exists()
    assert result.scanned_files == 2
    assert result.expired_files == 1
    assert result.deleted_files == 1
    assert result.deleted_bytes == 10
    assert result.failed_files == 0


def test_cleanup_dry_run_reports_without_deleting(tmp_path: Path) -> None:
    now = 2_000_000_000.0
    old_file = tmp_path / "old" / "source.mp3"
    _write_file(old_file, size=12, modified_at=now - 16 * 86400)

    result = cleanup_expired_audio_files(
        storage_dir=tmp_path,
        retention_days=15,
        dry_run=True,
        now=now,
    )

    assert old_file.exists()
    assert result.expired_files == 1
    assert result.reclaimable_bytes == 12
    assert result.deleted_files == 0
    assert result.deleted_bytes == 0
