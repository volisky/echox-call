"""Emotion annotation gateway command-line entrypoint."""

from __future__ import annotations

import argparse

import uvicorn


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the emotion annotation reverse gateway")
    parser.add_argument("--host", default="127.0.0.1", help="Gateway bind host")
    parser.add_argument("--port", default=8012, type=int, help="Gateway bind port")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload for local development",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    uvicorn.run(
        "echox_call.annotation_gateway.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
