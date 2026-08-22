"""
Instance Resolver - Resuelve InstanceConfig ANTES de procesar contenido.

Principio crítico: La instancia debe quedar resuelta ANTES de procesar el contenido
del mensaje. NO se determina la empresa analizando el texto.
"""

from typing import Optional, Callable, Awaitable
from functools import lru_cache

from fastapi import Request, HTTPException, Depends, Header
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from core.hermes.instance_config import InstanceConfig, load_instance_config, list_instances
from core.hermes.context import CompanyContext, CompanyContextBuilder, extract_telegram_actor, extract_api_key_actor
from core.hermes.config import get_global_settings


# =========================================================================
# CACHE DE DOMINIOS -> INSTANCE_ID
# =========================================================================

_domain_cache: dict[str, str] = {}
_cache_loaded = False


def _build_domain_index(instances_root: Optional[str] = None) -> dict[str, str]:
    """Construir índice dominio -> instance_id desde todas las configs."""
    global _domain_cache, _cache_loaded
    
    if _cache_loaded:
        return _domain_cache
    
    settings = get_global_settings()
    if instances_root is None:
        instances_root = settings.PROJECT_ROOT / "instances"
    
    for instance_id in list_instances(instances_root):
        config = load_instance_config(instance_id, instances_root)
        if config and config.active:
            domains = config.domains
            # Dominio base
            _domain_cache[domains.base.lower()] = instance_id
            # Subdominios configurados
            if domains.dolibarr:
                _domain_cache[domains.dolibarr.lower()] = instance_id
            if domains.hermes:
                _domain_cache[domains.hermes.lower()] = instance_id
            for _, hostname in domains.custom.items():
                _domain_cache[hostname.lower()] = instance_id
    
    _cache_loaded = True
    return _domain_cache


def invalidate_domain_cache():
    """Invalidar cache de dominios (llamar tras crear/modificar instancia)."""
    global _domain_cache, _cache_loaded
    _domain_cache.clear()
    _cache_loaded = False


async def lookup_instance_by_domain(host: str) -> Optional[str]:
    """Buscar instance_id por hostname (Host header)."""
    host = host.lower().split(":")[0]  # Quitar puerto
    index = _build_domain_index()
    return index.get(host)


async def lookup_instance_by_api_key(api_key: str) -> Optional[str]:
    """Buscar instance_id por API key (prefijo: gsk_{instance}_)."""
    if api_key.startswith("gsk_"):
        parts = api_key.split("_", 2)
        if len(parts) >= 2:
            return parts[1]
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
    instance_id: Optional[str] = None
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
                "message": "Cannot resolve company instance. Provide X-Instance-ID header, use correct webhook path, or configure custom domain.",
                "tried_methods": ["header", "webhook_path", "domain", "api_key"],
            }
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
            }
        )
    
    if not config.active:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "INSTANCE_INACTIVE",
                "message": f"Instance '{instance_id}' is disabled",
                "instance_id": instance_id,
            }
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
    
    return CompanyContextBuilder(instance_config) \
        .with_actor(actor_type, actor_id) \
        .with_request_id(request_id) \
        .with_correlation_id(correlation_id or "") \
        .with_http_info(ip, user_agent, str(request.url.path), request.method) \
        .build()


# =========================================================================
# MIDDLEWARE OPCIONAL (PARA AUTO-INJECT EN REQUEST.STATE)
# =========================================================================

class InstanceResolutionMiddleware(BaseHTTPMiddleware):
    """
    Middleware que resuelve la instancia y la pone en request.state.
    
    Útil si se prefiere middleware sobre dependency injection.
    """
    
    async def dispatch(
        self, 
        request: Request, 
        call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Solo resolver para rutas que lo necesiten
        path = request.url.path
        
        # Rutas que NO requieren resolución de instancia
        skip_paths = {
            "/health", "/health/ready", "/metrics", "/docs", "/redoc", "/openapi.json",
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
    from core.hermes.instance_config import InstanceConfig, DolibarrConfig, TelegramConfig, DomainConfig, AIConfig
    
    config = InstanceConfig(
        instance_id=instance_id,
        company_name=company_name,
        database=DolibarrConfig(
            internal_url="http://127.0.0.1:8081",
            api_key="test_key",
            documents_path="/tmp/test_docs",
            db_name=f"dolibarr_{instance_id}",
            db_user=f"db_{instance_id}",
            db_password="test_pass",
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
def get_cached_instance_config(instance_id: str) -> Optional[InstanceConfig]:
    """Versión cacheada para uso en workers/background tasks."""
    return load_instance_config(instance_id)