"""
CompanyContext - Contexto inmutable de empresa para una operación.

Principio crítico: NO usar variables globales mutables para cambiar de empresa.
Cada request resuelve su CompanyContext y lo propaga explícitamente.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.hermes.instance_config import InstanceConfig

if TYPE_CHECKING:
    from core.hermes.instance_config import AIPolicyScope
    from core.integrations.dolibarr.client import DolibarrClient
    from core.integrations.telegram.client import TelegramClient


@dataclass(frozen=True, slots=True)
class CompanyContext:
    """
    Contexto inmutable de empresa para una operación empresarial.

    Se crea por request (middleware) y se inyecta como dependency.
    Inmutable: frozen=True, slots=True para performance y seguridad.

    Lleva TODA la información necesaria para operar en nombre de una empresa:
    - InstanceConfig completa (DB, Telegram, Domains, AI, Extensions)
    - Actor info (quién hace la request)
    - Trazabilidad (request_id, correlation_id)
    """

    instance_config: InstanceConfig
    actor_type: str  # "telegram_user", "api_key", "system", "webhook", "scheduled"
    actor_id: str  # user_id, api_key_id, "system", "cron", etc.
    request_id: str = field(default_factory=lambda: str(uuid4()))
    correlation_id: str | None = None
    timestamp: float = field(default_factory=time.time)

    # Metadata adicional para auditoría
    ip_address: str | None = None
    user_agent: str | None = None
    endpoint: str | None = None
    method: str | None = None

    # =========================================================================
    # ACCESO CONVENIENTE A CONFIG FRECUENTE
    # =========================================================================

    @property
    def instance_id(self) -> str:
        return self.instance_config.instance_id

    @property
    def company_name(self) -> str:
        return self.instance_config.company_name

    @property
    def database_config(self):
        """Configuración de base de datos (MariaDB)."""
        return self.instance_config.database

    @property
    def dolibarr_config(self):
        """Configuración de Dolibarr ERP."""
        return self.instance_config.dolibarr

    @property
    def telegram_config(self):
        return self.instance_config.telegram

    @property
    def domains_config(self):
        return self.instance_config.domains

    @property
    def ai_config(self):
        return self.instance_config.ai

    @property
    def enabled_agents(self) -> list[str]:
        return self.instance_config.enabled_agents

    @property
    def enabled_workflows(self) -> list[str]:
        return self.instance_config.enabled_workflows

    @property
    def enabled_tools(self) -> list[str]:
        return self.instance_config.enabled_tools

    # =========================================================================
    # MÉTODOS DE CONVENIENCIA
    # =========================================================================

    def create_dolibarr_client(self) -> DolibarrClient:
        """Crear cliente Dolibarr configurado para esta instancia."""
        from core.integrations.dolibarr.client import DolibarrClient

        db = self.instance_config.dolibarr
        return DolibarrClient(
            base_url=db.internal_url,
            api_key=db.api_key,
            timeout=30,
        )

    def create_telegram_client(self) -> TelegramClient:
        """Crear cliente Telegram configurado para esta instancia."""
        from core.integrations.telegram.client import TelegramClient

        return TelegramClient(bot_token=self.instance_config.telegram.bot_token)

    def get_ai_policy_for_task(self, task: str) -> AIPolicyScope:
        """Obtener política de IA para una tarea específica."""
        return self.instance_config.ai.task_policies.get(task, self.instance_config.ai.default_policy)

    def is_agent_enabled(self, agent_name: str) -> bool:
        """Verificar si un agente está habilitado para esta instancia."""
        return agent_name in self.enabled_agents

    def is_workflow_enabled(self, workflow_name: str) -> bool:
        """Verificar si un workflow está habilitado para esta instancia."""
        return workflow_name in self.enabled_workflows

    def is_tool_enabled(self, tool_name: str) -> bool:
        """Verificar si una herramienta está habilitada para esta instancia."""
        return tool_name in self.enabled_tools

    def to_audit_dict(self) -> dict[str, Any]:
        """Convertir a dict para logging de auditoría (sin secretos)."""
        return {
            "instance_id": self.instance_id,
            "company_name": self.company_name,
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "endpoint": self.endpoint,
            "method": self.method,
        }


# =========================================================================
# BUILDER PARA CREAR CONTEXTO DESDE REQUEST
# =========================================================================


class CompanyContextBuilder:
    """
    Builder para crear CompanyContext desde diferentes fuentes.

    Usado por middleware/resolver para construir el contexto
    antes de inyectarlo en handlers.
    """

    def __init__(self, instance_config: InstanceConfig):
        self._instance_config = instance_config
        self._actor_type = "unknown"
        self._actor_id = "unknown"
        self._request_id = str(uuid4())
        self._correlation_id: str | None = None
        self._ip_address: str | None = None
        self._user_agent: str | None = None
        self._endpoint: str | None = None
        self._method: str | None = None

    def with_actor(self, actor_type: str, actor_id: str) -> CompanyContextBuilder:
        self._actor_type = actor_type
        self._actor_id = str(actor_id)
        return self

    def with_request_id(self, request_id: str) -> CompanyContextBuilder:
        self._request_id = request_id
        return self

    def with_correlation_id(self, correlation_id: str) -> CompanyContextBuilder:
        self._correlation_id = correlation_id
        return self

    def with_http_info(
        self,
        ip: str | None = None,
        user_agent: str | None = None,
        endpoint: str | None = None,
        method: str | None = None,
    ) -> CompanyContextBuilder:
        self._ip_address = ip
        self._user_agent = user_agent
        self._endpoint = endpoint
        self._method = method
        return self

    def build(self) -> CompanyContext:
        return CompanyContext(
            instance_config=self._instance_config,
            actor_type=self._actor_type,
            actor_id=self._actor_id,
            request_id=self._request_id,
            correlation_id=self._correlation_id,
            ip_address=self._ip_address,
            user_agent=self._user_agent,
            endpoint=self._endpoint,
            method=self._method,
        )


# =========================================================================
# HELPERS PARA EXTRACCIÓN DE ACTOR
# =========================================================================


def extract_telegram_actor(update: dict) -> tuple[str, str]:
    """Extraer (actor_type, actor_id) de un update de Telegram."""
    # Message
    message = update.get("message") or update.get("edited_message")
    if message:
        user = message.get("from", {})
        return "telegram_user", str(user.get("id", "unknown"))

    # Callback query
    callback = update.get("callback_query")
    if callback:
        user = callback.get("from", {})
        return "telegram_user", str(user.get("id", "unknown"))

    return "telegram_webhook", "unknown"


def extract_api_key_actor(api_key: str) -> tuple[str, str]:
    """Extraer actor desde API key (formato: gsk_{instance}_{key})."""
    if api_key.startswith("gsk_"):
        parts = api_key.split("_", 2)
        if len(parts) >= 3:
            return "api_key", parts[2]
    return "api_key", "unknown"


def extract_system_actor() -> tuple[str, str]:
    """Actor para tareas del sistema (cron, workers, etc.)."""
    return "system", "system"
