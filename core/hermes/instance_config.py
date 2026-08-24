"""
Modelos de configuración por instancia (empresa).
Cada empresa tiene su InstanceConfig independiente.
"""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class AIProviderType(StrEnum):
    """Proveedores de IA soportados."""

    OLLAMA = "ollama"
    NVIDIA = "nvidia"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class AIPolicyScope(StrEnum):
    """Ámbito de privacidad para routing de IA."""

    LOCAL_ONLY = "LOCAL_ONLY"  # Nunca sale del servidor (facturas, datos sensibles)
    CLOUD_ALLOWED = "CLOUD_ALLOWED"  # Puede usar proveedores cloud (tareas públicas)


class DatabaseConfig(BaseModel):
    """Configuración de base de datos MariaDB para esta instancia."""

    host: str = "127.0.0.1"
    port: int = 3306
    name: str  # ej: dolibarr_empresa_a
    user: str  # ej: db_empresa_a
    password: str  # Generar único por instancia


class DolibarrConfig(BaseModel):
    """Configuración de Dolibarr (ERP) para esta instancia.

    Contiene SOLO información relacionada con la instancia ERP.
    La configuración de base de datos está en DatabaseConfig por separado.
    """

    version: str  # ej: "23.0.4" - requerido, no hay default en Core
    internal_url: str  # ej: http://127.0.0.1:8081
    public_url: str | None = None  # ej: https://dolibarr.empresa.com
    api_key: str
    documents_path: str  # ej: /var/lib/dolibarr/documents/empresa_a
    currency: str = "EUR"  # Moneda de la instancia (ej: EUR, USD, MXN)


class TelegramConfig(BaseModel):
    """Configuración de Telegram Bot para esta instancia."""

    bot_token: str
    webhook_path: str  # ej: /webhook/empresa_a
    webhook_secret: str  # Generar: openssl rand -hex 32
    webhook_secret_required: bool = True
    allowed_user_ids: list[int] = Field(default_factory=list)
    max_file_size_mb: int = 10
    update_idempotency_ttl_hours: int = 24


class DomainConfig(BaseModel):
    """Configuración de dominios/hostnames para esta instancia."""

    base: str  # ej: empresa.com
    dolibarr: str | None = None  # ej: dolibarr.empresa.com
    hermes: str | None = None  # ej: bot.empresa.com
    custom: dict[str, str] = Field(default_factory=dict)  # Otros hostnames

    @field_validator("base")
    @classmethod
    def validate_base_domain(cls, v: str) -> str:
        if not v or "." not in v:
            raise ValueError("Dominio base debe ser válido (ej: empresa.com)")
        return v.lower()


class AIConfig(BaseModel):
    """Configuración de IA para esta instancia.

    NOTA: Los modelos concretos NO tienen defaults en el Core.
    Deben configurarse explícitamente por instancia o en templates.
    """

    default_policy: AIPolicyScope = AIPolicyScope.LOCAL_ONLY

    # Ollama (local - compartido)
    ollama_endpoint: str = "http://127.0.0.1:11434"
    ollama_model: str  # Requerido - ej: "qwen3.5:4b", "llama3.1:8b", etc.
    ollama_vision_model: str | None = None

    # NVIDIA (cloud)
    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_text_model: str | None = None
    nvidia_vision_model: str | None = None

    # OpenAI (cloud)
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_text_model: str | None = None
    openai_vision_model: str | None = None

    # Configuración de routing por tarea
    task_policies: dict[str, AIPolicyScope] = Field(default_factory=dict)
    # ej: {"invoice_processing": "LOCAL_ONLY", "content_generation": "CLOUD_ALLOWED"}


def validate_instance_id(v: str) -> str:
    """Validate instance_id format and reserved names."""
    v = v.lower().strip()
    if not v:
        raise ValueError("instance_id no puede estar vacío")
    # Solo alphanumérico, guiones y underscores
    import re

    if not re.match(r"^[a-z0-9_-]+$", v):
        raise ValueError("instance_id solo puede contener letras minúsculas, números, guiones y underscores")
    if v in ("global", "shared", "core", "instances", "companies", "scripts", "tests", "config", "infrastructure"):
        raise ValueError(f"instance_id '{v}' está reservado")
    return v


class InstanceConfig(BaseModel):
    """
    Configuración completa de una instancia (empresa).

    Este modelo es la fuente de verdad para todo lo específico de una empresa.
    Se carga desde instances/{instance_id}/config.yml
    """

    instance_id: str  # slug único: "empresa_a", "transvega", "ejemplo"
    company_name: str  # Nombre legal: "Empresa A S.L."

    # Infraestructura específica - SEPARADA SEMÁNTICAMENTE
    database: DatabaseConfig  # Configuración MariaDB (host, port, name, user, password)
    dolibarr: DolibarrConfig  # Configuración Dolibarr ERP (urls, api_key, version, documents_path)
    telegram: TelegramConfig
    domains: DomainConfig
    ai: AIConfig = Field(default_factory=AIConfig)

    # Extensiones habilitadas (plugins/agents/tools/workflows)
    enabled_agents: list[str] = Field(default_factory=list)
    enabled_workflows: list[str] = Field(default_factory=list)
    enabled_tools: list[str] = Field(default_factory=list)

    # Referencias a secretos (NO valores reales - gitignored)
    # Formato: {"nombre_secreto": "vault:path/o/env:VAR_NAME"}
    secrets_refs: dict[str, str] = Field(default_factory=dict)

    # Rutas de datos (runtime, no versionadas)
    documents_path: str = "/var/lib/gestor-ia/{instance_id}/documents"
    backups_path: str = "/var/backups/gestor-ia/{instance_id}"
    runtime_path: str = "/var/lib/gestor-ia/{instance_id}/runtime"

    # Configuración Dolibarr Apache (puerto único por instancia)
    dolibarr_apache_port: int = 8081  # Se asigna dinámicamente

    # Metadata
    created_at: str = ""  # ISO format
    updated_at: str = ""  # ISO format
    active: bool = True

    @field_validator("instance_id")
    @classmethod
    def _validate_instance_id(cls, v: str) -> str:
        return validate_instance_id(v)

    def resolve_paths(self) -> "InstanceConfig":
        """Resolver placeholders en paths con instance_id."""
        resolved = self.model_copy()
        resolved.documents_path = self.documents_path.format(instance_id=self.instance_id)
        resolved.backups_path = self.backups_path.format(instance_id=self.instance_id)
        resolved.runtime_path = self.runtime_path.format(instance_id=self.instance_id)
        return resolved

    def get_database_url(self) -> str:
        """URL de conexión MariaDB para esta instancia."""
        db = self.database
        return f"mysql://{db.user}:{db.password}@{db.host}:{db.port}/{db.name}"

    def get_dolibarr_db_url(self) -> str:
        """URL de conexión MariaDB para Dolibarr de esta instancia (alias compatibilidad)."""
        return self.get_database_url()

    def get_redis_db(self) -> int:
        """Número de base de datos Redis para esta instancia (hash del instance_id).

        NOTA: Redis DB number (0-15) NO es una frontera de seguridad real.
        Es solo aislamiento lógico de claves. Para aislamiento real usar:
        - namespace/prefix por instancia
        - credenciales/ACL cuando proceda
        - o instancias Redis separadas cuando la sensibilidad lo requiera.
        """
        import hashlib

        return int(hashlib.md5(self.instance_id.encode()).hexdigest(), 16) % 16

    def get_redis_url(self, global_redis_url: str) -> str:
        """URL Redis con DB específica para esta instancia."""
        db_num = self.get_redis_db()
        if "redis://" in global_redis_url:
            base = global_redis_url.split("/")[0] + "//" + global_redis_url.split("//")[1].split("/")[0]
            # Manejar password en URL
            if "@" in global_redis_url:
                # redis://:pass@host:port/db
                parts = global_redis_url.split("@")
                return f"{parts[0]}@{parts[1].split('/')[0]}/{db_num}"
            else:
                # redis://host:port/db
                return f"{base}/{db_num}"
        return global_redis_url


# =========================================================================
# CARGADOR DE CONFIGURACIÓN
# =========================================================================

import threading

import yaml

from core.hermes.utils import get_instances_root

_config_cache: dict[str, InstanceConfig] = {}
_cache_lock = threading.Lock()


def load_instance_config(instance_id: str, instances_root: Path | None = None) -> InstanceConfig | None:
    """
    Cargar InstanceConfig desde archivo YAML.

    Args:
        instance_id: ID de la instancia (slug)
        instances_root: Directorio raíz de instancias (default: PROJECT_ROOT/instances)

    Returns:
        InstanceConfig o None si no existe
    """
    global _config_cache

    with _cache_lock:
        if instance_id in _config_cache:
            return _config_cache[instance_id]

    if instances_root is None:
        instances_root = get_instances_root()

    config_path = instances_root / instance_id / "config.yml"
    if not config_path.exists():
        return None

    with config_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data:
        return None

    config = InstanceConfig(**data).resolve_paths()

    with _cache_lock:
        _config_cache[instance_id] = config

    return config


def list_instances(instances_root: Path | None = None) -> list[str]:
    """Listar IDs de instancias disponibles."""
    if instances_root is None:
        instances_root = get_instances_root()

    if not instances_root.exists():
        return []

    return sorted([d.name for d in instances_root.iterdir() if d.is_dir() and (d / "config.yml").exists()])


def clear_config_cache():
    """Limpiar cache de configuraciones (útil para tests)."""
    global _config_cache
    with _cache_lock:
        _config_cache.clear()


# =========================================================================
# TEMPLATE PARA NUEVA INSTANCIA
# =========================================================================

INSTANCE_CONFIG_TEMPLATE = """# InstanceConfig para {company_name}
# Generado automáticamente - Editar valores reales
# NO commitear secretos reales - usar secrets_refs o instance.env

instance_id: "{instance_id}"
company_name: "{company_name}"

database:
  host: "127.0.0.1"
  port: 3306
  name: "dolibarr_{instance_id}"
  user: "db_{instance_id}"
  password: "CHANGE_ME_DB_PASSWORD"

dolibarr:
  version: "23.0.4"
  internal_url: "http://127.0.0.1:{dolibarr_port}"
  public_url: "https://{dolibarr_domain}"
  api_key: "CHANGE_ME_DOLIBARR_API_KEY"
  documents_path: "/var/lib/dolibarr/documents/{instance_id}"

telegram:
  bot_token: "CHANGE_ME_TELEGRAM_BOT_TOKEN"
  webhook_path: "/webhook/{instance_id}"
  webhook_secret: "CHANGE_ME_WEBHOOK_SECRET"
  webhook_secret_required: true
  allowed_user_ids: []
  max_file_size_mb: 10
  update_idempotency_ttl_hours: 24

domains:
  base: "{base_domain}"
  dolibarr: "{dolibarr_domain}"
  hermes: "{hermes_domain}"
  custom: {{}}

ai:
  default_policy: "LOCAL_ONLY"
  ollama_endpoint: "http://127.0.0.1:11434"
  ollama_model: "qwen3.5:4b"  # EJEMPLO - cambiar según modelo disponible
  ollama_vision_model: null
  nvidia_api_key: null
  nvidia_base_url: "https://integrate.api.nvidia.com/v1"
  nvidia_text_model: null
  nvidia_vision_model: null
  openai_api_key: null
  openai_base_url: "https://api.openai.com/v1"
  openai_text_model: null
  openai_vision_model: null
  task_policies: {{}}

enabled_agents: []
enabled_workflows: []
enabled_tools: []

secrets_refs:
  dolibarr_db_password: "env:DOLIBARR_DB_PASSWORD_{instance_id_upper}"
  dolibarr_api_key: "env:DOLIBARR_API_KEY_{instance_id_upper}"
  telegram_bot_token: "env:TELEGRAM_BOT_TOKEN_{instance_id_upper}"
  telegram_webhook_secret: "env:TELEGRAM_WEBHOOK_SECRET_{instance_id_upper}"

documents_path: "/var/lib/gestor-ia/{instance_id}/documents"
backups_path: "/var/backups/gestor-ia/{instance_id}"
runtime_path: "/var/lib/gestor-ia/{instance_id}/runtime"
dolibarr_apache_port: {dolibarr_port}

created_at: ""
updated_at: ""
active: true
"""


def generate_instance_template(
    instance_id: str,
    company_name: str,
    base_domain: str,
    dolibarr_port: int = 8081,
) -> str:
    """Generar template de config.yml para nueva instancia."""
    instance_id_upper = instance_id.upper().replace("-", "_")
    dolibarr_domain = f"dolibarr.{base_domain}"
    hermes_domain = f"bot.{base_domain}"

    return INSTANCE_CONFIG_TEMPLATE.format(
        instance_id=instance_id,
        instance_id_upper=instance_id_upper,
        company_name=company_name,
        base_domain=base_domain,
        dolibarr_domain=dolibarr_domain,
        hermes_domain=hermes_domain,
        dolibarr_port=dolibarr_port,
    )
