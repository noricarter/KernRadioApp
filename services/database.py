"""Small file-backed SQLite service. No domain schema or application imports."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Iterable, Iterator, Mapping, Sequence

Parameters = Sequence[object] | Mapping[str, object]


class DatabaseError(Exception):
    """Database access failed; the underlying exception is available as __cause__."""


class DatabaseConstraintError(DatabaseError):
    """A uniqueness, foreign-key, or other integrity constraint was violated."""


@dataclass(frozen=True)
class WriteResult:
    lastrowid: int | None
    rowcount: int


class DatabaseSession:
    """SQL operations scoped to a DatabaseService transaction; no raw cursors escape."""
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def execute(self, sql: str, parameters: Parameters = ()) -> WriteResult:
        cursor = self._connection.execute(sql, parameters)
        try:
            return WriteResult(cursor.lastrowid, cursor.rowcount)
        finally:
            cursor.close()

    def fetch_one(self, sql: str, parameters: Parameters = ()) -> dict | None:
        cursor = self._connection.execute(sql, parameters)
        try:
            row = cursor.fetchone()
            return dict(row) if row is not None else None
        finally:
            cursor.close()

    def fetch_all(self, sql: str, parameters: Parameters = ()) -> list[dict]:
        cursor = self._connection.execute(sql, parameters)
        try:
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()


class DatabaseService:
    """One short-lived connection per operation/transaction, safe across threads.

    Construct with a file path. Parent directories are created automatically.
    Transactions commit on success and roll back on any exception. Sessions must
    only be used inside their context; nested transactions are not supported.
    """
    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DatabaseError('Could not create the database directory.') from exc

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[DatabaseSession]:
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys = ON')
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield DatabaseSession(connection)
            connection.commit()
        except BaseException as exc:
            if connection is not None:
                connection.rollback()
            if isinstance(exc, sqlite3.IntegrityError):
                raise DatabaseConstraintError('A database constraint was violated.') from exc
            if isinstance(exc, sqlite3.Error):
                raise DatabaseError('The database operation could not be completed.') from exc
            raise
        finally:
            if connection is not None:
                connection.close()

    def initialize(self, statements: Iterable[str]) -> None:
        """Apply caller-owned, idempotent schema statements atomically."""
        with self.transaction() as session:
            for statement in statements:
                session.execute(statement)

    def execute(self, sql: str, parameters: Parameters = ()) -> WriteResult:
        with self.transaction() as session:
            return session.execute(sql, parameters)

    def fetch_one(self, sql: str, parameters: Parameters = ()) -> dict | None:
        with self.transaction(write=False) as session:
            return session.fetch_one(sql, parameters)

    def fetch_all(self, sql: str, parameters: Parameters = ()) -> list[dict]:
        with self.transaction(write=False) as session:
            return session.fetch_all(sql, parameters)
