"""
Configuración global de infraestructura compartida (NO por empresa).
"""

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GlobalSettings(BaseSettings):
    """
    Configuración de infraestructura compartida del servidor Gestor-IA.
    
    NO contiene configuración específica de empresas.
    Cada empresa tiene su InstanceConfig independiente.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # =========================================================================
    # ENTORNO GENERAL
    # =========================================================================
    ENVIRONMENT: str = "development"  # development | staging | production
    PROJECT_ROOT: Path = Field(default_factory=lambda: Path.cwd())
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # =========================================================================
    # MARIADB (Servidor compartido - Múltiples DBs por empresa)
    # =========================================================================
    MARIADB_HOST: str = "127.0.0.1"
    MARIADB_PORT: int = 3306
    MARIADB_ROOT_PASSWORD: str  # Generar: openssl rand -base64 32

    # =========================================================================
    # REDIS (Compartido - DBs separadas por instancia)
    # =========================================================================
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""  # Generar: openssl rand -base64 32
    REDIS_MAXMEMORY: str = "512mb"
    REDIS_MAXMEMORY_POLICY: str = "allkeys-lru"

    @property
    def redis_url(self) -> str:
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/0"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    # =========================================================================
    # IA COMPARTIDA (GPU/Ollama - Una instancia para todas las empresas)
    # =========================================================================
    OLLAMA_HOST: str = "127.0.0.1"
    OLLAMA_PORT: int = 11434
    OLLAMA_DEFAULT_MODEL: str = "qwen3.5:4b"

    @property
    def ollama_endpoint(self) -> str:
        return f"http://{self.OLLAMA_HOST}:{self.OLLAMA_PORT}"

    # =========================================================================
    # CLOUDFLARE (Infraestructura compartida - Tunnel + DNS + Access)
    # =========================================================================
    CLOUDFLARE_API_TOKEN: Optional[str] = None
    CLOUDFLARE_ACCOUNT_ID: Optional[str] = None
    CLOUDFLARE_ZONE_ID: Optional[str] = None
    CLOUDFLARE_TUNNEL_TOKEN: Optional[str] = None
    CLOUDFLARE_TUNNEL_ID: Optional[str] = None

    # =========================================================================
    # APACHE (Proxy reverso local para Dolibarr por instancia)
    # =========================================================================
    APACHE_PORT: int = 8080
    APACHE_DOLIBARR_BASE_PORT: int = 8080  # Cada instancia: base_port + offset

    # =========================================================================
    # SEGURIDAD GLOBAL
    # =========================================================================
    JWT_SECRET_KEY: str  # Generar: openssl rand -base64 64
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 30
    JWT_REFRESH_EXPIRATION_DAYS: int = 7

    FERNET_KEY: str  # Generar: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    # =========================================================================
    # RATE LIMITING GLOBAL
    # =========================================================================
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # =========================================================================
    # IDEMPOTENCIA GLOBAL
    # =========================================================================
    IDEMPOTENCY_TTL_HOURS: int = 24

    # =========================================================================
    # BACKUPS
    # =========================================================================
    BACKUP_SCHEDULE: str = "0 2 * * *"  # Diario 02:00
    BACKUP_RETENTION_DAYS: int = 30
    BACKUP_ENCRYPTION_KEY: str = ""  # Generar: openssl rand -base64 32
    BACKUP_LOCAL_PATH: str = "/var/backups/gestor-ia"

    # =========================================================================
    # MONITORIZACIÓN (Opcional)
    # =========================================================================
    PROMETHEUS_PORT: int = 9090
    METRICS_ENABLED: bool = True


@lru_cache
def get_global_settings() -> GlobalSettings:
    """Obtener configuración global cacheada."""
    return GlobalSettings()  # type: ignore[call-arg]


# Instancia global para compatibilidad
global_settings = get_global_settings()