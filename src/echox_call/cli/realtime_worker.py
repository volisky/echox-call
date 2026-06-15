"""Realtime audio stream worker command line entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
import time

from echox_call.core.db import DatabaseConnectionError
from echox_call.core.settings import (
    DatabaseConfigError,
    PostcallWorkerConfigError,
    RealtimeWorkerConfigError,
)
from echox_call.features.audio_analysis.postcall.realtime import RealtimeAudioWorker


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run realtime audio stream worker")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Process one realtime batch and exit")
    mode.add_argument("--loop", action="store_true", help="Continuously poll realtime windows")
    parser.add_argument("--batch-size", type=int, help="Override realtime worker batch size")
    parser.add_argument("--sleep-seconds", type=float, help="Loop idle sleep seconds")
    parser.add_argument(
        "--idle-log-seconds",
        type=float,
        default=30.0,
        help="Print an idle heartbeat at this interval while no window is ready",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        worker = RealtimeAudioWorker()
        if args.once:
            processed = worker.run_once(batch_size=args.batch_size)
            print(json.dumps({"processed": processed}, ensure_ascii=False))
            return 0

        sleep_seconds = (
            args.sleep_seconds
            if args.sleep_seconds is not None
            else worker.realtime_settings.sleep_seconds
        )
        print(
            "realtime-worker started: "
            f"windowSec={worker.realtime_settings.window_sec} "
            f"tailMinSec={worker.realtime_settings.tail_min_sec} "
            f"sleepSeconds={sleep_seconds}",
            flush=True,
        )
        last_idle_log = 0.0
        while True:
            processed = worker.run_once(batch_size=args.batch_size)
            if processed > 0:
                print(json.dumps({"processed": processed}, ensure_ascii=False), flush=True)
                last_idle_log = 0.0
                continue
            now = time.monotonic()
            if last_idle_log == 0.0 or now - last_idle_log >= args.idle_log_seconds:
                print("realtime-worker idle: no ready windows", flush=True)
                last_idle_log = now
            time.sleep(sleep_seconds)
        return 0
    except (DatabaseConfigError, PostcallWorkerConfigError, RealtimeWorkerConfigError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except DatabaseConnectionError as exc:
        print(f"database error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("realtime-worker stopped", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
