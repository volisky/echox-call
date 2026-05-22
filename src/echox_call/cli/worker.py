"""Postcall worker command line entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
import time

from echox_call.core.db import DatabaseConnectionError
from echox_call.core.settings import DatabaseConfigError, PostcallWorkerConfigError
from echox_call.features.audio_analysis.postcall.worker import PostcallWorker


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run postcall audio analysis worker")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Process one batch and exit")
    mode.add_argument("--loop", action="store_true", help="Continuously poll queued jobs")
    parser.add_argument("--worker-id", help="Stable worker id for locks")
    parser.add_argument("--batch-size", type=int, help="Override POSTCALL_WORKER_BATCH_SIZE")
    parser.add_argument("--sleep-seconds", type=float, default=5.0, help="Loop idle sleep seconds")
    parser.add_argument(
        "--idle-log-seconds",
        type=float,
        default=30.0,
        help="Print an idle heartbeat at this interval while no queued jobs exist",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        worker = PostcallWorker(worker_id=args.worker_id)
        if args.once:
            processed = worker.run_once(batch_size=args.batch_size)
            print(json.dumps({"processed": processed}, ensure_ascii=False))
            return 0

        batch_size = worker.settings.batch_size if args.batch_size is None else args.batch_size
        print(
            "worker started: "
            f"workerId={worker.worker_id} "
            f"batchSize={batch_size} "
            f"sleepSeconds={args.sleep_seconds}",
            flush=True,
        )
        print("worker polling: waiting for processing_queued jobs", flush=True)

        last_idle_log = 0.0
        while True:
            processed = worker.run_once(batch_size=args.batch_size)
            if processed > 0:
                print(json.dumps({"processed": processed}, ensure_ascii=False), flush=True)
                last_idle_log = 0.0
                continue

            now = time.monotonic()
            if last_idle_log == 0.0 or now - last_idle_log >= args.idle_log_seconds:
                print("worker idle: no queued jobs", flush=True)
                last_idle_log = now
            time.sleep(args.sleep_seconds)
        return 0
    except (DatabaseConfigError, PostcallWorkerConfigError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except DatabaseConnectionError as exc:
        print(f"database error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("worker stopped", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
