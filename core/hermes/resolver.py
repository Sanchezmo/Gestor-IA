"""
Instance Resolver - Resuelve InstanceConfig ANTES de procesar contenido.

Principio crítico: La instancia debe quedar resuelta ANTES de procesar el contenido
del mensaje. NO se determina la empresa analizando el texto.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from core.hermes.config import get_global_settings
from core.hermes.context import CompanyContext, CompanyContextBuilder, extract_api_key_actor, extract_telegram_actor
from core.hermes.identity import UserContext
from core.hermes.identity_resolver import (
    DolibarrConnectionError,
    DolibarrUserDisabledError,
    DolibarrUserNotFoundError,
    IdentityDisabledError,
    IdentityNotFoundError,
    IdentityResolver,
)
from core.hermes.identity_store import IdentityStore
from core.hermes.instance_config import InstanceConfig, list_instances, load_instance_config
from core.integrations.dolibarr.client import DolibarrClient

# =========================================================================
# CACHE DE DOMINIOS -> INSTANCE_ID
# =========================================================================

_domain_cache: dict[str, str] = {}
_cache_loaded = False


def _build_domain_index(instances_root: str | None = None) -> dict[str, str]:
    """Construir índice dominio -> instance_id desde todas las configs."""
    global _domain_cache, _cache_loaded

    if _cache_loaded:
        return _domain_cache

    # Primero cargar desde cache en memoria (para tests)
    from core.hermes.instance_config import _config_cache

    for instance_id, config in _config_cache.items():
        if config and config.active:
            domains = config.domains
            _domain_cache[domains.base.lower()] = instance_id
            if domains.dolibarr:
                _domain_cache[domains.dolibarr.lower()] = instance_id
            if domains.hermes:
                _domain_cache[domains.hermes.lower()] = instance_id
            for _, hostname in domains.custom.items():
                _domain_cache[hostname.lower()] = instance_id

    # Luego cargar desde filesystem (para producción)
    settings = get_global_settings()
    if instances_root is None:
        instances_root = settings.PROJECT_ROOT / "instances"

    for instance_id in list_instances(instances_root):
        config = load_instance_config(instance_id, instances_root)
        if config and config.active:
            domains = config.domains
            _domain_cache[domains.base.lower()] = instance_id
            if domains.dolibarr:
                _domain_cache[domains.dolibarr.lower()] = instance_id
            if domains.hermes:
                _domain_cache[domains.hermes.lower()] = instance_id
            for _, hostname in domains.custom.items():
                _domain_cache[hostname.lower()] = instance_id

    _cache_loaded = True
    return _domain_cache

    _cache_loaded = True
    return _domain_cache


def invalidate_domain_cache():
    """Invalidar cache de dominios (llamar tras crear/modificar instancia)."""
    global _domain_cache, _cache_loaded
    _domain_cache.clear()
    _cache_loaded = False


async def lookup_instance_by_domain(host: str) -> str | None:
    """Buscar instance_id por hostname (Host header)."""
    host = host.lower().split(":")[0]  # Quitar puerto
    index = _build_domain_index()
    return index.get(host)


async def lookup_instance_by_api_key(api_key: str) -> str | None:
    """Buscar instance_id por API key (formato: gsk_{b64_instance_id}_{key})."""
    if api_key.startswith("gsk_"):
        # Formato: gsk_{b64_instance_id}_{key}
        # instance_id se codifica en base64 URL-safe para permitir underscores
        parts = api_key.split("_", 2)
        if len(parts) >= 3:
            import base64

            try:
                # Decodificar instance_id desde base64 URL-safe
                b64_instance_id = parts[1]
                # Añadir padding si necesario
                padding = 4 - len(b64_instance_id) % 4
                if padding != 4:
                    b64_instance_id += "=" * padding
                instance_id = base64.urlsafe_b64decode(b64_instance_id).decode()
                return instance_id
            except Exception:
                pass
    return None


# =========================================================================
# RESOLVER PRINCIPAL
# =========================================================================


async def resolve_instance_config(request: Request) -> InstanceConfig:
    """
    Resolver InstanceConfig para el request actual.

    Prioridad de resolución (orden estricto):
    1. Header X-Instance-ID (API calls explícitos)
    2. Telegram webhook path: /webhook/{instance_id}/...
    3. Host header → DomainConfig lookup
    4. API Key (Authorization: Bearer gsk_{instance}_...)

    Lanza HTTPException si no se puede resolver.
    """
    instance_id: str | None = None
    resolution_method = "unknown"

    # 1. Header explícito X-Instance-ID (máxima prioridad para APIs)
    instance_id = request.headers.get("X-Instance-ID")
    if instance_id:
        resolution_method = "header"

    # 2. Telegram webhook path: /webhook/{instance_id}/...
    if not instance_id and request.url.path.startswith("/webhook/"):
        path_parts = request.url.path.strip("/").split("/")
        if len(path_parts) >= 2 and path_parts[0] == "webhook":
            instance_id = path_parts[1]
            resolution_method = "webhook_path"

    # 3. Host header (dominio personalizado)
    if not instance_id:
        host = request.headers.get("host", "").split(":")[0]
        if host:
            instance_id = await lookup_instance_by_domain(host)
            if instance_id:
                resolution_method = "domain"

    # 4. API Key (Authorization: Bearer gsk_{instance}_...)
    if not instance_id:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            api_key = auth[7:]  # Quitar "Bearer "
            instance_id = await lookup_instance_by_api_key(api_key)
            if instance_id:
                resolution_method = "api_key"

    if not instance_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INSTANCE_RESOLUTION_FAILED",
                "message": (
                    "Cannot resolve company instance. Provide X-Instance-ID header, "
                    "use correct webhook path, or configure custom domain."
                ),
                "tried_methods": ["header", "webhook_path", "domain", "api_key"],
            },
        )

    # Cargar config completa
    config = load_instance_config(instance_id)
    if not config:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "INSTANCE_NOT_FOUND",
                "message": f"Instance '{instance_id}' not found or inactive",
                "resolved_by": resolution_method,
            },
        )

    if not config.active:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "INSTANCE_INACTIVE",
                "message": f"Instance '{instance_id}' is disabled",
                "instance_id": instance_id,
            },
        )

    # Log de resolución para debugging/auditoría
    request.state.instance_resolution = {
        "instance_id": instance_id,
        "method": resolution_method,
    }

    return config


# =========================================================================
# DEPENDENCY PARA COMPANYCONTEXT COMPLETO
# =========================================================================


async def get_company_context(request: Request) -> CompanyContext:
    """
    Dependency de FastAPI que provee CompanyContext completo.

    Uso:
        @app.get("/api/resource")
        async def handler(ctx: CompanyContext = Depends(get_company_context)):
            # ctx.instance_config, ctx.actor_type, etc.
    """
    # Resolver InstanceConfig
    instance_config = await resolve_instance_config(request)

    # Determinar actor
    actor_type = "unknown"
    actor_id = "unknown"

    # Telegram webhook
    if request.url.path.startswith("/webhook/"):
        try:
            body = await request.json()
            # Cache body in request.state for webhook handler to reuse
            request.state.telegram_update = body
            actor_type, actor_id = extract_telegram_actor(body)
        except Exception:
            actor_type, actor_id = "telegram_webhook", "unknown"

    # API Key
    elif auth := request.headers.get("Authorization", ""):
        if auth.startswith("Bearer "):
            actor_type, actor_id = extract_api_key_actor(auth[7:])

    # System/Internal
    elif request.headers.get("X-Internal-Request"):
        actor_type, actor_id = "system", "internal"

    # Request ID para trazabilidad
    request_id = request.headers.get("X-Request-ID", "")

    # Correlation ID (para rastrear requests relacionados)
    correlation_id = request.headers.get("X-Correlation-ID")

    # Info HTTP para auditoría
    client = request.client
    ip = client.host if client else None
    user_agent = request.headers.get("User-Agent")

    return (
        CompanyContextBuilder(instance_config)
        .with_actor(actor_type, actor_id)
        .with_request_id(request_id)
        .with_correlation_id(correlation_id or "")
        .with_http_info(ip, user_agent, str(request.url.path), request.method)
        .build()
    )


# =========================================================================
# DEPENDENCY PARA USERCONTEXT (OPERACIONES AUTENTICADAS)
# =========================================================================


async def get_user_context(
    request: Request,
    company_context: CompanyContext = Depends(get_company_context),
) -> UserContext:
    """
    Dependency de FastAPI que provee UserContext para endpoints autenticados.

    Requiere que la instancia ya esté resuelta (CompanyContext).
    Resuelve la identidad del usuario Telegram -> Dolibarr -> UserContext.

    Lanza HTTPException 403 si no hay identidad válida.
    Lanza HTTPException 503 si falla la conexión con Dolibarr (default deny).

    Uso:
        @app.get("/api/private")
        async def handler(user_ctx: UserContext = Depends(get_user_context)):
            # user_ctx.dolibarr_user, user_ctx.effective_permissions, etc.
    """
    # Extract telegram_user_id from webhook update or actor_id
    telegram_user_id: int | None = None

    if request.url.path.startswith("/webhook/"):
        try:
            # Use cached body from get_company_context if available
            body = getattr(request.state, "telegram_update", None)
            if body is None:
                raise HTTPException(400, "Request body not cached")
            _, actor_id = extract_telegram_actor(body)
            telegram_user_id = int(actor_id)
        except Exception:
            pass
    elif company_context.actor_type == "telegram_user":
        try:
            telegram_user_id = int(company_context.actor_id)
        except Exception:
            pass

    if not telegram_user_id:
        raise HTTPException(
            status_code=401,
            detail={"error": "AUTHENTICATION_REQUIRED", "message": "Telegram user identity required"},
        )

    # Resolve identity using IdentityResolver
    identity_store = IdentityStore(company_context.instance_id)

    def _client_factory(ctx: CompanyContext, identity: TelegramIdentity) -> DolibarrClient:
        return DolibarrClient.from_instance_config(ctx.dolibarr_config)

    resolver = IdentityResolver(identity_store, _client_factory)

    try:
        user_context = await resolver.resolve(company_context, telegram_user_id)
    except IdentityNotFoundError:
        raise HTTPException(
            status_code=403,
            detail={"error": "UNAUTHORIZED", "message": "No tienes acceso autorizado a este asistente"},
        )
    except IdentityDisabledError:
        raise HTTPException(
            status_code=403,
            detail={"error": "ACCOUNT_DISABLED", "message": "Tu cuenta ha sido deshabilitada"},
        )
    except DolibarrUserNotFoundError:
        raise HTTPException(
            status_code=403,
            detail={"error": "USER_NOT_FOUND", "message": "Usuario no encontrado en el ERP"},
        )
    except DolibarrUserDisabledError:
        raise HTTPException(
            status_code=403,
            detail={"error": "ACCOUNT_DISABLED", "message": "Tu cuenta en el ERP está desactivada"},
        )
    except DolibarrConnectionError:
        # Default deny on Dolibarr failure
        raise HTTPException(
            status_code=503,
            detail={"error": "AUTH_SERVICE_UNAVAILABLE", "message": "Servicio de autorización no disponible"},
        )

    return user_context


# =========================================================================
# HELPER: Resolver UserContext desde CompanyContext ya resuelto
# =========================================================================


async def resolve_user_context_from_company_context(
    company_context: CompanyContext,
    telegram_user_id: int,
) -> UserContext | None:
    """
    Resolver UserContext usando CompanyContext ya resuelto (sin leer request.body).
    
    Retorna None si no hay identidad válida (en lugar de lanzar HTTPException).
    Útil para webhook handlers que quieren manejar auth graceful.
    """
    identity_store = IdentityStore(company_context.instance_id)

    def _client_factory(ctx: CompanyContext, identity: TelegramIdentity) -> DolibarrClient:
        return DolibarrClient.from_instance_config(ctx.dolibarr_config)

    resolver = IdentityResolver(identity_store, _client_factory)

    try:
        user_context = await resolver.resolve(company_context, telegram_user_id)
        return user_context
    except (IdentityNotFoundError, IdentityDisabledError, DolibarrUserNotFoundError, DolibarrUserDisabledError):
        return None
    except DolibarrConnectionError:
        # Default deny on Dolibarr failure
        return None


# =========================================================================
# MIDDLEWARE OPCIONAL (PARA AUTO-INJECT EN REQUEST.STATE)
# =========================================================================


class InstanceResolutionMiddleware(BaseHTTPMiddleware):
    """
    Middleware que resuelve la instancia y la pone en request.state.

    Útil si se prefiere middleware sobre dependency injection.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # Solo resolver para rutas que lo necesiten
        path = request.url.path

        # Rutas que NO requieren resolución de instancia
        skip_paths = {
            "/health",
            "/health/ready",
            "/metrics",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/admin",
        }

        if any(path.startswith(p) for p in skip_paths):
            return await call_next(request)

        try:
            instance_config = await resolve_instance_config(request)
            request.state.instance_config = instance_config
            request.state.instance_id = instance_config.instance_id
        except HTTPException:
            # Dejar que el exception handler lo maneje
            raise
        except Exception as e:
            raise HTTPException(500, f"Instance resolution error: {e}")

        return await call_next(request)


# =========================================================================
# HELPERS PARA TESTING
# =========================================================================


def create_test_context(
    instance_id: str = "test",
    company_name: str = "Test Company",
    actor_type: str = "test",
    actor_id: str = "test_user",
) -> CompanyContext:
    """Crear CompanyContext para tests unitarios."""
    from core.hermes.instance_config import (
        AIConfig,
        DatabaseConfig,
        DolibarrConfig,
        DomainConfig,
        InstanceConfig,
        TelegramConfig,
    )

    config = InstanceConfig(
        instance_id=instance_id,
        company_name=company_name,
        database=DatabaseConfig(
            host="127.0.0.1",
            port=3306,
            name=f"dolibarr_{instance_id}",
            user=f"db_{instance_id}",
            password="test_pass",
        ),
        dolibarr=DolibarrConfig(
            internal_url="http://127.0.0.1:8081",
            api_key="test_key",
            documents_path="/tmp/test_docs",
        ),
        telegram=TelegramConfig(
            bot_token="test_token",
            webhook_path=f"/webhook/{instance_id}",
            webhook_secret="test_secret",
        ),
        domains=DomainConfig(base="test.example.com"),
        ai=AIConfig(),
    )

    return CompanyContext(
        instance_config=config,
        actor_type=actor_type,
        actor_id=actor_id,
    )


@lru_cache
def get_cached_instance_config(instance_id: str) -> InstanceConfig | None:
    """Versión cacheada para uso en workers/background tasks."""
    return load_instance_config(instance_id)
