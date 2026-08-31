from __future__ import annotations

import sqlite3
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
    connection.commit()


class Database:
    """Small synchronous wrapper. One connection, serialised by SQLite itself.

    The app is single-process and low traffic, so a connection pool would be more
    machinery than the workload justifies.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection = connect(path)
        initialise(self._connection)

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._connection
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()

    def query(self, sql: str, parameters: tuple = ()) -> list[sqlite3.Row]:
        return self._connection.execute(sql, parameters).fetchall()

    def query_one(self, sql: str, parameters: tuple = ()) -> sqlite3.Row | None:
        return self._connection.execute(sql, parameters).fetchone()

    def close(self) -> None:
        self._connection.close()
