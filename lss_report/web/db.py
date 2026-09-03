from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

SCHEMA = resources.files("lss_report.web").joinpath("schema.sql")


def connect(database_path: Path) -> sqlite3.Connection:
    if str(database_path) != ":memory:":
        database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialise(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA.read_text(encoding="utf-8"))
    migrate(connection)
    connection.commit()


# Columns added to an existing table after the first release. `CREATE TABLE IF NOT
# EXISTS` leaves a live database's older table untouched, so new columns have to be
# added here as well as in schema.sql.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("staff", "red_cross_number", "TEXT"),
)


def migrate(connection: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


class Database:
    """Small synchronous wrapper. One connection, serialised by SQLite itself.

    The app is single-process and low traffic, so a connection pool would be more
    machinery than the workload justifies.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection = connect(path)
        # One connection is shared by request threads and the scan worker. Without
        # this lock a rollback in one thread would discard another thread's
        # uncommitted rows, because the transaction belongs to the connection.
        self._lock = threading.RLock()
        initialise(self._connection)

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._connection
            except Exception:
                self._connection.rollback()
                raise
            self._connection.commit()

    def query(self, sql: str, parameters: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(sql, parameters).fetchall()

    def query_one(self, sql: str, parameters: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(sql, parameters).fetchone()

    def close(self) -> None:
        self._connection.close()
