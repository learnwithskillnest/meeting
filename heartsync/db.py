"""SQLite data layer: connections, transactions and migrations.

SQL is kept close to standard so the schema can be ported to PostgreSQL
(see docs/DATABASE.md). All queries use bound parameters (no string
formatting of user input) to prevent SQL injection.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import get_settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    dt = dt or utcnow()
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso_in(**delta: float) -> str:
    return iso(utcnow() + timedelta(**delta))


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or get_settings().database_path
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Write transaction. BEGIN IMMEDIATE takes the write lock up front so
    read-then-write sequences (e.g. mutual-match detection) are atomic."""
    if conn.in_transaction:
        # Nested use: participate in the outer transaction.
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def one(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def all_(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def scalar(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def migrate(conn: sqlite3.Connection) -> list[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    ran: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        sql = path.read_text()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _split_sql(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)", (path.name, iso())
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        ran.append(path.name)
    return ran


def _split_sql(sql: str) -> list[str]:
    """Split a script into complete statements (comment- and quote-aware)."""
    statements, buf = [], ""
    for line in sql.splitlines(keepends=True):
        if not buf and line.strip().startswith("--"):
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                statements.append(buf.strip())
            buf = ""
    if buf.strip():
        raise ValueError("Incomplete SQL statement in migration")
    return statements
