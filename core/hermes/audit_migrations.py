"""
Database Migrations for gestor_ia_audit (MariaDB).

Versioned, idempotent migrations for the audit database schema.
Detects existing schema and applies only missing changes.
Does NOT touch Dolibarr databases.

IMPORTANT: Runs with gestor_ia_audit user (no CREATE TABLE permission).
Migrations are idempotent by checking column/table existence before ALTER.
"""

from __future__ import annotations

import structlog
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

from sqlalchemy import create_engine, inspect, text

if TYPE_CHECKING:
    from core.hermes.instance_config import InstanceConfig

logger = structlog.get_logger()


def get_audit_database_url(instance_config: InstanceConfig | None = None, database_url: str | None = None) -> str:
    """Get the audit database URL (gestor_ia_audit)."""
    if database_url:
        return database_url if database_url.startswith("mysql+pymysql://") else database_url.replace("mysql://", "mysql+pymysql://")

    if instance_config:
        from core.hermes.config import get_global_settings

        settings = get_global_settings()
        return (
            f"mysql+pymysql://gestor_ia_audit:{settings.MARIADB_AUDIT_PASSWORD}@"
            f"{settings.MARIADB_HOST}:{settings.MARIADB_PORT}/gestor_ia_audit"
        )

    from core.hermes.config import get_global_settings

    settings = get_global_settings()
    return (
        f"mysql+pymysql://gestor_ia_audit:{settings.MARIADB_AUDIT_PASSWORD}@"
        f"{settings.MARIADB_HOST}:{settings.MARIADB_PORT}/gestor_ia_audit"
    )


@contextmanager
def audit_engine(database_url: str) -> Iterator:
    """Create engine for audit database with proper URL format."""
    engine_url = database_url if database_url.startswith("mysql+pymysql://") else database_url.replace("mysql://", "mysql+pymysql://")
    engine = create_engine(
        engine_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )
    try:
        yield engine
    finally:
        engine.dispose()


def column_exists(engine, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return False
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def table_exists(engine, table_name: str) -> bool:
    """Check if a table exists."""
    inspector = inspect(engine)
    return table_name in inspector.get_table_names()


def index_exists(engine, table_name: str, index_name: str) -> bool:
    """Check if an index exists on a table."""
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return False
    indexes = inspector.get_indexes(table_name)
    return any(idx["name"] == index_name for idx in indexes)


def run_migration_add_dolibarr_invoice_columns(engine) -> bool:
    """Migration: Add dolibarr_invoice_id and dolibarr_invoice_ref columns to document_idempotency_record.

    Idempotent: checks if columns exist before adding.
    Returns True if any change was made.
    """
    logger.info("Checking migration: Add dolibarr_invoice_id and dolibarr_invoice_ref columns")

    # Check if table exists
    if not table_exists(engine, "document_idempotency_record"):
        logger.warning("Table document_idempotency_record does not exist - skipping migration")
        return False

    # Check if columns already exist
    has_invoice_id = column_exists(engine, "document_idempotency_record", "dolibarr_invoice_id")
    has_invoice_ref = column_exists(engine, "document_idempotency_record", "dolibarr_invoice_ref")

    if has_invoice_id and has_invoice_ref:
        logger.info("Migration skipped: dolibarr_invoice_id and dolibarr_invoice_ref already exist")
        return False

    changes_made = False

    # Detect dialect for proper ALTER TABLE syntax
    dialect_name = engine.dialect.name  # 'mysql', 'mariadb', 'sqlite', etc.

    with engine.connect() as conn:
        if not has_invoice_id:
            logger.info("Adding column dolibarr_invoice_id")
            try:
                if dialect_name in ("mysql", "mariadb"):
                    # MySQL/MariaDB: can add column and index in one statement
                    conn.execute(text("""
                        ALTER TABLE document_idempotency_record
                        ADD COLUMN dolibarr_invoice_id INTEGER NULL,
                        ADD INDEX ix_idempotency_dolibarr_invoice_id (dolibarr_invoice_id)
                    """))
                else:
                    # SQLite and others: add column first, then create index separately
                    conn.execute(text("""
                        ALTER TABLE document_idempotency_record
                        ADD COLUMN dolibarr_invoice_id INTEGER NULL
                    """))
                    conn.execute(text("""
                        CREATE INDEX ix_idempotency_dolibarr_invoice_id
                        ON document_idempotency_record (dolibarr_invoice_id)
                    """))
                changes_made = True
            except Exception as e:
                logger.warning("Could not add dolibarr_invoice_id (may already exist)", error=str(e))

        if not has_invoice_ref:
            logger.info("Adding column dolibarr_invoice_ref")
            try:
                if dialect_name in ("mysql", "mariadb"):
                    conn.execute(text("""
                        ALTER TABLE document_idempotency_record
                        ADD COLUMN dolibarr_invoice_ref VARCHAR(100) NULL,
                        ADD INDEX ix_idempotency_dolibarr_invoice_ref (dolibarr_invoice_ref)
                    """))
                else:
                    conn.execute(text("""
                        ALTER TABLE document_idempotency_record
                        ADD COLUMN dolibarr_invoice_ref VARCHAR(100) NULL
                    """))
                    conn.execute(text("""
                        CREATE INDEX ix_idempotency_dolibarr_invoice_ref
                        ON document_idempotency_record (dolibarr_invoice_ref)
                    """))
                changes_made = True
            except Exception as e:
                logger.warning("Could not add dolibarr_invoice_ref (may already exist)", error=str(e))

        if changes_made:
            conn.commit()

    if changes_made:
        logger.info("Migration completed: dolibarr_invoice_id and/or dolibarr_invoice_ref added")
    return changes_made


def run_migration_verify_no_duplicate_timestamps(engine) -> bool:
    """Migration: Verify no duplicate created_at/completed_at columns exist.

    This is a verification-only migration. MariaDB would reject duplicate column names
    at CREATE TABLE time, but we verify the schema is clean.
    """
    logger.info("Running verification: Check for duplicate timestamp columns")

    if not table_exists(engine, "document_idempotency_record"):
        logger.warning("Table document_idempotency_record does not exist")
        return False

    inspector = inspect(engine)
    columns = [col["name"] for col in inspector.get_columns("document_idempotency_record")]

    created_at_count = sum(1 for c in columns if c.lower() == "created_at")
    completed_at_count = sum(1 for c in columns if c.lower() == "completed_at")

    if created_at_count > 1:
        logger.warning("Multiple created_at columns detected!", count=created_at_count, columns=columns)
    if completed_at_count > 1:
        logger.warning("Multiple completed_at columns detected!", count=completed_at_count, columns=columns)

    logger.info("Schema verification done", columns=columns)
    return False  # No changes made (verification only)


def run_audit_migrations(database_url: str | None = None, instance_config: "InstanceConfig | None" = None) -> dict[str, any]:
    """
    Run all pending migrations for the audit database.

    Idempotent: can be run multiple times safely.
    Does NOT require CREATE TABLE permission (uses ALTER TABLE only).
    Does NOT touch Dolibarr databases.

    Returns:
        dict with migration results
    """
    import structlog
    global logger
    logger = structlog.get_logger()

    url = get_audit_database_url(instance_config, database_url)

    with audit_engine(url) as engine:
        logger.info("Audit schema check - running migrations")

        migrations_applied = []
        changes_made = False

        # Migration 1: Add dolibarr invoice columns
        if run_migration_add_dolibarr_invoice_columns(engine):
            migrations_applied.append("add_dolibarr_invoice_columns")
            changes_made = True

        # Migration 2: Verify no duplicate timestamps
        run_migration_verify_no_duplicate_timestamps(engine)

        logger.info("Audit migrations completed", migrations_applied=migrations_applied, changes_made=changes_made)

        return {
            "success": True,
            "migrations_applied": migrations_applied,
            "changes_made": changes_made,
        }


def verify_audit_schema(database_url: str | None = None, instance_config: "InstanceConfig | None" = None) -> dict[str, any]:
    """Verify the audit database schema matches expectations."""
    import structlog
    global logger
    logger = structlog.get_logger()

    url = get_audit_database_url(instance_config, database_url)

    with audit_engine(url) as engine:
        inspector = inspect(engine)
        tables = inspector.get_table_names()

        result = {
            "tables": tables,
            "document_idempotency_record": {},
            "audit_log": {},
        }

        # Check document_idempotency_record columns
        if "document_idempotency_record" in tables:
            columns = {col["name"]: col for col in inspector.get_columns("document_idempotency_record")}
            result["document_idempotency_record"] = {
                "columns": list(columns.keys()),
                "has_dolibarr_invoice_id": "dolibarr_invoice_id" in columns,
                "has_dolibarr_invoice_ref": "dolibarr_invoice_ref" in columns,
                "has_duplicate_created_at": sum(1 for c in columns if c.lower() == "created_at") > 1,
                "has_duplicate_completed_at": sum(1 for c in columns if c.lower() == "completed_at") > 1,
            }

            # Check indexes
            indexes = inspector.get_indexes("document_idempotency_record")
            result["document_idempotency_record"]["indexes"] = [idx["name"] for idx in indexes]

        # Check audit_log columns
        if "audit_log" in tables:
            columns = {col["name"]: col for col in inspector.get_columns("audit_log")}
            result["audit_log"] = {
                "columns": list(columns.keys()),
            }

        return result