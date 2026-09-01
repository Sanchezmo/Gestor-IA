"""
Database Migrations for gestor_ia_audit (MariaDB).

Versioned, idempotent migrations for the audit database schema.
Detects existing schema and applies only missing changes.
Does NOT touch Dolibarr databases.

IMPORTANT: Runs with gestor_ia_audit user (no CREATE TABLE permission).
Migrations are idempotent by checking column/table existence before ALTER.
Bootstrap (create_all) runs first for new databases, then migrations apply.
"""

from __future__ import annotations

import structlog
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

from sqlalchemy import create_engine, inspect, text

if TYPE_CHECKING:
    from core.hermes.instance_config import InstanceConfig

logger = structlog.get_logger()


class AuditSchemaValidationError(Exception):
    """Raised when audit schema validation fails - FAIL CLOSED."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


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
    """Create engine for audit database with proper URL format and dialect-specific options."""
    engine_url = database_url if database_url.startswith("mysql+pymysql://") else database_url.replace("mysql://", "mysql+pymysql://")

    # Dialect-specific engine options
    is_sqlite = engine_url.startswith("sqlite")
    if is_sqlite:
        engine = create_engine(
            engine_url,
            echo=False,
        )
    else:
        # MariaDB/MySQL with pooling
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


def bootstrap_audit_schema(engine) -> bool:
    """
    Bootstrap the audit database schema (create all tables).

    Idempotent: safe to run multiple times. Uses SQLAlchemy's create_all()
    which only creates missing tables.

    This runs BEFORE migrations. For new databases, it creates the base schema.
    For existing databases, it's a no-op.

    Args:
        engine: SQLAlchemy engine connected to gestor_ia_audit database

    Returns:
        True if any tables were created, False if schema already existed
    """
    # Import Base from audit module to get table definitions
    from core.hermes.audit import Base

    logger.info("Bootstrapping audit schema (create_all)")

    # Check if any tables exist before create_all
    inspector = inspect(engine)
    tables_before = set(inspector.get_table_names())

    # Create all tables (idempotent - only creates missing)
    Base.metadata.create_all(engine)

    # Check what was created - MUST create fresh inspector to avoid caching
    inspector = inspect(engine)
    tables_after = set(inspector.get_table_names())
    created = tables_after - tables_before

    if created:
        logger.info("Audit schema bootstrap created tables", tables=sorted(created))
        return True
    else:
        logger.info("Audit schema bootstrap: no new tables created (already exist)")
        return False


def validate_audit_schema(engine) -> dict[str, any]:
    """
    Validate the audit database schema matches minimum requirements.

    FAIL CLOSED: Raises AuditSchemaValidationError if schema is incomplete.

    Checks:
    - Both required tables exist (audit_log, document_idempotency_record)
    - document_idempotency_record has all critical columns
    - Critical indexes exist (ux_idempotency_dedup)

    Args:
        engine: SQLAlchemy engine connected to gestor_ia_audit database

    Returns:
        Validation detail dict

    Raises:
        AuditSchemaValidationError: If schema validation fails
    """
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    errors = []
    warnings = []

    # Required tables
    required_tables = {"audit_log", "document_idempotency_record"}
    missing_tables = required_tables - set(tables)
    if missing_tables:
        errors.append(f"Missing required tables: {sorted(missing_tables)}")

    result = {
        "valid": len(errors) == 0,
        "tables": tables,
        "errors": errors,
        "warnings": warnings,
        "document_idempotency_record": {},
        "audit_log": {},
    }

    # Check document_idempotency_record columns
    if "document_idempotency_record" in tables:
        columns = {col["name"]: col for col in inspector.get_columns("document_idempotency_record")}
        col_names = set(columns.keys())

        # Critical columns that MUST exist
        critical_columns = {
            "id", "created_at", "completed_at", "instance_id", "document_hash",
            "supplier_tax_id", "supplier_invoice_number", "supplier_dolibarr_id",
            "invoice_dolibarr_id", "dolibarr_invoice_ref", "dolibarr_invoice_id",
            "final_state", "attachment_uploaded", "document_filename",
            "document_mime_type", "document_size_bytes", "correlation_id",
        }

        missing_critical = critical_columns - col_names
        if missing_critical:
            errors.append(f"document_idempotency_record missing critical columns: {sorted(missing_critical)}")

        # Specifically check the two columns added by migration
        if "dolibarr_invoice_id" not in col_names:
            errors.append("document_idempotency_record missing dolibarr_invoice_id column")
        if "dolibarr_invoice_ref" not in col_names:
            errors.append("document_idempotency_record missing dolibarr_invoice_ref column")

        # Check indexes
        indexes = {idx["name"] for idx in inspector.get_indexes("document_idempotency_record")}
        if "ux_idempotency_dedup" not in indexes:
            errors.append("Missing critical unique index: ux_idempotency_dedup")

        result["document_idempotency_record"] = {
            "columns": sorted(col_names),
            "missing_critical": sorted(missing_critical),
            "has_dolibarr_invoice_id": "dolibarr_invoice_id" in col_names,
            "has_dolibarr_invoice_ref": "dolibarr_invoice_ref" in col_names,
            "indexes": sorted(indexes),
        }

    # Check audit_log columns
    if "audit_log" in tables:
        columns = {col["name"]: col for col in inspector.get_columns("audit_log")}
        result["audit_log"] = {
            "columns": sorted(columns.keys()),
        }

    result["valid"] = len(errors) == 0
    result["errors"] = errors

    if errors:
        logger.error("Audit schema validation FAILED", errors=errors, details=result)
        raise AuditSchemaValidationError(
            f"Audit schema validation failed: {'; '.join(errors)}",
            details=result,
        )

    logger.info("Audit schema validation PASSED", tables=tables)
    return result


def run_audit_migrations(database_url: str | None = None, instance_config: "InstanceConfig | None" = None) -> dict[str, any]:
    """
    Run bootstrap + all pending migrations for the audit database.

    Idempotent: can be run multiple times safely.
    Bootstrap (create_all) runs first, then migrations (ALTER).
    Does NOT touch Dolibarr databases.

    Returns:
        dict with bootstrap and migration results
    """
    import structlog
    global logger
    logger = structlog.get_logger()

    url = get_audit_database_url(instance_config, database_url)

    with audit_engine(url) as engine:
        logger.info("Audit schema bootstrap + migrations starting")

        # STEP 1: Bootstrap (create_all) - creates base tables if missing
        bootstrap_created = bootstrap_audit_schema(engine)

        # STEP 2: Run migrations (ALTER TABLE for incremental changes)
        migrations_applied = []
        changes_made = bootstrap_created

        # Migration 1: Add dolibarr invoice columns
        if run_migration_add_dolibarr_invoice_columns(engine):
            migrations_applied.append("add_dolibarr_invoice_columns")
            changes_made = True

        # Migration 2: Verify no duplicate timestamps
        run_migration_verify_no_duplicate_timestamps(engine)

        # STEP 3: Validate schema - FAIL CLOSED if incomplete
        validate_audit_schema(engine)

        logger.info(
            "Audit bootstrap + migrations completed",
            bootstrap_created=bootstrap_created,
            migrations_applied=migrations_applied,
            changes_made=changes_made,
        )

        return {
            "success": True,
            "bootstrap_created": bootstrap_created,
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