"""Command line entrypoint for postcall audio retention cleanup."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from echox_call.features.audio_analysis.postcall.audio_retention import (
    cleanup_expired_audio_files,
)


LOGGER = logging.getLogger(__name__)


def _positive_env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name, "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw_value!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0, got {value}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Delete expired downloaded audio files")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Run one cleanup and exit")
    mode.add_argument("--loop", action="store_true", help="Run cleanup periodically")
    parser.add_argument(
        "--storage-dir",
        type=Path,
        default=Path(os.environ.get("POSTCALL_STORAGE_DIR", "/app/data/postcall")),
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=_positive_env_int("POSTCALL_AUDIO_RETENTION_DAYS", 15),
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=_positive_env_int("POSTCALL_AUDIO_CLEANUP_INTERVAL_SECONDS", 21600),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report expired files without deleting them",
    )
    return parser


def _run_cleanup(args: argparse.Namespace) -> None:
    result = cleanup_expired_audio_files(
        storage_dir=args.storage_dir,
        retention_days=args.retention_days,
        dry_run=args.dry_run,
    )
    LOGGER.info(
        "postcall audio cleanup completed storageDir=%s retentionDays=%d dryRun=%s "
        "scanned=%d expired=%d deleted=%d reclaimableBytes=%d deletedBytes=%d "
        "removedDirectories=%d failed=%d",
        args.storage_dir,
        args.retention_days,
        args.dry_run,
        result.scanned_files,
        result.expired_files,
        result.deleted_files,
        result.reclaimable_bytes,
        result.deleted_bytes,
        result.removed_directories,
        result.failed_files,
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        args = _build_parser().parse_args(argv)
        if args.retention_days <= 0:
            raise ValueError("--retention-days must be greater than 0")
        if args.interval_seconds <= 0:
            raise ValueError("--interval-seconds must be greater than 0")

        while True:
            try:
                _run_cleanup(args)
            except Exception:
                LOGGER.exception("postcall audio cleanup failed")
                if args.once:
                    return 1

            if args.once:
                return 0
            time.sleep(args.interval_seconds)
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        LOGGER.info("postcall audio cleanup stopped")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
