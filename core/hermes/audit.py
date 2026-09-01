"""
Audit Logger - Registro inmutable de eventos de auditoría.

ADAPTADO desde Transvega Animal - integration-api/app/services/audit_logger.py
Modificado para usar MariaDB (misma instancia server) en lugar de PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
from sqlalchemy import JSON, Boolean, Column, DateTime, Index, Integer, String, Text, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

if TYPE_CHECKING:
    from core.hermes.context import CompanyContext
    from core.hermes.instance_config import InstanceConfig

from core.hermes.audit_migrations import AuditSchemaValidationError, run_audit_migrations

logger = structlog.get_logger()

Base = declarative_base()


# =========================================================================
# ACTION CONSTANTS FOR IDENTITY/AUTHORIZATION EVENTS
# =========================================================================


class AuditActions:
    """Standard action names for audit logging."""

    # Identity events
    TELEGRAM_IDENTITY_UNKNOWN = "telegram.identity.unknown"
    TELEGRAM_IDENTITY_DISABLED = "telegram.identity.disabled"
    DOLIBARR_USER_DISABLED = "dolibarr.user.disabled"

    # Authorization events
    AUTHORIZATION_DENIED = "authorization.denied"

    # User management events
    USER_LINKED = "user.linked"
    USER_UNLINKED = "user.unlinked"
    USER_ENABLED = "user.enabled"
    USER_DISABLED = "user.disabled"

    # Critical actions that should never be cleaned up
    CRITICAL_ACTIONS = frozenset(
        [
            "login",
            "logout",
            "permission_change",
            "approval_decision",
            "invoice_approval",
            "supplier_creation",
            "data_export",
            TELEGRAM_IDENTITY_UNKNOWN,
            TELEGRAM_IDENTITY_DISABLED,
            DOLIBARR_USER_DISABLED,
            AUTHORIZATION_DENIED,
            USER_LINKED,
            USER_UNLINKED,
            USER_ENABLED,
            USER_DISABLED,
        ]
    )


# =========================================================================
# DURABLE IDEMPOTENCY RECORDS (Persistente - Sin TTL)
# =========================================================================
#
# Registro permanente de operaciones completadas para idempotencia ERP.
# Almacena de forma duradera:
# - instance_id: Aislamiento multi-empresa
# - document_hash: SHA256 del documento original
# - supplier_tax_id: CIF/NIF del proveedor
# - supplier_invoice_number: Número de factura del proveedor
# - supplier_dolibarr_id: ID del proveedor en Dolibarr
# - invoice_dolibarr_id: ID de la factura en Dolibarr
# - final_state: Estado final (COMPLETED, INVOICE_CREATED, SUPPLIER_CREATED)
# - attachment_uploaded: Si el PDF se subió a Dolibarr
# - created_at / completed_at: Timestamps
#
# Clave de deduplicación compuesta:
# (instance_id, supplier_tax_id, supplier_invoice_number)
# =========================================================================

class DocumentIdempotencyRecord(Base):
    """Registro permanente de idempotencia para operaciones ERP completadas.
    
    Almacena de forma duradera (sin TTL) el registro de operaciones completadas
    para evitar duplicados en Dolibarr. Redis maneja estados transitorios con TTL;
    esto es el almacenamiento duradero de verdad.
    
    Clave de deduplicación compuesta:
    (instance_id, supplier_tax_id, supplier_invoice_number)
    """
    __tablename__ = "document_idempotency_record"

    id = Column(String(36), primary_key=True)  # UUID
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True, index=True)

    # Aislamiento multi-empresa
    instance_id = Column(String(100), nullable=False, index=True)

    # Identificación del documento
    document_hash = Column(String(64), nullable=False, index=True)  # SHA256

    # Datos fiscales del proveedor (clave de deduplicación)
    supplier_tax_id = Column(String(50), nullable=False, index=True)
    supplier_invoice_number = Column(String(100), nullable=False, index=True)

    # IDs en Dolibarr
    supplier_dolibarr_id = Column(Integer, nullable=True, index=True)
    invoice_dolibarr_id = Column(Integer, nullable=True, index=True)
    dolibarr_invoice_ref = Column(String(100), nullable=True, index=True)  # ref from Dolibarr
    dolibarr_invoice_id = Column(Integer, nullable=True, index=True)       # rowid from Dolibarr

    # Estado final del workflow
    final_state = Column(String(50), nullable=False, index=True)  # COMPLETED, INVOICE_CREATED, SUPPLIER_CREATED, ERP_RESULT_UNKNOWN

    # Adjunto
    attachment_uploaded = Column(Boolean, nullable=False, default=False)

    # Metadatos adicionales
    document_filename = Column(String(255), nullable=True)
    document_mime_type = Column(String(100), nullable=True)
    document_size_bytes = Column(Integer, nullable=True)
    correlation_id = Column(String(36), nullable=True, index=True)

    # Índices compuestos para deduplicación eficiente
    __table_args__ = (
        # Clave única de deduplicación: una factura por proveedor+num_factura por instancia
        Index("ux_idempotency_dedup", "instance_id", "supplier_tax_id", "supplier_invoice_number", unique=True),
        Index("ix_idempotency_instance_hash", "instance_id", "document_hash"),
        Index("ix_idempotency_instance_supplier", "instance_id", "supplier_tax_id"),
        Index("ix_idempotency_instance_state", "instance_id", "final_state"),
        Index("ix_idempotency_completed", "completed_at"),
    )


class AuditLog(Base):
    """Tabla de auditoría inmutable (append-only)."""

    __tablename__ = "audit_log"

    id = Column(String(36), primary_key=True)  # UUID
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Request tracking
    request_id = Column(String(36), nullable=False, index=True)
    correlation_id = Column(String(36), nullable=True, index=True)

    # Actor info
    actor_type = Column(String(50), nullable=False)  # telegram_user, api_key, system
    actor_id = Column(String(100), nullable=False, index=True)
    instance_id = Column(String(100), nullable=False, index=True)  # CRÍTICO: aislamiento

    # User identity (for authenticated operations)
    dolibarr_user_id = Column(Integer, nullable=True, index=True)
    telegram_user_id = Column(Integer, nullable=True, index=True)

    # HTTP info
    method = Column(String(10), nullable=True)
    path = Column(String(500), nullable=True)
    query_params = Column(JSON, nullable=True)
    request_body_hash = Column(String(64), nullable=True)  # SHA256

    # Resource info
    resource_type = Column(String(100), nullable=True, index=True)
    resource_id = Column(String(100), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)

    # State changes
    previous_state = Column(JSON, nullable=True)
    new_state = Column(JSON, nullable=True)
    diff = Column(JSON, nullable=True)

    # Response
    status_code = Column(Integer, nullable=True)
    success = Column(Boolean, nullable=False, default=True, index=True)
    error_code = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    error_details = Column(JSON, nullable=True)

    # Performance
    duration_ms = Column(Integer, nullable=True)

    # Idempotency
    idempotency_key = Column(String(200), nullable=True, index=True)
    idempotent = Column(Boolean, nullable=False, default=False)

    # Network
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)

    # Integrity chain
    previous_hash = Column(String(64), nullable=True)
    current_hash = Column(String(64), nullable=True)

    # Índices compuestos para consultas comunes
    __table_args__ = (
        Index("ix_audit_instance_created", "instance_id", "created_at"),
        Index("ix_audit_instance_actor", "instance_id", "actor_type", "actor_id"),
        Index("ix_audit_instance_resource", "instance_id", "resource_type", "resource_id"),
        Index("ix_audit_instance_action", "instance_id", "action"),
        Index("ix_audit_instance_dolibarr_user", "instance_id", "dolibarr_user_id"),
        Index("ix_audit_instance_telegram_user", "instance_id", "telegram_user_id"),
    )


class AuditLogger:
    """
    Logger de auditoría inmutable para MariaDB.

    Características:
    - Append-only (no UPDATE/DELETE en aplicación)
    - Hash chain para integridad
    - Diff automático entre estados
    - Query con filtros por instancia (aislamiento)
    - No bloquea request principal (fire-and-forget opcional)
    """

    def __init__(self, database_url: str, pool_size: int = 5):
        """
        Args:
            database_url: URL MariaDB (ej: mysql://user:pass@host/db)
            pool_size: Tamaño del pool de conexiones
        """
        # Usar pymysql para MariaDB
        engine_url = database_url if database_url.startswith("mysql+pymysql://") else database_url.replace("mysql://", "mysql+pymysql://")
        self.engine = create_engine(
            engine_url,
            pool_size=pool_size,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

        # Run bootstrap + migrations + validation (idempotent, versioned, fail-closed)
        run_audit_migrations(database_url=database_url)

    def _calculate_hash(self, data: dict) -> str:
        """Calcular SHA256 de dict ordenado."""
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()

    def _calculate_diff(self, old: dict | None, new: dict | None) -> dict | None:
        """Calcular diferencia entre dos estados."""
        if not old and not new:
            return None
        if not old:
            return {"added": new, "removed": {}, "changed": {}}
        if not new:
            return {"added": {}, "removed": old, "changed": {}}

        diff = {"added": {}, "removed": {}, "changed": {}}
        all_keys = set(old.keys()) | set(new.keys())

        for key in all_keys:
            old_val = old.get(key)
            new_val = new.get(key)

            if key not in old:
                diff["added"][key] = new_val
            elif key not in new:
                diff["removed"][key] = old_val
            elif old_val != new_val:
                diff["changed"][key] = {"from": old_val, "to": new_val}

        # Si no hay cambios, retornar None
        if not diff["added"] and not diff["removed"] and not diff["changed"]:
            return None

        return diff

    def _get_last_hash(self, session) -> str:
        """Obtener hash del último registro para cadena de integridad."""
        result = session.execute(text("SELECT current_hash FROM audit_log ORDER BY created_at DESC LIMIT 1")).scalar()
        return result or "genesis"

    async def log(
        self,
        *,
        instance_id: str,
        actor_type: str,
        actor_id: str,
        action: str,
        request_id: str | None = None,
        correlation_id: str | None = None,
        method: str | None = None,
        path: str | None = None,
        query_params: dict | None = None,
        request_body: dict | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        previous_state: dict | None = None,
        new_state: dict | None = None,
        status_code: int = 200,
        success: bool = True,
        error_code: str | None = None,
        error_message: str | None = None,
        error_details: dict | None = None,
        duration_ms: float | None = None,
        idempotency_key: str | None = None,
        idempotent: bool = False,
        ip_address: str | None = None,
        user_agent: str | None = None,
        dolibarr_user_id: int | None = None,
        telegram_user_id: int | None = None,
    ) -> str:
        """
        Registrar evento de auditoría.

        Returns:
            audit_id (UUID) del registro creado
        """
        audit_id = str(uuid4())
        request_id = request_id or str(uuid4())

        # Hashes para integridad
        request_body_hash = self._calculate_hash(request_body) if request_body else None

        diff = self._calculate_diff(previous_state, new_state)

        previous_hash = "genesis"
        current_hash = hashlib.sha256(f"{previous_hash}{audit_id}{datetime.utcnow().isoformat()}".encode()).hexdigest()

        # Insertar (sync - para simplicidad; async opcional con aiomysql)
        session = self.Session()
        try:
            # Obtener hash anterior real
            previous_hash = self._get_last_hash(session)
            current_hash = hashlib.sha256(f"{previous_hash}{audit_id}{datetime.utcnow().isoformat()}".encode()).hexdigest()

            stmt = text("""
                INSERT INTO audit_log (
                    id, created_at,
                    request_id, correlation_id,
                    actor_type, actor_id, instance_id,
                    dolibarr_user_id, telegram_user_id,
                    method, path, query_params, request_body_hash,
                    resource_type, resource_id, action,
                    previous_state, new_state, diff,
                    status_code, success,
                    error_code, error_message, error_details,
                    duration_ms, idempotency_key, idempotent,
                    ip_address, user_agent,
                    previous_hash, current_hash
                ) VALUES (
                    :id, NOW(),
                    :request_id, :correlation_id,
                    :actor_type, :actor_id, :instance_id,
                    :dolibarr_user_id, :telegram_user_id,
                    :method, :path, :query_params, :request_body_hash,
                    :resource_type, :resource_id, :action,
                    :previous_state, :new_state, :diff,
                    :status_code, :success,
                    :error_code, :error_message, :error_details,
                    :duration_ms, :idempotency_key, :idempotent,
                    :ip_address, :user_agent,
                    :previous_hash, :current_hash
                )
            """)

            session.execute(
                stmt,
                {
                    "id": audit_id,
                    "request_id": request_id,
                    "correlation_id": correlation_id,
                    "actor_type": actor_type,
                    "actor_id": actor_id,
                    "instance_id": instance_id,
                    "dolibarr_user_id": dolibarr_user_id,
                    "telegram_user_id": telegram_user_id,
                    "method": method,
                    "path": path,
                    "query_params": json.dumps(query_params) if query_params else None,
                    "request_body_hash": request_body_hash,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "action": action,
                    "previous_state": json.dumps(previous_state) if previous_state else None,
                    "new_state": json.dumps(new_state) if new_state else None,
                    "diff": json.dumps(diff) if diff else None,
                    "status_code": status_code,
                    "success": success,
                    "error_code": error_code,
                    "error_message": error_message,
                    "error_details": json.dumps(error_details) if error_details else None,
                    "duration_ms": int(duration_ms) if duration_ms else None,
                    "idempotency_key": idempotency_key,
                    "idempotent": idempotent,
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                    "previous_hash": previous_hash,
                    "current_hash": current_hash,
                },
            )
            session.commit()

            logger.info("audit_logged", audit_id=audit_id, action=action, instance_id=instance_id)
            return audit_id

        except Exception as e:
            session.rollback()
            # No fallar la request principal por error de auditoría
            logger.error(
                "audit_log_failed",
                error=str(e),
                action=action,
                instance_id=instance_id,
            )
            return audit_id
        finally:
            session.close()

    async def log_from_context(
        self,
        ctx: CompanyContext,
        action: str,
        **kwargs,
    ) -> str:
        """Log usando CompanyContext (inyecta instance_id, actor, etc.)."""
        return await self.log(
            instance_id=ctx.instance_id,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action=action,
            request_id=ctx.request_id,
            correlation_id=ctx.correlation_id,
            method=ctx.method,
            path=ctx.endpoint,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            dolibarr_user_id=ctx.dolibarr_user_id,
            telegram_user_id=ctx.telegram_user_id,
            **kwargs,
        )

    # =========================================================================
    # CONSULTAS
    # =========================================================================

    def query_logs(
        self,
        *,
        instance_id: str,  # OBLIGATORIO: aislamiento por instancia
        actor_type: str | None = None,
        actor_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        action: str | None = None,
        success: bool | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Consultar logs con filtros (siempre filtrado por instance_id)."""
        session = self.Session()
        try:
            conditions = ["instance_id = :instance_id"]
            params = {"instance_id": instance_id}

            if actor_type:
                conditions.append("actor_type = :actor_type")
                params["actor_type"] = actor_type
            if actor_id:
                conditions.append("actor_id = :actor_id")
                params["actor_id"] = actor_id
            if resource_type:
                conditions.append("resource_type = :resource_type")
                params["resource_type"] = resource_type
            if resource_id:
                conditions.append("resource_id = :resource_id")
                params["resource_id"] = resource_id
            if action:
                conditions.append("action = :action")
                params["action"] = action
            if success is not None:
                conditions.append("success = :success")
                params["success"] = success
            if start_date:
                conditions.append("created_at >= :start_date")
                params["start_date"] = start_date
            if end_date:
                conditions.append("created_at <= :end_date")
                params["end_date"] = end_date

            where_clause = " AND ".join(conditions)

            query = text(f"""
                SELECT * FROM audit_log
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """)
            params["limit"] = limit
            params["offset"] = offset

            result = session.execute(query, params)
            columns = result.keys()
            return [dict(zip(columns, row, strict=False)) for row in result.fetchall()]
        finally:
            session.close()

    def get_summary(
        self,
        instance_id: str,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict[str, Any]]:
        """Resumen de actividad por día."""
        session = self.Session()
        try:
            query = text("""
                SELECT
                    DATE(created_at) as day,
                    COUNT(*) as total,
                    SUM(success = 1) as successful,
                    SUM(success = 0) as failed,
                    AVG(duration_ms) as avg_duration_ms,
                    COUNT(DISTINCT actor_id) as unique_actors,
                    COUNT(DISTINCT resource_type) as resource_types
                FROM audit_log
                WHERE instance_id = :instance_id
                  AND created_at >= :start_date
                  AND created_at <= :end_date
                GROUP BY DATE(created_at)
                ORDER BY day DESC
            """)
            result = session.execute(
                query,
                {
                    "instance_id": instance_id,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            )
            columns = result.keys()
            return [dict(zip(columns, row, strict=False)) for row in result.fetchall()]
        finally:
            session.close()

    def verify_integrity(self, instance_id: str | None = None) -> dict[str, Any]:
        """Verificar integridad de la cadena de hash."""
        session = self.Session()
        try:
            where = "WHERE instance_id = :instance_id" if instance_id else ""
            params = {"instance_id": instance_id} if instance_id else {}

            query = text(f"""
                SELECT id, created_at, previous_hash, current_hash
                FROM audit_log
                {where}
                ORDER BY created_at ASC
            """)
            result = session.execute(query, params)

            broken = []
            prev_hash = "genesis"

            for row in result:
                expected = hashlib.sha256(f"{prev_hash}{row.id}{row.created_at.isoformat()}".encode()).hexdigest()

                if row.current_hash != expected:
                    broken.append(
                        {
                            "id": row.id,
                            "expected": expected,
                            "actual": row.current_hash,
                            "created_at": row.created_at.isoformat(),
                        }
                    )

                prev_hash = row.current_hash

            return {
                "verified": len(broken) == 0,
                "total_records": result.rowcount,
                "broken_chain": broken,
            }
        finally:
            session.close()

    def cleanup_old_logs(
        self,
        instance_id: str,
        retention_days: int = 90,
    ) -> int:
        """Limpiar logs antiguos (solo exitosos, no acciones críticas)."""
        session = self.Session()
        try:
            # Build NOT IN clause from critical actions
            critical_actions = ", ".join(f"'{a}'" for a in AuditActions.CRITICAL_ACTIONS)
            query = text(f"""
                DELETE FROM audit_log
                WHERE instance_id = :instance_id
                  AND created_at < DATE_SUB(NOW(), INTERVAL :days DAY)
                  AND success = 1
                  AND action NOT IN ({critical_actions})
            """)
            result = session.execute(query, {"instance_id": instance_id, "days": retention_days})
            session.commit()
            deleted = result.rowcount

            if deleted:
                logger.info("audit_cleanup", deleted=deleted, instance_id=instance_id, retention_days=retention_days)

            return deleted
        except Exception as e:
            session.rollback()
            logger.error("audit_cleanup_failed", error=str(e), instance_id=instance_id)
            return 0
        finally:
            session.close()

    def close(self):
        """Cerrar conexiones."""
        self.engine.dispose()


# =========================================================================
# DURABLE IDEMPOTENCY MANAGER (Registro permanente de idempotencia ERP)
# =========================================================================

class DocumentIdempotencyManager:
    """
    Gestor de registros de idempotencia duraderos para operaciones ERP.

    Almacena de forma PERMANENTE (sin TTL) el estado actual de cada operación
    ERP (current durable state). Una fila lógica por operación comercial:
    (instance_id, supplier_tax_id, supplier_invoice_number).

    Los milestones ERP evolucionan esa MISMA fila:
    PENDING_CONFIRMATION -> CONFIRMING -> SUPPLIER_CREATED -> INVOICE_CREATED
    -> ATTACHMENT_PENDING -> COMPLETED

    Redis maneja estados transitorios con TTL; esto es el almacenamiento
    duradero de verdad (current state, no event log).

    Clave de deduplicación: (instance_id, supplier_tax_id, supplier_invoice_number)
    """

    # Estados válidos para final_state
    VALID_STATES = frozenset([
        "PENDING_CONFIRMATION",
        "CONFIRMING",
        "SUPPLIER_CREATED",
        "INVOICE_CREATED",
        "ATTACHMENT_PENDING",
        "COMPLETED",
        "ERP_RESULT_UNKNOWN",   # POST timeout - no sabemos si se creó
        "FAILED_RETRYABLE",     # Error transitorio, se puede reintentar
        "FAILED_FINAL",         # Error permanente, intervención manual
    ])

    # Transiciones válidas: estado_origen -> set(estados_destino_permitidos)
    VALID_TRANSITIONS = {
        "PENDING_CONFIRMATION": {"CONFIRMING"},
        "CONFIRMING": {"SUPPLIER_CREATED", "INVOICE_CREATED", "ERP_RESULT_UNKNOWN", "FAILED_RETRYABLE", "FAILED_FINAL"},
        "SUPPLIER_CREATED": {"INVOICE_CREATED", "ERP_RESULT_UNKNOWN", "FAILED_RETRYABLE", "FAILED_FINAL"},
        "INVOICE_CREATED": {"ATTACHMENT_PENDING", "COMPLETED", "ERP_RESULT_UNKNOWN", "FAILED_RETRYABLE", "FAILED_FINAL"},
        "ATTACHMENT_PENDING": {"COMPLETED", "FAILED_RETRYABLE", "FAILED_FINAL"},
        "ERP_RESULT_UNKNOWN": {"INVOICE_CREATED", "COMPLETED", "FAILED_RETRYABLE", "FAILED_FINAL"},  # Solo tras reconciliación
        "FAILED_RETRYABLE": {"CONFIRMING", "SUPPLIER_CREATED", "INVOICE_CREATED", "ERP_RESULT_UNKNOWN"},
        "FAILED_FINAL": set(),  # Terminal - no transitions allowed
        "COMPLETED": set(),     # Terminal - no transitions allowed
    }

    def __init__(self, database_url: str, pool_size: int = 5):
        """
        Args:
            database_url: URL MariaDB (ej: mysql://user:pass@host/db)
            pool_size: Tamaño del pool de conexiones
        """
        # Usar pymysql para MariaDB
        engine_url = database_url if database_url.startswith("mysql+pymysql://") else database_url.replace("mysql://", "mysql+pymysql://")
        self.engine = create_engine(
            engine_url,
            pool_size=pool_size,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

        # Run bootstrap + migrations + validation (idempotent, versioned, fail-closed)
        run_audit_migrations(database_url=database_url)

    def _validate_transition(self, from_state: str | None, to_state: str) -> None:
        """Validar que la transición de estado es permitida."""
        if from_state is None:
            # Nuevo registro - solo estados iniciales permitidos
            if to_state not in {"PENDING_CONFIRMATION", "CONFIRMING"}:
                raise ValueError(f"Invalid initial state: {to_state}. Must be PENDING_CONFIRMATION or CONFIRMING")
            return

        if from_state == to_state:
            return  # Same state is idempotent

        allowed = self.VALID_TRANSITIONS.get(from_state, set())
        if to_state not in allowed:
            raise ValueError(f"Invalid state transition: {from_state} -> {to_state}. Allowed: {allowed}")

    async def get_or_create_operation(
        self,
        *,
        instance_id: str,
        document_hash: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
        initial_state: str = "PENDING_CONFIRMATION",
        supplier_dolibarr_id: int | None = None,
        document_filename: str | None = None,
        document_mime_type: str | None = None,
        document_size_bytes: int | None = None,
        correlation_id: str | None = None,
    ) -> tuple[DocumentIdempotencyRecord, bool]:
        """
        Obtener operación existente o crear nueva (atomic upsert con recuperación de race).

        Implementa semántica GET_OR_CREATE_ATOMIC:
        - Intentar localizar operación existente con FOR UPDATE
        - Si no existe, crear nueva
        - UNIQUE constraint es la última barrera
        - Si hay race de INSERT (IntegrityError por ux_idempotency_dedup):
            rollback + re-leer la fila creada por el worker ganador
            continuar de forma idempotente (created=False)
        - Otros IntegrityError: FAIL (no esconder errores genuinos)

        Returns:
            (record, created) - record es la operación, created=True si fue creada AHORA por este worker
        """
        # Validar estado inicial antes de entrar en la transacción
        if initial_state not in {"PENDING_CONFIRMATION", "CONFIRMING"}:
            raise ValueError(f"Invalid initial state: {initial_state}")

        session = self.Session()
        try:
            # PASO 1: Intentar obtener existente con FOR UPDATE (bloquea la fila si existe)
            record = session.query(DocumentIdempotencyRecord).filter(
                DocumentIdempotencyRecord.instance_id == instance_id,
                DocumentIdempotencyRecord.supplier_tax_id == supplier_tax_id,
                DocumentIdempotencyRecord.supplier_invoice_number == supplier_invoice_number,
            ).with_for_update().first()

            if record:
                return record, False

            # PASO 2: No existe - intentar crear
            record = DocumentIdempotencyRecord(
                id=str(uuid4()),
                instance_id=instance_id,
                document_hash=document_hash,
                supplier_tax_id=supplier_tax_id,
                supplier_invoice_number=supplier_invoice_number,
                supplier_dolibarr_id=supplier_dolibarr_id,
                final_state=initial_state,
                document_filename=document_filename,
                document_mime_type=document_mime_type,
                document_size_bytes=document_size_bytes,
                correlation_id=correlation_id,
            )
            session.add(record)

            try:
                session.commit()
            except IntegrityError as e:
                # PASO 3: Race condition - otro worker insertó la misma clave única
                # Verificar que es específicamente la constraint de deduplicación comercial
                error_msg = str(e.orig).lower() if e.orig else str(e).lower()
                is_dedup_constraint = (
                    "ux_idempotency_dedup" in error_msg
                    or "duplicate entry" in error_msg
                    and "instance_id" in error_msg
                    and "supplier_tax_id" in error_msg
                    and "supplier_invoice_number" in error_msg
                )

                if not is_dedup_constraint:
                    # NO es la constraint esperada - re-lanzar (FAIL CLOSED)
                    session.rollback()
                    logger.error(
                        "durable_operation_create_integrity_error_not_dedup",
                        error=str(e),
                        instance_id=instance_id,
                        supplier_tax_id=supplier_tax_id,
                        supplier_invoice_number=supplier_invoice_number,
                    )
                    raise

                # ES la constraint de deduplicación - recuperar fila del worker ganador
                session.rollback()

                # Re-leer con FOR UPDATE para obtener la fila que creó el otro worker
                record = session.query(DocumentIdempotencyRecord).filter(
                    DocumentIdempotencyRecord.instance_id == instance_id,
                    DocumentIdempotencyRecord.supplier_tax_id == supplier_tax_id,
                    DocumentIdempotencyRecord.supplier_invoice_number == supplier_invoice_number,
                ).with_for_update().first()

                if not record:
                    # Esto no debería pasar si la constraint se disparó, pero por seguridad
                    session.rollback()
                    raise RuntimeError(
                        f"IntegrityError on dedup constraint but no record found for "
                        f"instance={instance_id}, supplier={supplier_tax_id}, invoice={supplier_invoice_number}"
                    )

                logger.info(
                    "durable_operation_race_recovered",
                    record_id=record.id,
                    instance_id=instance_id,
                    supplier_tax_id=supplier_tax_id,
                    supplier_invoice_number=supplier_invoice_number,
                    winner_state=record.final_state,
                )

                return record, False  # created=False: la creó el otro worker

            logger.info(
                "durable_operation_created",
                record_id=record.id,
                instance_id=instance_id,
                supplier_tax_id=supplier_tax_id,
                supplier_invoice_number=supplier_invoice_number,
                initial_state=initial_state,
            )

            return record, True

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    async def update_milestone(
        self,
        *,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
        new_state: str,
        supplier_dolibarr_id: int | None = None,
        invoice_dolibarr_id: int | None = None,
        dolibarr_invoice_ref: str | None = None,
        dolibarr_invoice_id: int | None = None,
        attachment_uploaded: bool | None = None,
    ) -> DocumentIdempotencyRecord:
        """
        Actualizar milestone de forma atómica (UPDATE con validación de transición).

        Args:
            instance_id: ID de la instancia
            supplier_tax_id: CIF/NIF del proveedor
            supplier_invoice_number: Número de factura del proveedor
            new_state: Nuevo estado (debe ser transición válida)
            supplier_dolibarr_id: ID proveedor en Dolibarr (opcional)
            invoice_dolibarr_id: ID factura en Dolibarr (opcional)
            dolibarr_invoice_ref: ref de Dolibarr (opcional)
            dolibarr_invoice_id: rowid de Dolibarr (opcional)
            attachment_uploaded: Si se subió adjunto (opcional)

        Returns:
            Registro actualizado

        Raises:
            ValueError: Si la transición no es válida
            NoResultFound: Si no existe la operación
        """
        if new_state not in self.VALID_STATES:
            raise ValueError(f"Invalid state: {new_state}. Valid: {self.VALID_STATES}")

        session = self.Session()
        try:
            record = session.query(DocumentIdempotencyRecord).filter(
                DocumentIdempotencyRecord.instance_id == instance_id,
                DocumentIdempotencyRecord.supplier_tax_id == supplier_tax_id,
                DocumentIdempotencyRecord.supplier_invoice_number == supplier_invoice_number,
            ).with_for_update().first()

            if not record:
                from sqlalchemy.orm.exc import NoResultFound
                raise NoResultFound(
                    f"No durable operation found for "
                    f"instance={instance_id}, supplier={supplier_tax_id}, invoice={supplier_invoice_number}"
                )

            # Validar transición
            self._validate_transition(record.final_state, new_state)

            # Actualizar campos
            old_state = record.final_state
            record.final_state = new_state

            if supplier_dolibarr_id is not None:
                record.supplier_dolibarr_id = supplier_dolibarr_id
            if invoice_dolibarr_id is not None:
                record.invoice_dolibarr_id = invoice_dolibarr_id
            if dolibarr_invoice_ref is not None:
                record.dolibarr_invoice_ref = dolibarr_invoice_ref
            if dolibarr_invoice_id is not None:
                record.dolibarr_invoice_id = dolibarr_invoice_id
            if attachment_uploaded is not None:
                record.attachment_uploaded = attachment_uploaded

            # Timestamps
            if new_state in {"COMPLETED", "FAILED_FINAL"}:
                record.completed_at = datetime.utcnow()

            session.commit()

            logger.info(
                "durable_milestone_updated",
                record_id=record.id,
                instance_id=instance_id,
                from_state=old_state,
                to_state=new_state,
                supplier_dolibarr_id=record.supplier_dolibarr_id,
                invoice_dolibarr_id=record.invoice_dolibarr_id,
            )

            return record

        except Exception as e:
            session.rollback()
            logger.error(
                "durable_milestone_update_failed",
                error=str(e),
                instance_id=instance_id,
                supplier_tax_id=supplier_tax_id,
                supplier_invoice_number=supplier_invoice_number,
                new_state=new_state,
            )
            raise
        finally:
            session.close()

    # =========================================================================
    # CONVENIENCE METHODS FOR EACH MILESTONE
    # =========================================================================

    async def mark_pending_confirmation(
        self,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
        *,
        document_hash: str,
        supplier_dolibarr_id: int | None = None,
        document_filename: str | None = None,
        document_mime_type: str | None = None,
        document_size_bytes: int | None = None,
        correlation_id: str | None = None,
    ) -> DocumentIdempotencyRecord:
        """Crear o actualizar a PENDING_CONFIRMATION."""
        record, created = await self.get_or_create_operation(
            instance_id=instance_id,
            document_hash=document_hash,
            supplier_tax_id=supplier_tax_id,
            supplier_invoice_number=supplier_invoice_number,
            initial_state="PENDING_CONFIRMATION",
            supplier_dolibarr_id=supplier_dolibarr_id,
            document_filename=document_filename,
            document_mime_type=document_mime_type,
            document_size_bytes=document_size_bytes,
            correlation_id=correlation_id,
        )
        if not created and record.final_state != "PENDING_CONFIRMATION":
            record = await self.update_milestone(
                instance_id=instance_id,
                supplier_tax_id=supplier_tax_id,
                supplier_invoice_number=supplier_invoice_number,
                new_state="PENDING_CONFIRMATION",
            )
        return record

    async def mark_confirming(
        self,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
    ) -> DocumentIdempotencyRecord:
        """Transicionar a CONFIRMING."""
        return await self.update_milestone(
            instance_id=instance_id,
            supplier_tax_id=supplier_tax_id,
            supplier_invoice_number=supplier_invoice_number,
            new_state="CONFIRMING",
        )

    async def mark_supplier_created(
        self,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
        supplier_dolibarr_id: int,
    ) -> DocumentIdempotencyRecord:
        """Transicionar a SUPPLIER_CREATED."""
        return await self.update_milestone(
            instance_id=instance_id,
            supplier_tax_id=supplier_tax_id,
            supplier_invoice_number=supplier_invoice_number,
            new_state="SUPPLIER_CREATED",
            supplier_dolibarr_id=supplier_dolibarr_id,
        )

    async def mark_invoice_created(
        self,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
        supplier_dolibarr_id: int,
        invoice_dolibarr_id: int,
        dolibarr_invoice_ref: str,
        dolibarr_invoice_id: int,
    ) -> DocumentIdempotencyRecord:
        """Transicionar a INVOICE_CREATED."""
        return await self.update_milestone(
            instance_id=instance_id,
            supplier_tax_id=supplier_tax_id,
            supplier_invoice_number=supplier_invoice_number,
            new_state="INVOICE_CREATED",
            supplier_dolibarr_id=supplier_dolibarr_id,
            invoice_dolibarr_id=invoice_dolibarr_id,
            dolibarr_invoice_ref=dolibarr_invoice_ref,
            dolibarr_invoice_id=dolibarr_invoice_id,
        )

    async def mark_attachment_pending(
        self,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
    ) -> DocumentIdempotencyRecord:
        """Transicionar a ATTACHMENT_PENDING."""
        return await self.update_milestone(
            instance_id=instance_id,
            supplier_tax_id=supplier_tax_id,
            supplier_invoice_number=supplier_invoice_number,
            new_state="ATTACHMENT_PENDING",
        )

    async def mark_attachment_uploaded(
        self,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
    ) -> DocumentIdempotencyRecord:
        """Transicionar a COMPLETED con attachment_uploaded=True."""
        return await self.update_milestone(
            instance_id=instance_id,
            supplier_tax_id=supplier_tax_id,
            supplier_invoice_number=supplier_invoice_number,
            new_state="COMPLETED",
            attachment_uploaded=True,
        )

    async def mark_completed(
        self,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
    ) -> DocumentIdempotencyRecord:
        """Transicionar a COMPLETED."""
        return await self.update_milestone(
            instance_id=instance_id,
            supplier_tax_id=supplier_tax_id,
            supplier_invoice_number=supplier_invoice_number,
            new_state="COMPLETED",
            attachment_uploaded=True,
        )

    async def mark_erp_result_unknown(
        self,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
    ) -> DocumentIdempotencyRecord:
        """
        Marcar como ERP_RESULT_UNKNOWN (timeout en POST, no sabemos resultado).

        ESTADO CRÍTICO: Prohíbe reintentar CREATE hasta reconciliación.
        """
        return await self.update_milestone(
            instance_id=instance_id,
            supplier_tax_id=supplier_tax_id,
            supplier_invoice_number=supplier_invoice_number,
            new_state="ERP_RESULT_UNKNOWN",
        )

    async def mark_failed_retryable(
        self,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
    ) -> DocumentIdempotencyRecord:
        """Transicionar a FAILED_RETRYABLE (error transitorio)."""
        return await self.update_milestone(
            instance_id=instance_id,
            supplier_tax_id=supplier_tax_id,
            supplier_invoice_number=supplier_invoice_number,
            new_state="FAILED_RETRYABLE",
        )

    async def mark_failed_final(
        self,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
    ) -> DocumentIdempotencyRecord:
        """Transicionar a FAILED_FINAL (error permanente)."""
        return await self.update_milestone(
            instance_id=instance_id,
            supplier_tax_id=supplier_tax_id,
            supplier_invoice_number=supplier_invoice_number,
            new_state="FAILED_FINAL",
        )

    # =========================================================================
    # QUERY METHODS
    # =========================================================================

    async def get_operation(
        self,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
    ) -> DocumentIdempotencyRecord | None:
        """Obtener operación por clave comercial."""
        session = self.Session()
        try:
            return session.query(DocumentIdempotencyRecord).filter(
                DocumentIdempotencyRecord.instance_id == instance_id,
                DocumentIdempotencyRecord.supplier_tax_id == supplier_tax_id,
                DocumentIdempotencyRecord.supplier_invoice_number == supplier_invoice_number,
            ).first()
        finally:
            session.close()

    async def get_by_document_hash(
        self,
        instance_id: str,
        document_hash: str,
    ) -> DocumentIdempotencyRecord | None:
        """Buscar registro por hash de documento."""
        session = self.Session()
        try:
            return session.query(DocumentIdempotencyRecord).filter(
                DocumentIdempotencyRecord.instance_id == instance_id,
                DocumentIdempotencyRecord.document_hash == document_hash,
            ).first()
        finally:
            session.close()

    async def check_duplicate(
        self,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
    ) -> DocumentIdempotencyRecord | None:
        """
        Verificar si ya existe un registro completado para esta factura.

        Returns:
            DocumentIdempotencyRecord si existe en estado terminal o avanzado, None si no
        """
        session = self.Session()
        try:
            result = session.query(DocumentIdempotencyRecord).filter(
                DocumentIdempotencyRecord.instance_id == instance_id,
                DocumentIdempotencyRecord.supplier_tax_id == supplier_tax_id,
                DocumentIdempotencyRecord.supplier_invoice_number == supplier_invoice_number,
                DocumentIdempotencyRecord.final_state.in_([
                    "COMPLETED", "INVOICE_CREATED", "SUPPLIER_CREATED",
                    "ATTACHMENT_PENDING", "ERP_RESULT_UNKNOWN",
                ]),
            ).first()

            if result:
                logger.info(
                    "duplicate_detected_durable",
                    instance_id=instance_id,
                    supplier_tax_id=supplier_tax_id,
                    supplier_invoice_number=supplier_invoice_number,
                    existing_id=result.id,
                    final_state=result.final_state,
                )

            return result
        finally:
            session.close()

    async def get_state(
        self,
        instance_id: str,
        supplier_tax_id: str,
        supplier_invoice_number: str,
    ) -> str | None:
        """
        Obtener el estado actual de una operación durable.

        Returns the final_state of the operation, or None if not found.
        Useful for checking the current milestone before proceeding.

        Args:
            instance_id: ID de la instancia
            supplier_tax_id: CIF/NIF del proveedor
            supplier_invoice_number: Número de factura del proveedor

        Returns:
            final_state string (e.g. "SUPPLIER_CREATED", "INVOICE_CREATED", etc.)
            or None if the operation doesn't exist
        """
        session = self.Session()
        try:
            record = session.query(DocumentIdempotencyRecord).filter(
                DocumentIdempotencyRecord.instance_id == instance_id,
                DocumentIdempotencyRecord.supplier_tax_id == supplier_tax_id,
                DocumentIdempotencyRecord.supplier_invoice_number == supplier_invoice_number,
            ).first()
            if record:
                return record.final_state
            return None
        finally:
            session.close()

    def close(self):
        """Cerrar conexiones."""
        self.engine.dispose()


# =========================================================================
# FACTORY
# =========================================================================


def create_audit_logger(instance_config: InstanceConfig | None = None, database_url: str | None = None) -> AuditLogger:
    """Crear AuditLogger para una instancia o global."""
    if database_url:
        return AuditLogger(database_url)

    if instance_config:
        # Usar BD de auditoría global (no la BD de Dolibarr de la instancia)
        from core.hermes.config import get_global_settings

        settings = get_global_settings()
        return AuditLogger(
            f"mysql+pymysql://gestor_ia_audit:{settings.MARIADB_AUDIT_PASSWORD}@{settings.MARIADB_HOST}:{settings.MARIADB_PORT}/gestor_ia_audit"
        )

    # Fallback: global audit DB (separada)
    from core.hermes.config import get_global_settings

    settings = get_global_settings()
    return AuditLogger(
        f"mysql+pymysql://gestor_ia_audit:{settings.MARIADB_AUDIT_PASSWORD}@{settings.MARIADB_HOST}:{settings.MARIADB_PORT}/gestor_ia_audit"
    )


def create_document_idempotency_manager(instance_config: InstanceConfig | None = None, database_url: str | None = None) -> DocumentIdempotencyManager:
    """Crear DocumentIdempotencyManager para una instancia o global.
    
    IMPORTANTE: Siempre usa la BD de auditoría (gestor_ia_audit), NO la BD de Dolibarr de la instancia.
    """
    if database_url:
        return DocumentIdempotencyManager(database_url)

    if instance_config:
        # Usar BD de auditoría global (no la BD de Dolibarr de la instancia)
        from core.hermes.config import get_global_settings

        settings = get_global_settings()
        return DocumentIdempotencyManager(
            f"mysql+pymysql://gestor_ia_audit:{settings.MARIADB_AUDIT_PASSWORD}@{settings.MARIADB_HOST}:{settings.MARIADB_PORT}/gestor_ia_audit"
        )

    # Fallback: global audit DB (separada)
    from core.hermes.config import get_global_settings

    settings = get_global_settings()
    return DocumentIdempotencyManager(
        f"mysql+pymysql://gestor_ia_audit:{settings.MARIADB_AUDIT_PASSWORD}@{settings.MARIADB_HOST}:{settings.MARIADB_PORT}/gestor_ia_audit"
    )