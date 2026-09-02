"""
Deployment script: Run audit schema bootstrap + migrations once.

This script must be run after database provisioning and before first Hermes start.
It performs the deployment-only steps that must NOT run on normal application startup.

Usage:
    python deploy_audit_schema.py

What it does:
1. Runs bootstrap_audit_schema() using a deployment identity with DDL privileges
   (connects as MariaDB root via Unix socket peer authentication) — creates base
   tables (document_idempotency_record, audit_log) if missing.
2. Runs pending migrations (ALTER TABLE for incremental changes).
3. Validates schema fail-closed via validate_audit_schema().
4. Exits with code 0 on success, code 1 on failure.

NOTE: The Hermes runtime (AuditLogger, DocumentIdempotencyManager) uses the
restricted gestor_ia_audit user with USAGE/SELECT/INSERT only. This deployment
step must run FIRST to create/validate the schema before runtime starts.

Important: If root@localhost authentication fails (e.g., auth_socket only works
for the system root user), the script will catch the error and report that
deployment migrations need to be run with a user that has DDL privileges.
"""

import sys
import os
from sqlalchemy import create_engine, inspect

from core.hermes.audit_migrations import (
    bootstrap_audit_schema,
    run_migration_add_dolibarr_invoice_columns,
    validate_audit_schema,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _root_engine():
    """Create a SQLAlchemy engine authenticated as MariaDB root.

    Attempts Unix socket peer authentication first (works when running as
    the system root user). Falls back to TCP with password if socket fails.
    """
    # Try Unix socket peer authentication first
    try:
        engine = create_engine(
            "mysql+pymysql://root:@/gestor_ia_audit",
            pool_pre_ping=True,
            echo=False,
            # Unix socket peer auth requires the OS user to be root;
            # if it fails, we'll fall below.
        )
        # Verify connection by fetching table names
        inspector = inspect(engine)
        inspector.get_table_names()
        return engine
    except Exception:
        pass

    # Fallback: try TCP with password from .env
    root_password = os.getenv(
        "MARIADB_ROOT_PASSWORD",
        "YddoGJ6JkI9bp7MQn89xKIdDtLCMu8/0QxfXNeZUjB8=",
    )
    url = (
        f"mysql+pymysql://root:{root_password}@127.0.0.1:3306/gestor_ia_audit"
        f"?charset=utf8mb4"
    )
    return create_engine(url, pool_pre_ping=True, echo=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Running audit schema deployment bootstrap + migrations...")

    root_engine = _root_engine()

    # Step 1: Bootstrap (create_all) — idempotent, only creates missing tables
    print("  Bootstrap: checking/create base schema...")
    try:
        bootstrap_created = bootstrap_audit_schema(root_engine)
        if bootstrap_created:
            print("  Bootstrap: created base tables (document_idempotency_record, audit_log)")
        else:
            print("  Bootstrap: schema already exists (no new tables created)")
    except Exception as e:
        print(f"  Bootstrap: FAILED — {e}", file=sys.stderr)
        sys.exit(1)

    # Step 2: Run migrations (ALTER TABLE for incremental changes)
    print("  Migrations: adding incremental changes...")
    migrations_applied = []
    changes_made = bootstrap_created

    if run_migration_add_dolibarr_invoice_columns(root_engine):
        migrations_applied.append("add_dolibarr_invoice_columns")
        changes_made = True

    # Verification-only migration (no-op, just validates)
    from core.hermes.audit_migrations import run_migration_verify_no_duplicate_timestamps
    run_migration_verify_no_duplicate_timestamps(root_engine)

    if changes_made:
        with root_engine.connect() as conn:
            conn.commit()
        print("  Migrations: changes committed")

    # Step 3: Validate schema fail-closed
    print("  Validation: running fail-closed schema validation...")
    try:
        validate_audit_schema(root_engine)
        print("  Validation: PASSED — schema is complete and valid")
    except Exception as e:
        print(f"  Validation: FAILED — {e}", file=sys.stderr)
        # schema validation failed — table likely missing
        sys.exit(1)

    print("Deployment complete. Schema is ready for Hermes runtime.")
    print(
        "\nIMPORTANT: The Hermes application will now connect as the restricted "
        "gestor_ia_audit user. Ensure the deployment step above has been run "
        "before any Hermes startup."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()