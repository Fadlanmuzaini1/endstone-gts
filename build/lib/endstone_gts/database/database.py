"""SQLite wrapper: one connection, explicit IMMEDIATE transactions, versioned migrations."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .migrations import MIGRATIONS

APPLICATION_ID = 0x47545331  # "GTS1": marks a database file as belonging to this plugin
REQUIRED_COLUMNS = {
    "listings": {"id", "listing_type", "seller_uuid", "seller_name", "item_data", "item_identifier",
                 "display_name", "search_text", "amount", "price", "status", "reclaim_state",
                 "created_at", "expires_at", "updated_at"},
    "transactions": {"transaction_id", "kind", "listing_id", "seller_uuid", "buyer_uuid", "price",
                     "fee", "status", "step", "payout_state", "created_at", "completed_at"},
}


class DatabaseMismatch(RuntimeError):
    """The file is not a GTS database of a compatible layout. It is never modified."""


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._depth = 0
        self._conn: sqlite3.Connection | None = None

    # -- lifecycle -----------------------------------------------------------------
    def open(self) -> None:
        # isolation_level=None -> we issue BEGIN/COMMIT ourselves (no implicit transactions).
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")  # durability matters more than speed here
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._check_ownership()
        self.migrate()
        self._verify_schema()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not open")
        return self._conn

    # -- ownership / schema guard ---------------------------------------------------
    def _tables(self) -> set[str]:
        rows = self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
        return {r[0] for r in rows}

    def _check_ownership(self) -> None:
        """Refuse to touch a database that some other plugin/version created."""
        tables = self._tables()
        app_id = int(self.conn.execute("PRAGMA application_id").fetchone()[0])
        if not tables:
            self.conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            return
        if app_id == APPLICATION_ID:
            return
        if app_id == 0 and self.schema_version() > 0 and self._columns_ok(tables):
            self.conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")  # an earlier GTS file: adopt
            return
        raise DatabaseMismatch(
            f"'{self.path}' already contains tables ({', '.join(sorted(tables))}) that do not belong to this "
            "plugin version. Move or delete that file, or set [database] file in config.toml to a new name."
        )

    def _columns_ok(self, tables: set[str]) -> bool:
        for table, needed in REQUIRED_COLUMNS.items():
            if table not in tables:
                return False
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if not needed <= have:
                return False
        return True

    def _verify_schema(self) -> None:
        if not self._columns_ok(self._tables()):
            raise DatabaseMismatch(f"'{self.path}' has an unexpected layout after migration.")

    # -- migrations ----------------------------------------------------------------
    def schema_version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> int:
        current = self.schema_version()
        for version, statements in MIGRATIONS:
            if version <= current:
                continue
            with self.transaction():
                for stmt in statements:
                    self.conn.execute(stmt)
                self.conn.execute(f"PRAGMA user_version = {int(version)}")
            current = version
        return current

    # -- transactions --------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE: takes the write lock up front so check-then-write is atomic.

        Nested use joins the outer transaction.
        """
        with self._lock:
            if self._depth > 0:
                self._depth += 1
                try:
                    yield self.conn
                finally:
                    self._depth -= 1
                return
            self.conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self.conn
            except BaseException:
                self._depth = 0
                self.conn.execute("ROLLBACK")
                raise
            else:
                self._depth = 0
                self.conn.execute("COMMIT")

    # -- helpers -------------------------------------------------------------------
    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()
