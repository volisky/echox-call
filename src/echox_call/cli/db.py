"""Database command-line checks."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys

from echox_call.core.db import DatabaseConnectionError, ping_database
from echox_call.core.migrations import (
    DEFAULT_MIGRATIONS_DIR,
    MigrationError,
    apply_pending_migrations,
    get_migration_status,
)
from echox_call.core.settings import DatabaseConfigError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PostgreSQL utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ping", help="Check PostgreSQL connectivity")

    migrate_parser = subparsers.add_parser("migrate", help="Apply pending SQL migrations")
    migrate_parser.add_argument(
        "--migrations-dir",
        default=DEFAULT_MIGRATIONS_DIR,
        help="Directory containing SQL migration files",
    )

    status_parser = subparsers.add_parser("migration-status", help="Show migration status")
    status_parser.add_argument(
        "--migrations-dir",
        default=DEFAULT_MIGRATIONS_DIR,
        help="Directory containing SQL migration files",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "ping":
        try:
            result = ping_database()
        except DatabaseConfigError as exc:
            print(f"database configuration error: {exc}", file=sys.stderr)
            return 2
        except DatabaseConnectionError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "migrate":
        try:
            applied = apply_pending_migrations(args.migrations_dir)
        except DatabaseConfigError as exc:
            print(f"database configuration error: {exc}", file=sys.stderr)
            return 2
        except MigrationError as exc:
            print(f"migration error: {exc}", file=sys.stderr)
            return 1

        result = {
            "applied_count": len(applied),
            "applied": [asdict(migration) | {"path": str(migration.path)} for migration in applied],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "migration-status":
        try:
            status = get_migration_status(args.migrations_dir)
        except DatabaseConfigError as exc:
            print(f"database configuration error: {exc}", file=sys.stderr)
            return 2
        except MigrationError as exc:
            print(f"migration error: {exc}", file=sys.stderr)
            return 1

        print(json.dumps([asdict(item) for item in status], ensure_ascii=False, indent=2))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
