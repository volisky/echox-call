"""Management console command-line entrypoint."""

from __future__ import annotations

import argparse

import uvicorn


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the EchoX Call management console")
    parser.add_argument("--host", default="127.0.0.1", help="Console bind host")
    parser.add_argument("--port", default=8001, type=int, help="Console bind port")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload for local development",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    uvicorn.run(
        "echox_call.console.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
