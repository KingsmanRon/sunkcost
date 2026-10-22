"""Postgres access for the worker: session pooler, ``SET LOCAL app.org_id`` per org transaction."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

Conn = Connection[dict[str, Any]]


class WorkerDb:
    def __init__(self, url: str, *, max_size: int = 4) -> None:
        self.url = url
        self.pool: ConnectionPool[Conn] = ConnectionPool(
            url,
            min_size=1,
            max_size=max_size,
            open=True,
            kwargs={"row_factory": dict_row, "autocommit": False, "prepare_threshold": None},
        )

    @contextmanager
    def tx(self, org_id: str | None = None) -> Iterator[Conn]:
        """A transaction acting for ``org_id`` (RLS then sees only that org)."""
        with self.pool.connection() as conn, conn.transaction():
            if org_id is not None:
                conn.execute("select set_config('app.org_id', %s, true)", (org_id,))
            yield conn

    def close(self) -> None:
        self.pool.close()


def one(conn: Conn, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> dict[str, Any] | None:
    return conn.execute(sql, params).fetchone()


def required(conn: Conn, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> dict[str, Any]:
    row = one(conn, sql, params)
    if row is None:
        raise RuntimeError("query returned no row")
    return row


def rows(conn: Conn, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[dict[str, Any]]:
    return conn.execute(sql, params).fetchall()
