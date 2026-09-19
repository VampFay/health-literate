"""Async SQLAlchemy engine, session factory, and sqlite-vec loader.

Spec §2 calls for PostgreSQL 16+ with pgvector. Demo uses SQLite + sqlite-vec
(see README §Database). The SQLAlchemy schema is portable; swapping to
PostgreSQL+pgvector in production requires changing DATABASE_URL + the
VECTOR column type + loading `vector` extension instead of `sqlite-vec`.

Critical: SQLite must run with `foreign_keys=ON` (default OFF in the C API).
"""
from __future__ import annotations

import sqlite3
from typing import Any

import sqlite_vec
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings


def _get_raw_sqlite_conn(dbapi_conn: Any) -> sqlite3.Connection:
    """Traverse SQLAlchemy's async adapter stack to reach the raw sqlite3.Connection.

    SQLAlchemy's aiosqlite dialect wraps the connection in
    `AsyncAdapt_aiosqlite_connection` -> `aiosqlite.Connection` -> `sqlite3.Connection`.
    Only the deepest layer has `enable_load_extension`, which sqlite-vec needs.
    """
    candidate = dbapi_conn
    # Walk down the wrapper chain until we hit a real sqlite3.Connection.
    for _ in range(5):
        if isinstance(candidate, sqlite3.Connection):
            return candidate
        # SQLAlchemy async adapter and aiosqlite both use `_connection`
        # as the inner-handle attribute.
        candidate = getattr(candidate, "_connection", None)
        if candidate is None:
            break
    raise RuntimeError(
        "Could not reach raw sqlite3.Connection from "
        f"{type(dbapi_conn).__name__} — sqlite-vec load will fail. "
        "If SQLAlchemy/aiosqlite changed their adapter layout, update "
        "_get_raw_sqlite_conn in /core/db.py."
    )


def _load_sqlite_vec(dbapi_conn: Any) -> None:
    """Enable sqlite-vec extension on a raw sqlite3 connection.

    Called via SQLAlchemy's `connect` event for the sqlite dialect only.
    """
    raw = _get_raw_sqlite_conn(dbapi_conn)
    raw.enable_load_extension(True)
    try:
        sqlite_vec.load(raw)
    finally:
        raw.enable_load_extension(False)


def _set_sqlite_pragmas(dbapi_conn: Any) -> None:
    """Enable FK enforcement, WAL mode, and busy timeout."""
    raw = _get_raw_sqlite_conn(dbapi_conn)
    cursor = raw.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


_engine: Any = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> Any:
    """Return cached async engine, creating it on first call."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            future=True,
        )

        @event.listens_for(_engine.sync_engine, "connect")
        def _on_connect(dbapi_conn, _record):
            _set_sqlite_pragmas(dbapi_conn)
            _load_sqlite_vec(dbapi_conn)

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return cached session factory bound to the async engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_session() -> AsyncSession:
    """FastAPI dependency yielding an async DB session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def verify_sqlite_vec_loaded() -> tuple[str, str]:
    """Sanity check: confirm both SQLite and sqlite-vec are loadable.

    Returns (sqlite_version, sqlite_vec_version). Used by Phase 0 health
    endpoint and by future integration tests.
    """
    factory = get_session_factory()
    async with factory() as session:
        sqlite_version = (await session.execute(text("SELECT sqlite_version()"))).scalar()
        vec_version = (
            await session.execute(text("SELECT vec_version()"))
        ).scalar()
    return sqlite_version, vec_version


async def dispose_engine() -> None:
    """Clean shutdown — used by tests and lifespan teardown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
