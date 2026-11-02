"""Database access layer (MySQL / MariaDB).

Design rules followed here:
  * every statement is parameterised (%s placeholders) -- no string
    concatenation of user input, which prevents SQL injection;
  * connections come from a small pool and are always returned;
  * transaction boundaries are explicit (commit / rollback);
  * driver errors are translated into application errors so the API can
    answer with a meaningful HTTP status instead of a stack trace.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterable, Sequence

import mysql.connector
from mysql.connector import Error as MySQLError
from mysql.connector import pooling

from config import config

log = logging.getLogger(__name__)

# MySQL / MariaDB error numbers with a special meaning for the API layer
ER_DUP_ENTRY = 1062
ER_NO_REFERENCED_ROW_2 = 1452
ER_ROW_IS_REFERENCED_2 = 1451
ER_CHECK_CONSTRAINT_VIOLATED = 3819
ER_BAD_NULL_ERROR = 1048
ER_DATA_TOO_LONG = 1406
CONNECTION_ERRORS = {2002, 2003, 2005, 2006, 2013, 2055}


class DatabaseError(RuntimeError):
    """Raised when the database cannot be reached or a statement fails."""

    def __init__(self, message: str, *, errno: int | None = None, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.errno = errno
        self.detail = detail

    @property
    def unavailable(self) -> bool:
        return self.errno in CONNECTION_ERRORS

    @property
    def is_duplicate(self) -> bool:
        return self.errno == ER_DUP_ENTRY

    @property
    def is_constraint(self) -> bool:
        return self.errno in (ER_NO_REFERENCED_ROW_2, ER_CHECK_CONSTRAINT_VIOLATED,
                              ER_BAD_NULL_ERROR, ER_DATA_TOO_LONG)

    @property
    def is_referenced(self) -> bool:
        return self.errno == ER_ROW_IS_REFERENCED_2


class Database:
    """Thin wrapper around a mysql.connector connection pool."""

    def __init__(self, cfg=config):
        self.cfg = cfg
        self._pool: pooling.MySQLConnectionPool | None = None

    # ------------------------------------------------------------------
    def _build_pool(self) -> pooling.MySQLConnectionPool:
        kwargs = self.cfg.db_kwargs()
        kwargs["pool_name"] = "scm_pool"
        kwargs["pool_size"] = max(1, self.cfg.DB_POOL_SIZE)
        kwargs["pool_reset_session"] = True
        return pooling.MySQLConnectionPool(**kwargs)

    @property
    def pool(self) -> pooling.MySQLConnectionPool:
        if self._pool is None:
            try:
                self._pool = self._build_pool()
            except MySQLError as exc:
                raise DatabaseError(
                    "Cannot connect to the MySQL server. Check DB_HOST/DB_PORT/DB_USER/"
                    "DB_PASSWORD in your .env file and that the server is running.",
                    errno=getattr(exc, "errno", None),
                    detail=str(exc),
                ) from exc
        return self._pool

    def configure(self, cfg) -> None:
        """Point this instance at another configuration (used by tests)."""
        if cfg is not self.cfg:
            self._pool = None
        self.cfg = cfg

    def close(self) -> None:
        self._pool = None

    # ------------------------------------------------------------------
    @contextmanager
    def cursor(self, *, dictionary: bool = True, commit: bool = False):
        """Yield a cursor; commit on success, rollback on failure."""
        try:
            conn = self.pool.get_connection()
        except MySQLError as exc:
            raise DatabaseError(
                "Database connection could not be obtained from the pool.",
                errno=getattr(exc, "errno", None), detail=str(exc)) from exc
        cur = None
        try:
            cur = conn.cursor(dictionary=dictionary)
            yield cur
            if commit:
                conn.commit()
        except MySQLError as exc:
            try:
                conn.rollback()
            except MySQLError:      # connection already dead
                pass
            raise DatabaseError(
                _human_message(exc), errno=getattr(exc, "errno", None), detail=str(exc)) from exc
        except Exception:
            try:
                conn.rollback()
            except MySQLError:
                pass
            raise
        finally:
            if cur is not None:
                try:
                    cur.close()
                except MySQLError:
                    pass
            try:
                conn.close()
            except MySQLError:
                pass

    # ------------------------------------------------------------------
    def fetch_all(self, sql: str, params: Sequence[Any] | None = None) -> list[dict]:
        with self.cursor() as cur:
            cur.execute(sql, params or ())
            return list(cur.fetchall())

    def fetch_one(self, sql: str, params: Sequence[Any] | None = None) -> dict | None:
        with self.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()

    def fetch_scalar(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        row = self.fetch_one(sql, params)
        if not row:
            return None
        return next(iter(row.values()))

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> int:
        """Run a single INSERT/UPDATE/DELETE and return lastrowid (or rowcount)."""
        with self.cursor(commit=True) as cur:
            cur.execute(sql, params or ())
            return cur.lastrowid or cur.rowcount

    def execute_many(self, sql: str, seq: Iterable[Sequence[Any]]) -> int:
        with self.cursor(commit=True) as cur:
            cur.executemany(sql, list(seq))
            return cur.rowcount

    @contextmanager
    def transaction(self):
        """Multi-statement transaction: yields a cursor, commits at the end."""
        with self.cursor(commit=True) as cur:
            yield cur

    # ------------------------------------------------------------------
    def health(self) -> dict:
        """Used by GET /api/health. Never raises."""
        info = {
            "database": self.cfg.DB_NAME,
            "host": self.cfg.DB_HOST,
            "port": self.cfg.DB_PORT,
            "connected": False,
        }
        try:
            with self.cursor() as cur:
                cur.execute("SELECT VERSION() AS v, DATABASE() AS db, NOW() AS ts")
                row = cur.fetchone() or {}
            info.update({
                "connected": True,
                "server_version": row.get("v"),
                "schema": row.get("db"),
                "server_time": str(row.get("ts")),
            })
        except DatabaseError as exc:
            info["error"] = exc.message
        return info


def _human_message(exc: MySQLError) -> str:
    errno = getattr(exc, "errno", None)
    if errno == ER_DUP_ENTRY:
        return "Duplicate value: a record with this unique key already exists."
    if errno == ER_NO_REFERENCED_ROW_2:
        return "Foreign key violation: the referenced parent record does not exist."
    if errno == ER_ROW_IS_REFERENCED_2:
        return "Record is still referenced by child records and cannot be deleted."
    if errno == ER_CHECK_CONSTRAINT_VIOLATED:
        return "A database CHECK constraint rejected the values (range or sign problem)."
    if errno in CONNECTION_ERRORS:
        return "Lost connection to the MySQL server."
    return f"Database error ({errno}): {exc.msg if hasattr(exc, 'msg') else exc}"


# Module-level singleton used by the API and the analysis pipeline.
db = Database()
