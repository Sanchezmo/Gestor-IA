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
from sqlalchemy.orm import declarative_base, sessionmaker

if TYPE_CHECKING:
    from core.hermes.context import CompanyContext
    from core.hermes.instance_config import InstanceConfig

logger = structlog.get_logger()

Base = declarative_base()


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
        engine_url = database_url.replace("mysql://", "mysql+pymysql://")
        self.engine = create_engine(
            engine_url,
            pool_size=pool_size,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False,
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

        # Crear tabla si no existe
        Base.metadata.create_all(self.engine)

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
            current_hash = hashlib.sha256(
                f"{previous_hash}{audit_id}{datetime.utcnow().isoformat()}".encode()
            ).hexdigest()

            stmt = text("""
                INSERT INTO audit_log (
                    id, created_at,
                    request_id, correlation_id,
                    actor_type, actor_id, instance_id,
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
            query = text("""
                DELETE FROM audit_log
                WHERE instance_id = :instance_id
                  AND created_at < DATE_SUB(NOW(), INTERVAL :days DAY)
                  AND success = 1
                  AND action NOT IN (
                      'login', 'logout', 'permission_change',
                      'approval_decision', 'invoice_approval',
                      'supplier_creation', 'data_export'
                  )
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
# FACTORY
# =========================================================================


def create_audit_logger(
    instance_config: InstanceConfig | None = None, database_url: str | None = None
) -> AuditLogger:
    """Crear AuditLogger para una instancia o global."""
    if database_url:
        return AuditLogger(database_url)

    if instance_config:
        return AuditLogger(instance_config.get_dolibarr_db_url().replace("mysql://", "mysql+pymysql://"))

    # Fallback: global audit DB (separada)
    from core.hermes.config import get_global_settings

    settings = get_global_settings()
    return AuditLogger(
        f"mysql+pymysql://root:{settings.MARIADB_ROOT_PASSWORD}@{settings.MARIADB_HOST}:{settings.MARIADB_PORT}/gestor_ia_audit"
    )
