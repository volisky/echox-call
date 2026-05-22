"""Backfill postcall_audio_assets from real files already downloaded by the worker."""

from __future__ import annotations

import hashlib
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import soundfile as sf
from psycopg.types.json import Jsonb

from echox_call.core.db import connect


@dataclass(frozen=True)
class LocalAsset:
    asset_type: str
    uri: str
    path: Path
    content_type: str
    sha256: str
    sample_rate: int | None
    channels: int | None
    duration_sec: float | None
    size_bytes: int
    metadata: dict[str, Any]


def main() -> int:
    root_dir = _storage_root()
    storage_timezone = _storage_timezone()
    inserted = 0
    skipped = 0
    missing = 0

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                job_id,
                audio_url,
                bjsj
            FROM postcall_jobs
            ORDER BY created_at ASC
            """
        ).fetchall()

        for row in rows:
            job = dict(row)
            assets = _local_assets_for_job(
                job,
                root_dir=root_dir,
                storage_timezone=storage_timezone,
            )
            if not assets:
                missing += 1
                print(f"missing local audio: {job['job_id']}")
                continue

            for asset in assets:
                exists = conn.execute(
                    """
                    SELECT 1
                    FROM postcall_audio_assets
                    WHERE postcall_job_id = %s
                      AND asset_type = %s
                      AND uri = %s
                    LIMIT 1
                    """,
                    (job["id"], asset.asset_type, asset.uri),
                ).fetchone()
                if exists:
                    skipped += 1
                    continue

                conn.execute(
                    """
                    INSERT INTO postcall_audio_assets (
                        postcall_job_id,
                        asset_type,
                        uri,
                        content_type,
                        sha256,
                        sample_rate,
                        channels,
                        duration_sec,
                        size_bytes,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        job["id"],
                        asset.asset_type,
                        asset.uri,
                        asset.content_type,
                        asset.sha256,
                        asset.sample_rate,
                        asset.channels,
                        asset.duration_sec,
                        asset.size_bytes,
                        Jsonb(asset.metadata),
                    ),
                )
                inserted += 1
                print(f"inserted {asset.asset_type}: {job['job_id']} -> {asset.uri}")

    print(f"done inserted={inserted} skipped={skipped} missing_jobs={missing}")
    return 0


def _local_assets_for_job(
    job: dict[str, Any],
    *,
    root_dir: Path,
    storage_timezone: ZoneInfo,
) -> list[LocalAsset]:
    job_dir = _job_storage_dir(root_dir=root_dir, job=job, storage_timezone=storage_timezone)
    source_path = _first_existing([job_dir / "source.wav", *sorted(job_dir.glob("source.*"))])
    normalized_path = _first_existing([job_dir / "normalized.wav"])

    if source_path is None and normalized_path is None:
        return []

    partition = job_dir.relative_to(root_dir).parent.as_posix()
    source_sha256 = _sha256_file(source_path) if source_path else None
    assets: list[LocalAsset] = []
    if source_path:
        assets.append(
            _build_asset(
                asset_type="source",
                path=source_path,
                content_type=mimetypes.guess_type(source_path.name)[0] or "audio/wav",
                metadata={
                    "downloadUrl": job.get("audio_url"),
                    "storagePartition": partition,
                    "storagePartitionBasis": "bjsj",
                    "storageTimezone": storage_timezone.key,
                },
            )
        )
    if normalized_path:
        metadata: dict[str, Any] = {
            "storagePartition": partition,
            "storagePartitionBasis": "bjsj",
            "storageTimezone": storage_timezone.key,
        }
        if source_sha256:
            metadata["sourceSha256"] = source_sha256
        assets.append(
            _build_asset(
                asset_type="normalized",
                path=normalized_path,
                content_type="audio/wav",
                metadata=metadata,
            )
        )
    return assets


def _build_asset(
    *,
    asset_type: str,
    path: Path,
    content_type: str,
    metadata: dict[str, Any],
) -> LocalAsset:
    info = _audio_info(path)
    return LocalAsset(
        asset_type=asset_type,
        uri=path.as_posix(),
        path=path,
        content_type=content_type,
        sha256=_sha256_file(path),
        sample_rate=info["sample_rate"],
        channels=info["channels"],
        duration_sec=info["duration_sec"],
        size_bytes=path.stat().st_size,
        metadata=metadata,
    )


def _job_storage_dir(
    *,
    root_dir: Path,
    job: dict[str, Any],
    storage_timezone: ZoneInfo,
) -> Path:
    bjsj = job["bjsj"]
    if bjsj.tzinfo is None:
        bjsj = bjsj.replace(tzinfo=timezone.utc)
    partition_time = bjsj.astimezone(storage_timezone)
    return (
        root_dir
        / f"{partition_time.year:04d}"
        / f"{partition_time.month:02d}"
        / f"{partition_time.day:02d}"
        / f"{partition_time.hour:02d}"
        / job["job_id"]
    )


def _storage_root() -> Path:
    configured = Path(os.environ.get("POSTCALL_STORAGE_DIR") or "data/postcall").expanduser()
    return configured if configured.is_absolute() else configured


def _storage_timezone() -> ZoneInfo:
    timezone_name = os.environ.get("POSTCALL_STORAGE_TIMEZONE") or "Asia/Shanghai"
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def _audio_info(path: Path) -> dict[str, int | float | None]:
    try:
        info = sf.info(str(path))
    except Exception:
        return {"sample_rate": None, "channels": None, "duration_sec": None}
    return {
        "sample_rate": int(info.samplerate) if info.samplerate else None,
        "channels": int(info.channels) if info.channels else None,
        "duration_sec": float(info.duration) if info.duration is not None else None,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
