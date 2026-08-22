"""
SQLite persistence for TelegramIdentity per instance.

Each instance has its own SQLite database at:
instances/{instance_id}/identities.db

Schema:
- schema_version table for migrations
- telegram_identities table with compound PK and unique constraints
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC
from pathlib import Path

from core.hermes.identity import TelegramIdentity
from core.hermes.instance_config import validate_instance_id
from core.hermes.utils import get_instances_root

SCHEMA_VERSION = 1

SCHEMA_SQL = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- Telegram identities per instance
CREATE TABLE IF NOT EXISTS telegram_identities (
    instance_id TEXT NOT NULL,
    telegram_user_id INTEGER NOT NULL,
    dolibarr_user_id INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    username_cache TEXT,
    first_name_cache TEXT,
    last_name_cache TEXT,
    PRIMARY KEY (instance_id, telegram_user_id),
    UNIQUE(instance_id, dolibarr_user_id)
);

-- Index for reverse lookup by dolibarr_user_id
CREATE INDEX IF NOT EXISTS idx_telegram_identities_dolibarr
    ON telegram_identities(instance_id, dolibarr_user_id);
"""


class CrossInstanceError(ValueError):
    """Raised when an identity from a different instance is used."""

    pass


class IdentityStore:
    """
    SQLite-backed store for TelegramIdentity.

    One database file per instance for natural isolation.
    Uses WAL mode for concurrent access.
    """

    def __init__(self, instance_id: str, instances_root: Path | None = None):
        # Validate instance_id to prevent path traversal
        validate_instance_id(instance_id)
        self.instance_id = instance_id
        self.instances_root = instances_root or get_instances_root()
        self.db_path = self.instances_root / instance_id / "identities.db"
        self._init_db()

    def _validate_identity_instance(self, identity: TelegramIdentity) -> None:
        """Validate that the identity belongs to this store's instance."""
        if identity.instance_id != self.instance_id:
            raise CrossInstanceError(
                f"Identity instance_id '{identity.instance_id}' does not match store instance_id '{self.instance_id}'"
            )

    def _init_db(self) -> None:
        """Initialize database with schema and WAL mode."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._connection() as conn:
            # Enable WAL mode for better concurrency
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA foreign_keys=ON;")

            # Run schema
            conn.executescript(SCHEMA_SQL)

            # Check/set schema version
            self._ensure_schema_version(conn)

    def _ensure_schema_version(self, conn: sqlite3.Connection) -> None:
        """Ensure schema version is current, run migrations if needed."""
        cursor = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        row = cursor.fetchone()
        current_version = row[0] if row else 0

        if current_version < SCHEMA_VERSION:
            # Run migrations (for future versions)
            # For now, just set version
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, datetime('now'))",
                (SCHEMA_VERSION,),
            )
            conn.commit()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Context manager for database connections."""
        conn: sqlite3.Connection = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # =========================================================================
    # CRUD Operations
    # =========================================================================

    def get(self, telegram_user_id: int) -> TelegramIdentity | None:
        """Get identity by telegram_user_id."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM telegram_identities WHERE instance_id = ? AND telegram_user_id = ?",
                (self.instance_id, telegram_user_id),
            )
            row = cursor.fetchone()
            return TelegramIdentity.from_row(dict(row)) if row else None

    def get_by_dolibarr_user(self, dolibarr_user_id: int) -> TelegramIdentity | None:
        """Get identity by dolibarr_user_id (reverse lookup)."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM telegram_identities WHERE instance_id = ? AND dolibarr_user_id = ?",
                (self.instance_id, dolibarr_user_id),
            )
            row = cursor.fetchone()
            return TelegramIdentity.from_row(dict(row)) if row else None

    def create(self, identity: TelegramIdentity) -> None:
        """Create new identity. Raises IntegrityError if constraints violated."""
        self._validate_identity_instance(identity)
        with self._connection() as conn:
            row = identity.to_row()
            conn.execute(
                """
                INSERT INTO telegram_identities (
                    instance_id, telegram_user_id, dolibarr_user_id,
                    enabled, created_at, last_seen_at,
                    username_cache, first_name_cache, last_name_cache
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["instance_id"],
                    row["telegram_user_id"],
                    row["dolibarr_user_id"],
                    row["enabled"],
                    row["created_at"],
                    row["last_seen_at"],
                    row["username_cache"],
                    row["first_name_cache"],
                    row["last_name_cache"],
                ),
            )
            conn.commit()

    def update(self, identity: TelegramIdentity) -> None:
        """Update existing identity."""
        self._validate_identity_instance(identity)
        with self._connection() as conn:
            row = identity.to_row()
            cursor = conn.execute(
                """
                UPDATE telegram_identities SET
                    dolibarr_user_id = ?,
                    enabled = ?,
                    last_seen_at = ?,
                    username_cache = ?,
                    first_name_cache = ?,
                    last_name_cache = ?
                WHERE instance_id = ? AND telegram_user_id = ?
                """,
                (
                    row["dolibarr_user_id"],
                    row["enabled"],
                    row["last_seen_at"],
                    row["username_cache"],
                    row["first_name_cache"],
                    row["last_name_cache"],
                    row["instance_id"],
                    row["telegram_user_id"],
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Identity not found: {identity.instance_id}/{identity.telegram_user_id}")
            conn.commit()

    def delete(self, telegram_user_id: int) -> None:
        """Delete identity by telegram_user_id."""
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM telegram_identities WHERE instance_id = ? AND telegram_user_id = ?",
                (self.instance_id, telegram_user_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Identity not found: {self.instance_id}/{telegram_user_id}")
            conn.commit()

    def list_all(self) -> list[TelegramIdentity]:
        """List all identities for this instance."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM telegram_identities WHERE instance_id = ? ORDER BY created_at",
                (self.instance_id,),
            )
            return [TelegramIdentity.from_row(dict(row)) for row in cursor.fetchall()]

    def set_enabled(self, telegram_user_id: int, enabled: bool) -> None:
        """Update enabled status."""
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE telegram_identities SET enabled = ? WHERE instance_id = ? AND telegram_user_id = ?",
                (int(enabled), self.instance_id, telegram_user_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Identity not found: {self.instance_id}/{telegram_user_id}")
            conn.commit()

    def update_last_seen(self, telegram_user_id: int) -> None:
        """Update last_seen_at to now."""
        from datetime import datetime

        now = datetime.now(UTC).isoformat()
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE telegram_identities SET last_seen_at = ? WHERE instance_id = ? AND telegram_user_id = ?",
                (now, self.instance_id, telegram_user_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Identity not found: {self.instance_id}/{telegram_user_id}")
            conn.commit()

    # =========================================================================
    # Utility
    # =========================================================================

    def exists(self, telegram_user_id: int) -> bool:
        """Check if identity exists."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM telegram_identities WHERE instance_id = ? AND telegram_user_id = ?",
                (self.instance_id, telegram_user_id),
            )
            return cursor.fetchone() is not None

    def count(self) -> int:
        """Count identities for this instance."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM telegram_identities WHERE instance_id = ?",
                (self.instance_id,),
            )
            row = cursor.fetchone()
            return row[0] if row else 0
