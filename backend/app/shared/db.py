"""Connection pool and transaction helper. The only place that knows how to reach PostgreSQL."""
from contextlib import contextmanager
from typing import Iterator, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings
from app.shared import domain_events

_pool: Optional[ConnectionPool] = None


def init_pool(database_url: Optional[str] = None) -> ConnectionPool:
    global _pool
    close_pool()
    _pool = ConnectionPool(
        database_url or settings.database_url,
        min_size=1,
        max_size=10,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=True,
    )
    return _pool


def get_pool() -> ConnectionPool:
    return _pool if _pool is not None else init_pool()


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def transaction() -> Iterator[psycopg.Connection]:
    """One database transaction: commit on success, roll back on any exception.

    Domain notifications queued inside the transaction are dispatched only after commit.
    """
    with get_pool().connection() as conn:
        token = domain_events.begin()
        try:
            with conn.transaction():
                yield conn
        except BaseException:
            domain_events.discard(token)
            raise
        domain_events.flush(token)
