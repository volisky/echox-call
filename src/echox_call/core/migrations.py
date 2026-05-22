"""PostgreSQL schema migration utilities."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from echox_call.core.settings import DatabaseSettings, load_database_settings


DEFAULT_MIGRATIONS_DIR = "migrations"


class MigrationError(RuntimeError):
    """Raised when a database migration cannot be applied safely."""


@dataclass(frozen=True)
class MigrationFile:
    version: str
    name: str
    path: Path
    checksum: str


@dataclass(frozen=True)
class MigrationStatus:
    version: str
    name: str
    checksum: str
    applied: bool


def _migration_table_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version text PRIMARY KEY,
        name text NOT NULL,
        checksum text NOT NULL,
        applied_at timestamptz NOT NULL DEFAULT now()
    )
    """


def _read_migration(path: Path) -> tuple[str, str]:
    sql = path.read_text(encoding="utf-8")
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    return sql, checksum


def discover_migrations(migrations_dir: str | Path = DEFAULT_MIGRATIONS_DIR) -> list[MigrationFile]:
    directory = Path(migrations_dir)
    if not directory.exists():
        raise MigrationError(f"migrations directory not found: {directory}")
    if not directory.is_dir():
        raise MigrationError(f"migrations path is not a directory: {directory}")

    migrations: list[MigrationFile] = []
    for path in sorted(directory.glob("*.sql")):
        version = path.name.split("_", 1)[0]
        if not version:
            raise MigrationError(f"migration file has no version prefix: {path.name}")
        _, checksum = _read_migration(path)
        migrations.append(
            MigrationFile(
                version=version,
                name=path.name,
                path=path,
                checksum=checksum,
            )
        )

    if not migrations:
        raise MigrationError(f"no SQL migration files found in: {directory}")

    versions = [migration.version for migration in migrations]
    duplicate_versions = sorted({version for version in versions if versions.count(version) > 1})
    if duplicate_versions:
        raise MigrationError(f"duplicate migration versions: {', '.join(duplicate_versions)}")

    return migrations


def _load_applied_migrations(conn: psycopg.Connection) -> dict[str, dict[str, str]]:
    conn.execute(_migration_table_sql())
    rows = conn.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {
        row["version"]: {
            "name": row["name"],
            "checksum": row["checksum"],
        }
        for row in rows
    }


def get_migration_status(
    migrations_dir: str | Path = DEFAULT_MIGRATIONS_DIR,
    settings: DatabaseSettings | None = None,
) -> list[MigrationStatus]:
    db_settings = settings or load_database_settings()
    migrations = discover_migrations(migrations_dir)

    with psycopg.connect(
        db_settings.conninfo,
        autocommit=True,
        connect_timeout=db_settings.connect_timeout,
        row_factory=dict_row,
    ) as conn:
        applied = _load_applied_migrations(conn)

    return [
        MigrationStatus(
            version=migration.version,
            name=migration.name,
            checksum=migration.checksum,
            applied=migration.version in applied,
        )
        for migration in migrations
    ]


def apply_pending_migrations(
    migrations_dir: str | Path = DEFAULT_MIGRATIONS_DIR,
    settings: DatabaseSettings | None = None,
) -> list[MigrationFile]:
    db_settings = settings or load_database_settings()
    migrations = discover_migrations(migrations_dir)
    applied_now: list[MigrationFile] = []

    with psycopg.connect(
        db_settings.conninfo,
        autocommit=False,
        connect_timeout=db_settings.connect_timeout,
        row_factory=dict_row,
    ) as conn:
        with conn.transaction():
            applied = _load_applied_migrations(conn)

        for migration in migrations:
            if migration.version in applied:
                applied_migration = applied[migration.version]
                if applied_migration["checksum"] != migration.checksum:
                    raise MigrationError(
                        "applied migration checksum mismatch for "
                        f"{migration.name}; create a new migration instead of editing it"
                    )
                continue

            sql, checksum = _read_migration(migration.path)
            if checksum != migration.checksum:
                raise MigrationError(f"migration changed while being applied: {migration.name}")

            try:
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        """
                        INSERT INTO schema_migrations (version, name, checksum)
                        VALUES (%s, %s, %s)
                        """,
                        (migration.version, migration.name, migration.checksum),
                    )
            except psycopg.Error as exc:
                raise MigrationError(f"failed to apply migration {migration.name}: {exc}") from exc

            applied_now.append(migration)

    return applied_now

