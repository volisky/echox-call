"""API client configuration command-line checks."""

from __future__ import annotations

import argparse
import json
import sys

from echox_call.core.auth import ClientConfigError, load_api_clients


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="API client config utilities")
    parser.add_argument(
        "--config",
        help="Path to clients.yaml. Defaults to CLIENTS_CONFIG_PATH or config/clients.yaml.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="Validate config and list clients without API keys")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "list":
        try:
            clients = load_api_clients(args.config)
        except ClientConfigError as exc:
            print(f"client configuration error: {exc}", file=sys.stderr)
            return 2

        print(
            json.dumps(
                [
                    {
                        "client_id": client.client_id,
                        "name": client.name,
                        "source_system": client.source_system,
                        "enabled": client.enabled,
                        "allow_debug": client.allow_debug,
                    }
                    for client in clients
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
