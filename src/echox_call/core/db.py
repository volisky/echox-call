"""PostgreSQL connection utilities."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from echox_call.core.settings import DatabaseSettings, load_database_settings


class DatabaseConnectionError(RuntimeError):
    """Raised when PostgreSQL cannot be reached."""


@contextmanager
def connect(
    settings: DatabaseSettings | None = None,
    *,
    autocommit: bool = True,
) -> Iterator[psycopg.Connection]:
    """Open one PostgreSQL connection with application defaults."""

    db_settings = settings or load_database_settings()
    try:
        with psycopg.connect(
            db_settings.conninfo,
            autocommit=autocommit,
            connect_timeout=db_settings.connect_timeout,
            row_factory=dict_row,
        ) as conn:
            yield conn
    except psycopg.Error as exc:
        raise DatabaseConnectionError(f"PostgreSQL connection failed: {exc}") from exc


def create_pool(settings: DatabaseSettings | None = None) -> ConnectionPool:
    """Create a lazy PostgreSQL connection pool.

    Callers own the pool lifecycle and should call pool.open() during service
    startup and pool.close() during shutdown.
    """

    db_settings = settings or load_database_settings()
    return ConnectionPool(
        conninfo=db_settings.conninfo,
        min_size=db_settings.pool_min_size,
        max_size=db_settings.pool_max_size,
        timeout=db_settings.connect_timeout,
        kwargs={
            "autocommit": True,
            "connect_timeout": db_settings.connect_timeout,
            "row_factory": dict_row,
        },
        open=False,
    )


def ping_database(settings: DatabaseSettings | None = None) -> dict[str, Any]:
    """Run a lightweight PostgreSQL health check query."""

    with connect(settings) as conn:
        row = conn.execute(
            """
            SELECT
                current_database() AS database,
                current_user AS user,
                inet_server_addr()::text AS host,
                inet_server_port() AS port,
                version() AS version
            """
        ).fetchone()

    if row is None:
        raise DatabaseConnectionError("PostgreSQL health check returned no rows")

    return dict(row)

