"""Postgres access. Every org-scoped transaction runs ``SET LOCAL app.org_id`` first (§8.1).

The Supabase transaction pooler (Supavisor, port 6543) does not support prepared statements, so
they are disabled (``prepare_threshold=None``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

Conn = AsyncConnection[dict[str, Any]]


class Database:
    def __init__(self, url: str, *, min_size: int = 1, max_size: int = 10) -> None:
        self.pool: AsyncConnectionPool[Conn] = AsyncConnectionPool(
            url,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"prepare_threshold": None, "row_factory": dict_row, "autocommit": False},
        )

    async def open(self) -> None:
        await self.pool.open(wait=False)

    async def close(self) -> None:
        await self.pool.close()

    @asynccontextmanager
    async def tx(self, org_id: str | None = None) -> AsyncIterator[Conn]:
        """A transaction, acting for ``org_id`` when given (RLS sees only that org)."""
        async with self.pool.connection() as conn, conn.transaction():
            if org_id is not None:
                await conn.execute("select set_config('app.org_id', %s, true)", (org_id,))
            yield conn

    async def ping(self) -> bool:
        try:
            async with self.pool.connection(timeout=2.0) as conn:
                await conn.execute("select 1")
            return True
        except Exception:
            return False


async def one(
    conn: Conn, sql: str, params: tuple[Any, ...] | list[Any] = ()
) -> dict[str, Any] | None:
    """The first row of a query, or None."""
    return await (await conn.execute(sql, params)).fetchone()


async def required(
    conn: Conn, sql: str, params: tuple[Any, ...] | list[Any] = ()
) -> dict[str, Any]:
    """The first row of a query that must return one (``insert … returning``, for example)."""
    row = await one(conn, sql, params)
    if row is None:
        raise RuntimeError("query returned no row")
    return row


async def rows(
    conn: Conn, sql: str, params: tuple[Any, ...] | list[Any] = ()
) -> list[dict[str, Any]]:
    return await (await conn.execute(sql, params)).fetchall()
