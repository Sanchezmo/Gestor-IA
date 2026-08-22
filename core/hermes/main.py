"""
Gestor-IA Core - FastAPI Application Entry Point.

Monolito modular que sirve a todas las instancias.
"""

import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.hermes.audit import AuditLogger, create_audit_logger
from core.hermes.config import GlobalSettings, get_global_settings
from core.hermes.context import CompanyContext, get_company_context
from core.hermes.extensions import extension_registry, load_extensions_from_config
from core.hermes.instance_config import list_instances, load_instance_config
from core.hermes.policy import create_model_router_from_config
from core.hermes.resolver import InstanceResolutionMiddleware
from core.integrations.cloudflare.manager import create_cloudflare_manager
from core.integrations.dolibarr.client import DolibarrClient
from core.integrations.telegram.client import TelegramClient, create_telegram_client

# Configurar logging estructurado
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Globals para lifecycle
_global_settings: GlobalSettings | None = None
_audit_logger: AuditLogger | None = None
_cloudflare_manager = None
_telegram_clients: dict[str, TelegramClient] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Gestión del ciclo de vida de la aplicación."""
    global _global_settings, _audit_logger, _cloudflare_manager, _telegram_clients

    # Startup
    _global_settings = get_global_settings()
    logger.info(
        "starting_gestor_ia",
        version="0.1.0",
        environment=_global_settings.ENVIRONMENT,
    )

    # Audit logger global (para eventos de sistema)
    _audit_logger = create_audit_logger(
        database_url=f"mysql+pymysql://root:{_global_settings.MARIADB_ROOT_PASSWORD}@"
        f"{_global_settings.MARIADB_HOST}:{_global_settings.MARIADB_PORT}/gestor_ia_audit"
    )

    # Cloudflare manager
    _cloudflare_manager = create_cloudflare_manager(_global_settings)

    # Precargar configs de instancias activas
    for instance_id in list_instances():
        config = load_instance_config(instance_id)
        if config and config.active:
            # Cargar extensiones declaradas
            load_extensions_from_config(config)

            # Pre-crear Telegram client si tiene token
            if config.telegram.bot_token and config.telegram.bot_token != "CHANGE_ME_TELEGRAM_BOT_TOKEN":
                try:
                    client = await create_telegram_client(config.telegram.bot_token)
                    _telegram_clients[instance_id] = client
                except Exception as e:
                    logger.warning("telegram_client_preload_failed", instance_id=instance_id, error=str(e))

    logger.info("gestor_ia_started", active_instances=len(_telegram_clients))

    yield

    # Shutdown
    logger.info("shutting_down_gestor_ia")

    # Cerrar Telegram clients
    for instance_id, client in _telegram_clients.items():
        try:
            await client.close()
        except Exception as e:
            logger.warning("telegram_client_close_failed", instance_id=instance_id, error=str(e))
    _telegram_clients.clear()

    # Cerrar Cloudflare
    if _cloudflare_manager:
        await _cloudflare_manager.close()

    # Cerrar audit logger
    if _audit_logger:
        _audit_logger.close()

    logger.info("gestor_ia_stopped")


app = FastAPI(
    title="Gestor-IA Core",
    version="0.1.0",
    description="Plataforma empresarial multiempresa y multisector asistida por IA",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configurar restrictivamente en producción
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-RateLimit-Remaining", "X-RateLimit-Reset", "X-Request-ID"],
)

# Middleware de resolución de instancia
app.add_middleware(InstanceResolutionMiddleware)


# Middleware global de logging y request_id
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    start_time = time.time()
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

    # Añadir request_id a response headers
    response = await call_next(request)

    duration_ms = round((time.time() - start_time) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-MS"] = str(duration_ms)

    logger.info(
        "request_completed",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
        instance_id=getattr(request.state, "instance_id", "unresolved"),
    )

    return response


# =========================================================================
# EXCEPTION HANDLERS
# =========================================================================


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": getattr(exc, "error_code", "HTTP_ERROR"),
                "message": exc.detail if isinstance(exc.detail, str) else "Error",
                "details": exc.detail if isinstance(exc.detail, dict) else {},
            }
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(
        "unhandled_exception",
        error=str(exc),
        path=request.url.path,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Error interno del servidor",
                "details": {},
            }
        },
    )


# =========================================================================
# HEALTH CHECKS
# =========================================================================


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check básico."""
    return {
        "status": "healthy",
        "service": "Gestor-IA Core",
        "version": "0.1.0",
        "environment": _global_settings.ENVIRONMENT if _global_settings else "unknown",
    }


@app.get("/health/ready", tags=["Health"])
async def readiness_check():
    """Readiness check - verifica dependencias."""
    checks = {}
    overall = "ready"

    # Verificar MariaDB (audit DB)
    try:
        if _audit_logger:
            _audit_logger.engine.connect().close()
            checks["audit_db"] = "ok"
        else:
            checks["audit_db"] = "not_initialized"
            overall = "not_ready"
    except Exception as e:
        checks["audit_db"] = f"error: {e}"
        overall = "not_ready"

    # Verificar Redis
    try:
        import redis

        r = redis.Redis(
            host=_global_settings.REDIS_HOST,
            port=_global_settings.REDIS_PORT,
            password=_global_settings.REDIS_PASSWORD or None,
            decode_responses=True,
        )
        r.ping()
        r.close()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"
        overall = "not_ready"

    return {
        "status": overall,
        "checks": checks,
    }


# =========================================================================
# INSTANCE MANAGEMENT API (Admin)
# =========================================================================


@app.get("/admin/instances", tags=["Admin"])
async def list_instances_endpoint():
    """Listar todas las instancias configuradas."""
    instances = []
    for instance_id in list_instances():
        config = load_instance_config(instance_id)
        if config:
            instances.append(
                {
                    "instance_id": config.instance_id,
                    "company_name": config.company_name,
                    "active": config.active,
                    "domains": {
                        "base": config.domains.base,
                        "dolibarr": config.domains.dolibarr,
                        "hermes": config.domains.hermes,
                    },
                    "enabled_agents": config.enabled_agents,
                    "enabled_workflows": config.enabled_workflows,
                }
            )
    return {"instances": instances}


@app.get("/admin/instances/{instance_id}", tags=["Admin"])
async def get_instance(instance_id: str):
    """Obtener configuración de una instancia (sin secretos)."""
    config = load_instance_config(instance_id)
    if not config:
        raise HTTPException(404, "Instance not found")

    return {
        "instance_id": config.instance_id,
        "company_name": config.company_name,
        "active": config.active,
        "database": {
            "internal_url": config.database.internal_url,
            "public_url": config.database.public_url,
            "db_name": config.database.db_name,
            "db_user": config.database.db_user,
        },
        "telegram": {
            "webhook_path": config.telegram.webhook_path,
            "webhook_secret_required": config.telegram.webhook_secret_required,
        },
        "domains": {
            "base": config.domains.base,
            "dolibarr": config.domains.dolibarr,
            "hermes": config.domains.hermes,
            "custom": config.domains.custom,
        },
        "ai": {
            "default_policy": config.ai.default_policy.value,
            "ollama_model": config.ai.ollama_model,
            "task_policies": {k: v.value for k, v in config.ai.task_policies.items()},
        },
        "enabled_agents": config.enabled_agents,
        "enabled_workflows": config.enabled_workflows,
        "enabled_tools": config.enabled_tools,
        "dolibarr_apache_port": config.dolibarr_apache_port,
    }


@app.post("/admin/instances/{instance_id}/reload", tags=["Admin"])
async def reload_instance(instance_id: str):
    """Recargar configuración de una instancia (limpiar cache)."""
    from core.hermes.extensions import extension_registry
    from core.hermes.instance_config import clear_config_cache

    config = load_instance_config(instance_id)
    if not config:
        raise HTTPException(404, "Instance not found")

    clear_config_cache()
    extension_registry.clear_instance(instance_id)
    load_extensions_from_config(config)

    # Recargar Telegram client si cambió
    if config.telegram.bot_token and config.telegram.bot_token != "CHANGE_ME_TELEGRAM_BOT_TOKEN":
        if instance_id in _telegram_clients:
            await _telegram_clients[instance_id].close()
        _telegram_clients[instance_id] = await create_telegram_client(config.telegram.bot_token)

    return {"success": True, "message": f"Instance {instance_id} reloaded"}


# =========================================================================
# TELEGRAM WEBHOOK ENDPOINT (Multi-instancia)
# =========================================================================


@app.post("/webhook/{instance_id}", tags=["Telegram"])
@app.post("/webhook/{instance_id}/", tags=["Telegram"])
async def telegram_webhook(
    instance_id: str,
    request: Request,
    ctx: CompanyContext = Depends(get_company_context),
):
    """
    Webhook de Telegram multi-instancia.

    La instancia se resuelve ANTES por el path /webhook/{instance_id}.
    El middleware InstanceResolutionMiddleware valida que coincida.
    """
    # Verificar que el instance_id del path coincide con el resuelto
    if ctx.instance_id != instance_id:
        raise HTTPException(400, "Instance ID mismatch")

    # Verificar secret token
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if ctx.telegram_config.webhook_secret_required:
        if not secret_token:
            raise HTTPException(403, "Missing webhook secret token")
        import hmac

        if not hmac.compare_digest(secret_token, ctx.telegram_config.webhook_secret):
            raise HTTPException(403, "Invalid webhook secret token")

    # Parse update
    try:
        update = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    update_id = update.get("update_id")

    # Idempotency check (Redis)
    from core.hermes.config import get_global_settings

    settings = get_global_settings()
    import redis

    r = redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD or None,
        db=ctx.instance_config.get_redis_db(),
        decode_responses=True,
    )

    idempotency_key = f"telegram:update:{update_id}"
    if r.exists(idempotency_key):
        # Duplicate - return 200 OK to avoid Telegram retries
        return {"success": True, "duplicate": True, "update_id": update_id}

    # Marcar como procesando (TTL 24h)
    r.setex(idempotency_key, 86400, "processing")

    try:
        # Obtener Telegram client para esta instancia
        telegram_client = _telegram_clients.get(instance_id)
        if not telegram_client:
            telegram_client = await create_telegram_client(ctx.telegram_config.bot_token)
            _telegram_clients[instance_id] = telegram_client

        # TODO: Procesar update con SupervisorAgent/ExtensionRegistry
        # Por ahora: echo básico
        message = update.get("message") or update.get("edited_message")
        if message:
            chat_id = message.get("chat", {}).get("id")
            text = message.get("text", "")

            if text == "/start":
                await telegram_client.send_message(
                    chat_id=chat_id,
                    text=f"🤖 Gestor-IA - {ctx.company_name}\n\nInstancia: {ctx.instance_id}\n\nComandos:\n/start - Este mensaje\n/help - Ayuda\n/status - Estado",
                )
            elif text == "/help":
                await telegram_client.send_message(
                    chat_id=chat_id,
                    text="Comandos disponibles:\n/start - Inicio\n/help - Esta ayuda\n/status - Estado de la instancia",
                )
            elif text == "/status":
                await telegram_client.send_message(
                    chat_id=chat_id,
                    text=f"Instancia: {ctx.instance_id}\nEmpresa: {ctx.company_name}\nAgentes: {', '.join(ctx.enabled_agents) or 'ninguno'}",
                )
            else:
                await telegram_client.send_message(
                    chat_id=chat_id,
                    text=f"Recibido: {text}\n\nUsa /help para comandos.",
                )

        # Marcar completado
        r.setex(idempotency_key, 86400, "completed")

        return {"success": True, "update_id": update_id}

    except Exception as e:
        logger.error("webhook_processing_failed", instance_id=instance_id, error=str(e))
        r.delete(idempotency_key)  # Permitir reintento
        raise HTTPException(500, "Processing failed")


# =========================================================================
# DOLIBARR PROXY ENDPOINTS (Por instancia)
# =========================================================================


@app.get("/api/{instance_id}/dolibarr/thirdparties", tags=["Dolibarr"])
async def list_thirdparties(
    instance_id: str,
    ctx: CompanyContext = Depends(get_company_context),
    limit: int = 100,
    offset: int = 0,
):
    """Listar terceros de Dolibarr para la instancia."""
    if ctx.instance_id != instance_id:
        raise HTTPException(400, "Instance ID mismatch")

    client = DolibarrClient.from_instance_config(ctx.dolibarr_config)
    async with client as c:
        return await c.list_thirdparties(limit=limit, offset=offset)


@app.post("/api/{instance_id}/dolibarr/thirdparties", tags=["Dolibarr"])
async def create_thirdparty(
    instance_id: str,
    data: dict,
    ctx: CompanyContext = Depends(get_company_context),
):
    """Crear tercero en Dolibarr para la instancia."""
    if ctx.instance_id != instance_id:
        raise HTTPException(400, "Instance ID mismatch")

    from core.integrations.dolibarr.mappers import thirdparty_to_dolibarr

    client = DolibarrClient.from_instance_config(ctx.dolibarr_config)
    async with client as c:
        dolibarr_data = thirdparty_to_dolibarr(data, is_client=data.get("client", True))
        return await c.create_thirdparty(dolibarr_data)


# =========================================================================
# AI ENDPOINTS (Por instancia)
# =========================================================================


@app.post("/api/{instance_id}/ai/generate", tags=["AI"])
async def ai_generate(
    instance_id: str,
    request: Request,
    ctx: CompanyContext = Depends(get_company_context),
):
    """Generar texto con IA según política de la instancia."""
    if ctx.instance_id != instance_id:
        raise HTTPException(400, "Instance ID mismatch")

    body = await request.json()
    prompt = body.get("prompt", "")
    task = body.get("task", "general")

    if not prompt:
        raise HTTPException(400, "Prompt required")

    # Determinar política
    from core.hermes.policy import AIPolicy

    policy = AIPolicy(ctx.ai_config)
    scope = policy.get_scope_for_task(task)

    # Crear router y generar
    router = create_model_router_from_config(ctx.ai_config)
    try:
        result = await router.generate(
            privacy_scope=scope,
            prompt=prompt,
        )
        return {"success": True, "text": result["text"], "scope": scope.value}
    finally:
        await router.aclose()


# =========================================================================
# AUDIT ENDPOINTS (Por instancia)
# =========================================================================


@app.get("/api/{instance_id}/audit", tags=["Audit"])
async def query_audit(
    instance_id: str,
    ctx: CompanyContext = Depends(get_company_context),
    action: str = None,
    resource_type: str = None,
    limit: int = 100,
    offset: int = 0,
):
    """Consultar logs de auditoría de la instancia."""
    if ctx.instance_id != instance_id:
        raise HTTPException(400, "Instance ID mismatch")

    audit_logger = create_audit_logger(instance_config=ctx.instance_config)
    logs = audit_logger.query_logs(
        instance_id=instance_id,
        action=action,
        resource_type=resource_type,
        limit=limit,
        offset=offset,
    )
    return {"logs": logs}


# =========================================================================
# EXTENSIONS ENDPOINTS (Descubrimiento)
# =========================================================================


@app.get("/api/{instance_id}/extensions", tags=["Extensions"])
async def list_extensions(
    instance_id: str,
    ctx: CompanyContext = Depends(get_company_context),
):
    """Listar extensiones disponibles para la instancia."""
    if ctx.instance_id != instance_id:
        raise HTTPException(400, "Instance ID mismatch")

    return extension_registry.get_instance_summary(instance_id)


# =========================================================================
# ROOT
# =========================================================================


@app.get("/", tags=["Root"])
async def root():
    return {
        "service": "Gestor-IA Core",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
        "admin": "/admin/instances",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("core.heroku.main:app", host="0.0.0.0", port=8000, reload=True)
