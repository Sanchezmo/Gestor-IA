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

from core.hermes.audit import AuditActions, AuditLogger, create_audit_logger
from core.hermes.authorization import AuthorizationService
from core.hermes.config import GlobalSettings, get_global_settings
from core.hermes.context import CompanyContext
from core.hermes.extensions import extension_registry, load_extensions_from_config
from core.hermes.identity import UserContext
from core.hermes.insights import (
    execute_customer_insight,
    execute_supplier_insight,
    format_customer_invoice_summary_by_thirdparty_for_telegram,
    format_customer_invoice_summary_for_telegram,
    format_customer_outstanding_by_thirdparty_for_telegram,
    format_customer_outstanding_summary_for_telegram,
    format_supplier_invoice_summary_by_thirdparty_for_telegram,
    format_supplier_invoice_summary_for_telegram,
    format_supplier_outstanding_by_thirdparty_for_telegram,
    format_supplier_outstanding_summary_for_telegram,
)
from core.hermes.instance_config import list_instances, load_instance_config
from core.hermes.policy import create_model_router_from_config
from core.hermes.query import (
    CompositeIntentInterpreter,
    format_count_for_telegram,
    format_thirdparties_for_telegram,
    format_thirdparty_detail_for_telegram,
    structured_intent_to_tool_call,
)
from core.hermes.query.models import (
    InsightAction,
    InterpretationStatus,
    InvoiceAction,
    InvoicePartyType,
    ThirdpartyAction,
    CommandAction,
)
from core.hermes.resolver import InstanceResolutionMiddleware, get_company_context, get_user_context, resolve_user_context_from_company_context
from core.hermes.tools import tool_registry
from core.hermes.tools.invoices import register_core_invoice_tools
from core.hermes.tools.invoices.formatters import (
    format_customer_invoice_detail_for_telegram,
    format_customer_invoices_for_telegram,
    format_invoice_count_for_telegram,
    format_supplier_invoice_detail_for_telegram,
    format_supplier_invoices_for_telegram,
)
from core.hermes.tools.product_formatters import (
    format_product_count_for_telegram,
    format_product_detail_for_telegram,
    format_products_for_telegram,
)
from core.hermes.tools.product_tools import register_core_product_tools
from core.hermes.tools.thirdparty_tools import register_core_thirdparty_tools
from core.hermes.commands import register_core_commands, command_registry
from core.hermes.commands.executor import CommandExecutor
from core.hermes.commands.store import PendingCommandStore
from core.hermes.commands.telegram import send_command_preview, handle_command_callback
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
_intent_interpreter: CompositeIntentInterpreter | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Gestión del ciclo de vida de la aplicación."""
    global _global_settings, _audit_logger, _cloudflare_manager, _telegram_clients, _intent_interpreter

    # Startup
    _global_settings = get_global_settings()
    logger.info(
        "starting_gestor_ia",
        version="0.1.0",
        environment=_global_settings.ENVIRONMENT,
    )

    # Audit logger global (para eventos de sistema)
    _audit_logger = create_audit_logger(
        database_url=f"mysql+pymysql://gestor_ia_audit:{_global_settings.MARIADB_AUDIT_PASSWORD}@"
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

    # Registrar core tools de Hermes
    register_core_thirdparty_tools()
    register_core_invoice_tools()
    register_core_product_tools()

    # Registrar core commands de Hermes (Command Layer V1)
    register_core_commands()

    # Crear intérprete de intención compuesto (parser-first + Ollama fallback)
    # Usamos una config dummy para el intérprete global; se creará uno por request con CompanyContext
    from core.hermes.query.factory import create_deterministic_interpreter
    from core.hermes.query.interpreter import CompositeIntentInterpreter

    _intent_interpreter = CompositeIntentInterpreter(
        deterministic=create_deterministic_interpreter(),
        ollama=None,  # Se crea por request con config de instancia
    )

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

    # Cerrar intérprete de intención
    if _intent_interpreter:
        await _intent_interpreter.aclose()

    # Cerrar Cloudflare
    if _cloudflare_manager:
        await _cloudflare_manager.close()

    # Cerrar audit logger
    if _audit_logger:
        _audit_logger.close()

    logger.info("gestor_ia_stopped")


# Get global settings for environment-aware config
_global_settings_for_app = get_global_settings()
_is_production = _global_settings_for_app.ENVIRONMENT == "production"

# FastAPI app with environment-aware docs
app = FastAPI(
    title="Gestor-IA Core",
    version="0.1.0",
    description="Plataforma empresarial multiempresa y multisector asistida por IA",
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
    lifespan=lifespan,
)

# CORS - Environment-aware configuration
if _is_production:
    # Production: DEFAULT DENY - explicit allowlist required
    cors_allow_origins = getattr(_global_settings_for_app, "CORS_ALLOW_ORIGINS", "")
    cors_origins = cors_allow_origins.split(",") if cors_allow_origins else []
    cors_allow_credentials = False
else:
    # Development: Allow localhost and configurable origins
    cors_origins = ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8000", "http://127.0.0.1:8000"]
    if getattr(_global_settings_for_app, "CORS_ALLOW_ORIGINS", ""):
        cors_origins.extend(_global_settings_for_app.CORS_ALLOW_ORIGINS.split(","))
    cors_allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
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
# AUTHORIZATION DEPENDENCIES
# =========================================================================


class RequirePermission:
    """Dependency class for requiring a specific permission."""

    def __init__(self, permission: str):
        self.permission = permission

    async def __call__(self, request: Request, user_context: UserContext = Depends(get_user_context)) -> UserContext:
        auth_service = AuthorizationService()
        auth_service.require(user_context, self.permission)
        return user_context


async def require_admin_user(request: Request) -> CompanyContext:
    """
    Require admin access for /admin/* endpoints.

    Validates GESTOR_IA_ADMIN_TOKEN from environment.
    Does NOT require instance resolution since admin endpoints are global.
    """
    import hmac

    from core.hermes.config import get_global_settings

    settings = get_global_settings()
    admin_token = settings.GESTOR_IA_ADMIN_TOKEN

    if not admin_token:
        raise HTTPException(
            status_code=503,
            detail={"error": "ADMIN_NOT_CONFIGURED", "message": "Admin token not configured"},
        )

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=403,
            detail={"error": "ADMIN_REQUIRED", "message": "Administrative access required"},
        )

    provided_token = auth[7:]  # Remove "Bearer " prefix

    # Secure comparison using hmac.compare_digest
    if not hmac.compare_digest(provided_token, admin_token):
        raise HTTPException(
            status_code=403,
            detail={"error": "ADMIN_REQUIRED", "message": "Administrative access required"},
        )

    # Return a minimal CompanyContext for admin operations
    from core.hermes.context import CompanyContextBuilder
    from core.hermes.instance_config import (
        AIConfig,
        DatabaseConfig,
        DolibarrConfig,
        DomainConfig,
        InstanceConfig,
        TelegramConfig,
    )

    dummy_config = InstanceConfig(
        instance_id="admin",
        company_name="Gestor-IA Admin",
        database=DatabaseConfig(host="127.0.0.1", port=3306, name="admin", user="admin", password="admin"),
        dolibarr=DolibarrConfig(
            version="1.0",
            internal_url="http://127.0.0.1:8081",
            api_key="admin",
            documents_path="/tmp",
        ),
        telegram=TelegramConfig(bot_token="admin", webhook_path="/webhook/admin", webhook_secret="admin"),
        domains=DomainConfig(base="admin.local"),
        ai=AIConfig(ollama_model="dummy"),
    )
    return CompanyContextBuilder(dummy_config).with_actor("admin", "admin").build()


async def require_authenticated_user(
    request: Request, user_context: UserContext = Depends(get_user_context)
) -> UserContext:
    """Require authenticated user for /api/* endpoints."""
    return user_context


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
async def list_instances_endpoint(ctx: CompanyContext = Depends(require_admin_user)):
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
                    "enabled_tools": config.enabled_tools,
                    "dolibarr_api_key_configured": bool(config.dolibarr.api_key),
                    "telegram_webhook_secret_configured": bool(config.telegram.webhook_secret),
                }
            )
    return {"instances": instances}


@app.get("/admin/instances/{instance_id}", tags=["Admin"])
async def get_instance(instance_id: str, ctx: CompanyContext = Depends(require_admin_user)):
    """Obtener configuración de una instancia (sin secretos)."""
    config = load_instance_config(instance_id)
    if not config:
        raise HTTPException(404, "Instance not found")

    return {
        "instance_id": config.instance_id,
        "company_name": config.company_name,
        "active": config.active,
        "database": {
            "host": config.database.host,
            "port": config.database.port,
            "name": config.database.name,
            "user": config.database.user,
        },
        "dolibarr": {
            "internal_url": config.dolibarr.internal_url,
            "public_url": config.dolibarr.public_url,
            "api_key_configured": bool(config.dolibarr.api_key),
            "documents_path": config.dolibarr.documents_path,
            "version": config.dolibarr.version,
        },
        "telegram": {
            "webhook_path": config.telegram.webhook_path,
            "webhook_secret_required": config.telegram.webhook_secret_required,
            "webhook_secret_configured": bool(config.telegram.webhook_secret),
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
async def reload_instance(instance_id: str, ctx: CompanyContext = Depends(require_admin_user)):
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


async def _get_telegram_client(instance_id: str, bot_token: str) -> TelegramClient:
    """Obtener o crear TelegramClient para una instancia."""
    client = _telegram_clients.get(instance_id)
    if not client:
        client = await create_telegram_client(bot_token)
        _telegram_clients[instance_id] = client
    return client


async def _format_thirdparties_response(parties: list[dict], limit: int, page: int) -> str:
    """Formatear lista de terceros para Telegram."""
    if not parties:
        return "No se han encontrado terceros."

    lines = ["Terceros encontrados:"]
    for i, p in enumerate(parties, 1):
        tipo = []
        if p.get("is_customer"):
            tipo.append("Cliente")
        if p.get("is_supplier"):
            tipo.append("Proveedor")
        tipo_str = f" ({', '.join(tipo)})" if tipo else ""
        email_str = f" - {p['email']}" if p.get("email") else ""
        # Dolibarr usa 'nom' para el nombre de terceros
        name = p.get("nom") or p.get("name") or "Sin nombre"
        lines.append(f"{i}. {name}{tipo_str}{email_str}")

    if len(parties) >= limit:
        lines.append(f"\nMostrando los primeros {limit} resultados (página {page + 1}).")

    return "\n".join(lines)


@app.post("/webhook/{instance_id}", tags=["Telegram"])
@app.post("/webhook/{instance_id}/", tags=["Telegram"])
async def telegram_webhook(
    instance_id: str,
    request: Request,
    ctx: CompanyContext = Depends(get_company_context),
):
    """
    Webhook de Telegram multi-instancia.

    Pipeline:
    1. Validar webhook secret (ya hecho por middleware/get_company_context)
    2. Idempotencia (Redis)
    3. Resolver identidad -> UserContext
    4. Enrutar comando a Hermes Tool
    5. Formatear respuesta
    6. Auditar
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

    # Parse update (use cached body from get_company_context dependency)
    update = getattr(request.state, "telegram_update", None)
    if update is None:
        raise HTTPException(400, "Request body not cached - dependency order issue")

    update_id = update.get("update_id")

    # Idempotency check (Redis) - ATOMIC
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

    # ATOMIC: SET key value NX EX ttl
    # Only the request that successfully creates the key processes the update
    # Returns True if key was set, False if already exists
    acquired = r.set(idempotency_key, "processing", nx=True, ex=86400)
    if not acquired:
        # Duplicate - return 200 OK to avoid Telegram retries
        return {"success": True, "duplicate": True, "update_id": update_id}

    try:
        # Obtener Telegram client para esta instancia
        telegram_client = await _get_telegram_client(instance_id, ctx.telegram_config.bot_token)

        # Extraer mensaje
        message = update.get("message") or update.get("edited_message")
        if not message:
            r.setex(idempotency_key, 86400, "completed")
            return {"success": True, "update_id": update_id, "ignored": "no_message"}

        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()
        telegram_user_id = message.get("from", {}).get("id")

        # =====================================================================
        # RESOLVE USER CONTEXT EARLY (needed for both commands and documents)
        # =====================================================================
        user_context = None
        if telegram_user_id:
            user_context = await resolve_user_context_from_company_context(ctx, telegram_user_id)

        # =====================================================================
        # DOCUMENT/PHOTO HANDLING (Supplier Invoice Ingestion)
        # =====================================================================
        document = message.get("document")
        photo = message.get("photo")

        if document or photo:
            # Process document/photo for supplier invoice ingestion
            if not user_context:
                if user_context is None:
                    await telegram_client.send_message(
                        chat_id=chat_id,
                        text="No tienes acceso autorizado a este asistente."
                    )
                    r.set(idempotency_key, "completed", ex=86400)
                    return {"success": True, "update_id": update_id, "unauthorized": True}

            # Get file_id and file info
            if document:
                file_id = document.get("file_id")
                filename = document.get("file_name") or "document.pdf"
                mime_type = document.get("mime_type") or "application/pdf"
            else:
                # Photo - use largest size
                largest_photo = max(photo, key=lambda p: p.get("file_size", 0))
                file_id = largest_photo.get("file_id")
                filename = "photo.jpg"
                mime_type = "image/jpeg"

            # Send processing message
            processing_msg = await telegram_client.send_message(
                chat_id=chat_id,
                text="📥 Procesando factura... (esto puede tardar unos segundos)"
            )

            try:
                # Import here to avoid circular imports
                from core.hermes.invoices import create_document_ingestion_service

                ingestion_service = create_document_ingestion_service(ctx, user_context, telegram_client)
                result = await ingestion_service.ingest(file_id, filename, mime_type)

                if result.success and result.preview_text:
                    # FASE 36: Preview ONLY - no ERP writes yet
                    # Send custom preview with disabled confirm button
                    from core.integrations.telegram.client import TelegramMessage
                    from uuid import uuid4

                    # Build inline keyboard with disabled confirm button
                    keyboard = {
                        "inline_keyboard": [
                            [
                                {"text": "🔒 Confirmar (Fase 36 - Solo Preview)", "callback_data": "disabled:fase36_preview"},
                                {"text": "❌ Cancelar", "callback_data": f"cancel:{uuid4()}"},
                            ]
                        ]
                    }

                    await telegram_client.send_message(
                        chat_id=chat_id,
                        text=result.preview_text,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
                    # Delete processing message
                    try:
                        await telegram_client.delete_message(chat_id, processing_msg.message_id)
                    except Exception:
                        pass

                else:
                    # Error - send error message
                    error_text = f"❌ {result.error}"
                    if result.error_code == "NOT_INVOICE":
                        error_text = "📄 He recibido el documento, pero no parece una factura de proveedor."
                    elif result.error_code == "MULTI_DOCUMENT":
                        error_text = "📄 El documento parece contener varios documentos. Envíalos por separado."
                    elif result.error_code == "DUPLICATE_DOCUMENT":
                        error_text = "📄 Este documento ya fue procesado anteriormente."
                    elif result.error_code == "LOCAL_MODEL_UNAVAILABLE":
                        error_text = "⚙️ El modelo local no está disponible. Inténtalo más tarde."

                    await telegram_client.edit_message_text(
                        chat_id=chat_id,
                        message_id=processing_msg.message_id,
                        text=error_text,
                    )

            except Exception as e:
                logger.error("invoice_ingestion_failed", instance_id=instance_id, error=str(e))
                await telegram_client.edit_message_text(
                    chat_id=chat_id,
                    message_id=processing_msg.message_id,
                    text="❌ Error procesando la factura. Inténtalo de nuevo.",
                )

            r.set(idempotency_key, "completed", ex=86400)
            return {"success": True, "update_id": update_id}

        # Text-only messages continue to command processing
        if not text:
            if not user_context:
                await telegram_client.send_message(chat_id=chat_id, text="No tienes acceso autorizado a este asistente.")
            else:
                await telegram_client.send_message(chat_id=chat_id, text="Solo se procesan mensajes de texto.")
            r.set(idempotency_key, "completed", ex=86400)
            return {"success": True, "update_id": update_id}

        # =====================================================================
        # ENRUTAMIENTO DE COMANDOS
        # =====================================================================
        response_text = ""

        if not user_context:
            auth_error = "No tienes acceso autorizado a este asistente."
        else:
            auth_error = None

        if text == "/start":
            response_text = (
                f"🤖 Gestor-IA - {ctx.company_name}\n\n"
                f"Instancia: {ctx.instance_id}\n\n"
                "Comandos:\n/start - Este mensaje\n/help - Ayuda\n"
                "/status - Estado\n/terceros - Listar terceros\n"
                "/facturas - Facturas de cliente\n/facturas_proveedor - Facturas de proveedor"
            )

        elif text == "/help":
            response_text = (
                "Comandos disponibles:\n"
                "/start - Inicio\n"
                "/help - Esta ayuda\n"
                "/status - Estado de la instancia\n"
                "/terceros - Listar terceros (clientes/proveedores)\n"
                "/facturas - Listar facturas de cliente\n"
                "/facturas_proveedor - Listar facturas de proveedor"
            )

        elif text == "/status":
            agents_text = ", ".join(ctx.enabled_agents) or "ninguno"
            response_text = f"Instancia: {ctx.instance_id}\nEmpresa: {ctx.company_name}\nAgentes: {agents_text}"

        elif text == "/terceros":
            if auth_error:
                response_text = "No tienes acceso autorizado a este asistente."
                # Auditoría: identidad desconocida
                if _audit_logger:
                    await _audit_logger.log_from_context(
                        ctx,
                        action=AuditActions.TELEGRAM_IDENTITY_UNKNOWN,
                        resource_type="telegram_update",
                        resource_id=str(update_id),
                        status_code=403,
                        success=False,
                        error_code="UNAUTHORIZED",
                        error_message="Telegram user not linked",
                    )
            else:
                # AUTORIZACIÓN Y EJECUCIÓN via ToolRegistry (usa AuthorizationService + CapabilityResolver)
                tool_result = await tool_registry.execute_tool(
                    instance_id=instance_id,
                    name="list_thirdparties",
                    company_context=ctx,
                    user_context=user_context,
                    limit=10,
                    page=0,
                )

                if tool_result.success:
                    response_text = await _format_thirdparties_response(
                        tool_result.data["thirdparties"],
                        tool_result.data["limit"],
                        tool_result.data["page"],
                    )
                    # Auditoría: éxito
                    if _audit_logger:
                        await _audit_logger.log_from_context(
                            ctx,
                            action="thirdparty.list",
                            resource_type="thirdparty",
                            status_code=200,
                            success=True,
                            new_state={"count": tool_result.data["count"]},
                        )
                else:
                    # Error de tool (Dolibarr, etc.)
                    response_text = tool_result.error_message or "No he podido consultar Dolibarr en este momento."
                    # Auditoría: error
                    if _audit_logger:
                        await _audit_logger.log_from_context(
                            ctx,
                            action="thirdparty.list",
                            resource_type="thirdparty",
                            status_code=500,
                            success=False,
                            error_code=tool_result.error_code,
                            error_message=tool_result.error_message,
                        )

        elif text == "/facturas":
            if auth_error:
                response_text = "No tienes acceso autorizado a este asistente."
                if _audit_logger:
                    await _audit_logger.log_from_context(
                        ctx,
                        action=AuditActions.TELEGRAM_IDENTITY_UNKNOWN,
                        resource_type="telegram_update",
                        resource_id=str(update_id),
                        status_code=403,
                        success=False,
                        error_code="UNAUTHORIZED",
                        error_message="Telegram user not linked",
                    )
            else:
                # ERP permission checked by Dolibarr via user's API key
                tool_result = await tool_registry.execute_tool(
                    instance_id=instance_id,
                    name="list_customer_invoices",
                    company_context=ctx,
                    user_context=user_context,
                    limit=10,
                    page=1,
                )

                if tool_result.success:
                    response_text = format_customer_invoices_for_telegram(
                        tool_result.data["invoices"],
                        tool_result.data["limit"],
                        tool_result.data["page"],
                    )
                    if _audit_logger:
                        await _audit_logger.log_from_context(
                            ctx,
                            action="customer_invoice.list",
                            resource_type="customer_invoice",
                            status_code=200,
                            success=True,
                            new_state={"count": tool_result.data["count"]},
                        )
                else:
                    # ToolResult.error_message already handles 401/403 from Dolibarr
                    response_text = tool_result.error_message or "No he podido consultar Dolibarr en este momento."
                    if _audit_logger:
                        await _audit_logger.log_from_context(
                            ctx,
                            action="customer_invoice.list",
                            resource_type="customer_invoice",
                            status_code=403 if tool_result.error_code == "DOLIBARR_PERMISSION_DENIED" else 500,
                            success=False,
                            error_code=tool_result.error_code,
                            error_message=tool_result.error_message,
                        )

        elif text == "/facturas_proveedor":
            if auth_error:
                response_text = "No tienes acceso autorizado a este asistente."
                if _audit_logger:
                    await _audit_logger.log_from_context(
                        ctx,
                        action=AuditActions.TELEGRAM_IDENTITY_UNKNOWN,
                        resource_type="telegram_update",
                        resource_id=str(update_id),
                        status_code=403,
                        success=False,
                        error_code="UNAUTHORIZED",
                        error_message="Telegram user not linked",
                    )
            else:
                # ERP permission checked by Dolibarr via user's API key
                tool_result = await tool_registry.execute_tool(
                    instance_id=instance_id,
                    name="list_supplier_invoices",
                    company_context=ctx,
                    user_context=user_context,
                    limit=10,
                    page=1,
                )

                if tool_result.success:
                    response_text = format_supplier_invoices_for_telegram(
                        tool_result.data["invoices"],
                        tool_result.data["limit"],
                        tool_result.data["page"],
                    )
                    if _audit_logger:
                        await _audit_logger.log_from_context(
                            ctx,
                            action="supplier_invoice.list",
                            resource_type="supplier_invoice",
                            status_code=200,
                            success=True,
                            new_state={"count": tool_result.data["count"]},
                        )
                else:
                    # ToolResult.error_message already handles 401/403 from Dolibarr
                    response_text = tool_result.error_message or "No he podido consultar Dolibarr en este momento."
                    if _audit_logger:
                        await _audit_logger.log_from_context(
                            ctx,
                            action="supplier_invoice.list",
                            resource_type="supplier_invoice",
                            status_code=403 if tool_result.error_code == "DOLIBARR_PERMISSION_DENIED" else 500,
                            success=False,
                            error_code=tool_result.error_code,
                            error_message=tool_result.error_message,
                        )

        elif text == "/productos":
            if auth_error:
                response_text = "No tienes acceso autorizado a este asistente."
                if _audit_logger:
                    await _audit_logger.log_from_context(
                        ctx,
                        action=AuditActions.TELEGRAM_IDENTITY_UNKNOWN,
                        resource_type="telegram_update",
                        resource_id=str(update_id),
                        status_code=403,
                        success=False,
                        error_code="UNAUTHORIZED",
                        error_message="Telegram user not linked",
                    )
            else:
                # ERP permission checked by Dolibarr via user's API key
                tool_result = await tool_registry.execute_tool(
                    instance_id=instance_id,
                    name="list_products",
                    company_context=ctx,
                    user_context=user_context,
                    limit=10,
                    page=1,
                    product_type="PRODUCT",
                )

                if tool_result.success:
                    response_text = format_products_for_telegram(
                        tool_result.data["products"],
                        tool_result.data["limit"],
                        tool_result.data["page"],
                        ctx.currency if hasattr(ctx, "currency") else "EUR",
                    )
                    if _audit_logger:
                        await _audit_logger.log_from_context(
                            ctx,
                            action="product.list",
                            resource_type="product",
                            status_code=200,
                            success=True,
                            new_state={"count": tool_result.data["count"]},
                        )
                else:
                    # ToolResult.error_message already handles 401/403 from Dolibarr
                    response_text = tool_result.error_message or "No he podido consultar Dolibarr en este momento."
                    if _audit_logger:
                        await _audit_logger.log_from_context(
                            ctx,
                            action="product.list",
                            resource_type="product",
                            status_code=403 if tool_result.error_code == "DOLIBARR_PERMISSION_DENIED" else 500,
                            success=False,
                            error_code=tool_result.error_code,
                            error_message=tool_result.error_message,
                        )

        elif text == "/servicios":
            if auth_error:
                response_text = "No tienes acceso autorizado a este asistente."
                if _audit_logger:
                    await _audit_logger.log_from_context(
                        ctx,
                        action=AuditActions.TELEGRAM_IDENTITY_UNKNOWN,
                        resource_type="telegram_update",
                        resource_id=str(update_id),
                        status_code=403,
                        success=False,
                        error_code="UNAUTHORIZED",
                        error_message="Telegram user not linked",
                    )
            else:
                # ERP permission checked by Dolibarr via user's API key
                tool_result = await tool_registry.execute_tool(
                    instance_id=instance_id,
                    name="list_products",
                    company_context=ctx,
                    user_context=user_context,
                    limit=10,
                    page=1,
                    product_type="SERVICE",
                )

                if tool_result.success:
                    response_text = format_products_for_telegram(
                        tool_result.data["products"],
                        tool_result.data["limit"],
                        tool_result.data["page"],
                        ctx.currency if hasattr(ctx, "currency") else "EUR",
                    )
                    if _audit_logger:
                        await _audit_logger.log_from_context(
                            ctx,
                            action="product.list",
                            resource_type="service",
                            status_code=200,
                            success=True,
                            new_state={"count": tool_result.data["count"]},
                        )
                else:
                    # ToolResult.error_message already handles 401/403 from Dolibarr
                    response_text = tool_result.error_message or "No he podido consultar Dolibarr en este momento."
                    if _audit_logger:
                        await _audit_logger.log_from_context(
                            ctx,
                            action="product.list",
                            resource_type="service",
                            status_code=403 if tool_result.error_code == "DOLIBARR_PERMISSION_DENIED" else 500,
                            success=False,
                            error_code=tool_result.error_code,
                            error_message=tool_result.error_message,
                        )

        # =====================================================================
        # QUERY LAYER V2: Procesar lenguaje natural con IntentInterpreter
        # =====================================================================
        else:
            if auth_error:
                response_text = "No tienes acceso autorizado a este asistente."
                if _audit_logger:
                    await _audit_logger.log_from_context(
                        ctx,
                        action=AuditActions.TELEGRAM_IDENTITY_UNKNOWN,
                        resource_type="telegram_update",
                        resource_id=str(update_id),
                        status_code=403,
                        success=False,
                        error_code="UNAUTHORIZED",
                        error_message="Telegram user not linked",
                    )
            else:
                # Crear intérprete específico para esta instancia (con Ollama si está configurado)
                from core.hermes.query.factory import create_interpreter_for_company_context

                instance_interpreter = create_interpreter_for_company_context(ctx)

                try:
                    # Interpretar usando estrategia parser-first + Ollama fallback
                    interpretation = await instance_interpreter.interpret(text)

                    if interpretation.status == InterpretationStatus.MATCHED and interpretation.intent:
                        action = interpretation.intent.action

                        # AUTORIZACIÓN ANTES DE EJECUTAR (solo para acciones de QUERY, no COMMAND)
                        auth_service = AuthorizationService()
                        tool_result = None

                        # Check permissions for QUERY actions
                        is_query_action = False
                        auth_service = AuthorizationService()

                        # Check permissions for QUERY actions using CapabilityResolver
                        if action in (
                            ThirdpartyAction.LIST,
                            ThirdpartyAction.SEARCH,
                            ThirdpartyAction.GET,
                            ThirdpartyAction.COUNT,
                            InvoiceAction.LIST_CUSTOMER_INVOICES,
                            InvoiceAction.SEARCH_CUSTOMER_INVOICES,
                            InvoiceAction.GET_CUSTOMER_INVOICE,
                            InvoiceAction.COUNT_CUSTOMER_INVOICES,
                            InvoiceAction.LIST_SUPPLIER_INVOICES,
                            InvoiceAction.SEARCH_SUPPLIER_INVOICES,
                            InvoiceAction.GET_SUPPLIER_INVOICE,
                            InvoiceAction.COUNT_SUPPLIER_INVOICES,
                        ):
                            is_query_action = True

                        # EJECUTAR TOOL o INSIGHT via Hermes
                        if action is not None:
                            if action in (
                                ThirdpartyAction.LIST,
                                ThirdpartyAction.SEARCH,
                                ThirdpartyAction.GET,
                                ThirdpartyAction.COUNT,
                            ):
                                # Thirdparty actions -> ToolRegistry
                                tool_name, tool_params = structured_intent_to_tool_call(interpretation.intent)
                                tool_result = await tool_registry.execute_tool(
                                    instance_id=instance_id,
                                    name=tool_name,
                                    company_context=ctx,
                                    user_context=user_context,
                                    **tool_params,
                                )

                            elif action in (
                                InvoiceAction.LIST_CUSTOMER_INVOICES,
                                InvoiceAction.SEARCH_CUSTOMER_INVOICES,
                                InvoiceAction.GET_CUSTOMER_INVOICE,
                                InvoiceAction.COUNT_CUSTOMER_INVOICES,
                                InvoiceAction.LIST_SUPPLIER_INVOICES,
                                InvoiceAction.SEARCH_SUPPLIER_INVOICES,
                                InvoiceAction.GET_SUPPLIER_INVOICE,
                                InvoiceAction.COUNT_SUPPLIER_INVOICES,
                            ):
                                # Invoice actions -> ToolRegistry
                                tool_name, tool_params = structured_intent_to_tool_call(interpretation.intent)
                                tool_result = await tool_registry.execute_tool(
                                    instance_id=instance_id,
                                    name=tool_name,
                                    company_context=ctx,
                                    user_context=user_context,
                                    **tool_params,
                                )

                            elif action in (
                                InsightAction.CUSTOMER_INVOICE_SUMMARY,
                                InsightAction.CUSTOMER_OUTSTANDING_SUMMARY,
                                InsightAction.CUSTOMER_OUTSTANDING_BY_THIRDPARTY,
                                InsightAction.CUSTOMER_INVOICE_SUMMARY_BY_THIRDPARTY,
                            ):
                                # Customer Insight actions -> CustomerFinanceInsightService
                                tool_result = await execute_customer_insight(
                                    company_context=ctx,
                                    user_context=user_context,
                                    action=action.value,
                                    args=interpretation.intent.arguments.__dict__,
                                )

                            elif action in (
                                InsightAction.SUPPLIER_INVOICE_SUMMARY,
                                InsightAction.SUPPLIER_OUTSTANDING_SUMMARY,
                                InsightAction.SUPPLIER_OUTSTANDING_BY_THIRDPARTY,
                                InsightAction.SUPPLIER_INVOICE_SUMMARY_BY_THIRDPARTY,
                            ):
                                # Supplier Insight actions -> SupplierFinanceInsightService
                                tool_result = await execute_supplier_insight(
                                    company_context=ctx,
                                    user_context=user_context,
                                    action=action.value,
                                    args=interpretation.intent.arguments.__dict__,
                                )

                            elif action in (
                                CommandAction.CREATE_THIRDPARTY,
                                CommandAction.CREATE_PRODUCT,
                                CommandAction.CREATE_SERVICE,
                            ):
                                # Command actions -> CommandExecutor (preview + confirmation)
                                from core.hermes.commands.models import CommandIntent, CommandType

                                command_type = CommandType(action.value)

                                command_intent = CommandIntent(
                                    command_type=command_type,
                                    payload=interpretation.intent.arguments.__dict__,
                                    instance_id=ctx.instance_id,
                                    telegram_user_id=user_context.telegram_user_id,
                                    dolibarr_user_id=user_context.dolibarr_user_id,
                                    request_id=str(update_id),
                                )

                                audit_logger = create_audit_logger(instance_config=ctx.instance_config)
                                store = PendingCommandStore(ctx.instance_id)

                                executor = CommandExecutor(
                                    registry=command_registry,
                                    store=store,
                                    audit_logger=audit_logger,
                                    company_context=ctx,
                                    user_context=user_context,
                                )

                                try:
                                    preview = await executor.preview(command_intent)
                                except PermissionError as e:
                                    logger.warning("command_preview_denied", instance_id=instance_id, error=str(e))
                                    response_text = f"❌ {e}"
                                    if _audit_logger:
                                        await _audit_logger.log_from_context(
                                            ctx,
                                            action="command.preview.denied",
                                            resource_type=action.value,
                                            status_code=403,
                                            success=False,
                                            error_code="PERMISSION_DENIED",
                                            error_message=str(e),
                                        )
                                except Exception as e:
                                    logger.error("command_preview_failed", instance_id=instance_id, error=str(e), exc_info=True)
                                    response_text = "Error interno generando preview."
                                    if _audit_logger:
                                        await _audit_logger.log_from_context(
                                            ctx,
                                            action="command.preview.error",
                                            resource_type=action.value,
                                            status_code=500,
                                            success=False,
                                            error_code="INTERNAL_ERROR",
                                            error_message=str(e),
                                        )
                                else:
                                    await send_command_preview(
                                        telegram=telegram_client,
                                        chat_id=chat_id,
                                        preview=preview,
                                    )
                                    response_text = ""

                            else:
                                response_text = "Acción no soportada."
                                tool_result = None

                            # For command actions, preview was already sent, no tool_result
                            if tool_result is not None and tool_result.success:
                                # Formatear respuesta según tipo de intent
                                action = interpretation.intent.action
                                if action == ThirdpartyAction.LIST:
                                    response_text = format_thirdparties_for_telegram(
                                        tool_result.data["thirdparties"],
                                        tool_result.data["limit"],
                                        tool_result.data["page"],
                                    )
                                elif action == ThirdpartyAction.SEARCH:
                                    response_text = format_thirdparties_for_telegram(
                                        tool_result.data["thirdparties"],
                                        tool_result.data["limit"],
                                        tool_result.data["page"],
                                    )
                                elif action == ThirdpartyAction.GET:
                                    response_text = format_thirdparty_detail_for_telegram(
                                        tool_result.data["thirdparty"]
                                    )
                                elif action == ThirdpartyAction.COUNT:
                                    party_type = interpretation.intent.arguments.party_type
                                    response_text = format_count_for_telegram(
                                        tool_result.data["count"],
                                        party_type,
                                    )
                                # Customer Invoice actions
                                elif action == InvoiceAction.LIST_CUSTOMER_INVOICES:
                                    response_text = format_customer_invoices_for_telegram(
                                        tool_result.data["invoices"],
                                        tool_result.data["limit"],
                                        tool_result.data["page"],
                                    )
                                elif action == InvoiceAction.SEARCH_CUSTOMER_INVOICES:
                                    response_text = format_customer_invoices_for_telegram(
                                        tool_result.data["invoices"],
                                        tool_result.data["limit"],
                                        tool_result.data["page"],
                                    )
                                elif action == InvoiceAction.GET_CUSTOMER_INVOICE:
                                    response_text = format_customer_invoice_detail_for_telegram(
                                        tool_result.data["invoice"]
                                    )
                                elif action == InvoiceAction.COUNT_CUSTOMER_INVOICES:
                                    response_text = format_invoice_count_for_telegram(
                                        tool_result.data["count"],
                                        InvoicePartyType.CUSTOMER,
                                    )
                                # Supplier Invoice actions
                                elif action == InvoiceAction.LIST_SUPPLIER_INVOICES:
                                    response_text = format_supplier_invoices_for_telegram(
                                        tool_result.data["invoices"],
                                        tool_result.data["limit"],
                                        tool_result.data["page"],
                                    )
                                elif action == InvoiceAction.SEARCH_SUPPLIER_INVOICES:
                                    response_text = format_supplier_invoices_for_telegram(
                                        tool_result.data["invoices"],
                                        tool_result.data["limit"],
                                        tool_result.data["page"],
                                    )
                                elif action == InvoiceAction.GET_SUPPLIER_INVOICE:
                                    response_text = format_supplier_invoice_detail_for_telegram(
                                        tool_result.data["invoice"]
                                    )
                                elif action == InvoiceAction.COUNT_SUPPLIER_INVOICES:
                                    response_text = format_invoice_count_for_telegram(
                                        tool_result.data["count"],
                                        InvoicePartyType.SUPPLIER,
                                    )
                                # Customer Insight actions
                                elif action == InsightAction.CUSTOMER_INVOICE_SUMMARY:
                                    response_text = format_customer_invoice_summary_for_telegram(tool_result.data)
                                elif action == InsightAction.CUSTOMER_OUTSTANDING_SUMMARY:
                                    response_text = format_customer_outstanding_summary_for_telegram(tool_result.data)
                                elif action == InsightAction.CUSTOMER_OUTSTANDING_BY_THIRDPARTY:
                                    response_text = format_customer_outstanding_by_thirdparty_for_telegram(
                                        tool_result.data
                                    )
                                elif action == InsightAction.CUSTOMER_INVOICE_SUMMARY_BY_THIRDPARTY:
                                    response_text = format_customer_invoice_summary_by_thirdparty_for_telegram(
                                        tool_result.data
                                    )
                                # Supplier Insight actions
                                elif action == InsightAction.SUPPLIER_INVOICE_SUMMARY:
                                    response_text = format_supplier_invoice_summary_for_telegram(tool_result.data)
                                elif action == InsightAction.SUPPLIER_OUTSTANDING_SUMMARY:
                                    response_text = format_supplier_outstanding_summary_for_telegram(tool_result.data)
                                elif action == InsightAction.SUPPLIER_OUTSTANDING_BY_THIRDPARTY:
                                    response_text = format_supplier_outstanding_by_thirdparty_for_telegram(
                                        tool_result.data
                                    )
                                elif action == InsightAction.SUPPLIER_INVOICE_SUMMARY_BY_THIRDPARTY:
                                    response_text = format_supplier_invoice_summary_by_thirdparty_for_telegram(
                                        tool_result.data
                                    )
                                else:
                                    response_text = "Consulta procesada correctamente."

                                if _audit_logger:
                                    await _audit_logger.log_from_context(
                                        ctx,
                                        action=f"thirdparty.{action.value}",
                                        resource_type="thirdparty",
                                        status_code=200,
                                        success=True,
                                        new_state={
                                            "count": tool_result.data.get(
                                                "count", len(tool_result.data.get("thirdparties", []))
                                            )
                                        },
                                    )
                            else:
                                if tool_result is not None:
                                    response_text = (
                                        tool_result.error_message or "No he podido consultar Dolibarr en este momento."
                                    )
                                    if _audit_logger:
                                        await _audit_logger.log_from_context(
                                            ctx,
                                            action=f"thirdparty.{interpretation.intent.action.value}",
                                            resource_type="thirdparty",
                                            status_code=500,
                                            success=False,
                                            error_code=tool_result.error_code,
                                            error_message=tool_result.error_message,
                                        )
                                else:
                                    # Command action - preview already sent
                                    response_text = ""
                    elif interpretation.status == InterpretationStatus.NEEDS_CLARIFICATION:
                        response_text = interpretation.clarification_message or (
                            "No he entendido la consulta completamente. "
                            "Intenta: 'lista clientes', 'busca cliente NOMBRE', 'cuántos proveedores hay'."
                        )
                        if _audit_logger:
                            await _audit_logger.log_from_context(
                                ctx,
                                action="thirdparty.clarification_needed",
                                resource_type="thirdparty",
                                status_code=400,
                                success=False,
                                error_code="NEEDS_CLARIFICATION",
                                error_message=interpretation.clarification_message,
                            )
                    else:
                        # NO_MATCH, INVALID_OUTPUT, PROVIDER_ERROR
                        response_text = (
                            f"No he entendido la consulta: {text}\n\n"
                            f"Intenta:\n"
                            f'• "lista clientes"\n'
                            f'• "busca cliente ACME"\n'
                            f'• "cuántos proveedores hay"\n'
                            f'• "muestra el tercero 42"'
                        )
                        if _audit_logger:
                            await _audit_logger.log_from_context(
                                ctx,
                                action="thirdparty.no_match",
                                resource_type="thirdparty",
                                status_code=400,
                                success=False,
                                error_code="NO_MATCH",
                                error_message=(
                                    f"Interpreter: {interpretation.interpreter_used}, "
                                    f"status: {interpretation.status.value}"
                                ),
                            )

                finally:
                    await instance_interpreter.aclose()

        # Enviar respuesta (no romper webhook si falla Telegram)
        if response_text:
            try:
                await telegram_client.send_message(chat_id=chat_id, text=response_text, parse_mode=None)
            except Exception as e:
                logger.warning("telegram_send_failed", chat_id=chat_id, error=str(e))
                # No re-lanzar: webhook debe responder 200 a Telegram

        # Marcar completado (actualizar valor manteniendo TTL)
        r.set(idempotency_key, "completed", ex=86400)

        return {"success": True, "update_id": update_id}

    except Exception as e:
        import traceback
        logger.error("webhook_processing_failed", instance_id=instance_id, error=str(e), traceback=traceback.format_exc())
        # On error, delete key to allow retry (but only if we still own it)
        # Note: In production, consider using a Lua script for atomic check-and-delete
        r.delete(idempotency_key)  # Permitir reintento
        raise HTTPException(500, "Processing failed")


# =========================================================================
# TELEGRAM CALLBACK QUERY ENDPOINT (Command Confirmations)
# =========================================================================


@app.post("/webhook/{instance_id}/callback", tags=["Telegram"])
@app.post("/webhook/{instance_id}/callback/", tags=["Telegram"])
async def telegram_callback(
    instance_id: str,
    request: Request,
    ctx: CompanyContext = Depends(get_company_context),
):
    """
    Callback query handler for command confirmations.

    Handles inline keyboard callbacks: confirm:<command_id> / cancel:<command_id>
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

    # Parse update (use cached body from get_company_context dependency)
    update = getattr(request.state, "telegram_update", None)
    if update is None:
        raise HTTPException(400, "Request body not cached - dependency order issue")

    callback_query = update.get("callback_query")
    if not callback_query:
        raise HTTPException(400, "No callback query")

    callback_data = callback_query.get("data")
    if not callback_data:
        raise HTTPException(400, "No callback data")

    telegram_user_id = callback_query.get("from", {}).get("id")
    if not telegram_user_id:
        raise HTTPException(400, "No user ID in callback")

    chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
    message_id = callback_query.get("message", {}).get("message_id")

    if not chat_id or not message_id:
        raise HTTPException(400, "Invalid callback message")

    # Idempotency check for callback
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

    callback_id = callback_query.get("id")
    callback_idempotency_key = f"telegram:callback:{callback_id}"

    # ATOMIC: SET NX EX
    acquired = r.set(callback_idempotency_key, "processing", nx=True, ex=86400)
    if not acquired:
        # Duplicate callback - answer with 200 to avoid Telegram retries
        return {"success": True, "duplicate": True}

    try:
        # Resolver identidad -> UserContext
        user_context = None
        if telegram_user_id:
            try:
                user_context = await get_user_context(request)
            except HTTPException:
                pass  # Will be handled by executor

        if not user_context:
            # Answer callback to remove loading state
            telegram_client = await _get_telegram_client(instance_id, ctx.telegram_config.bot_token)
            await telegram_client.answer_callback_query(callback_query["id"], text="No autorizado", show_alert=True)
            r.set(callback_idempotency_key, "completed", ex=86400)
            return {"success": True, "unauthorized": True}

        # Crear executor y manejar callback
        telegram_client = await _get_telegram_client(instance_id, ctx.telegram_config.bot_token)

        # Create instance-specific audit logger
        audit_logger = create_audit_logger(instance_config=ctx.instance_config)

        # Create pending command store
        store = PendingCommandStore(ctx.instance_id)

        executor = CommandExecutor(
            registry=command_registry,
            store=store,
            audit_logger=audit_logger,
            company_context=ctx,
            user_context=user_context,
        )

        await handle_command_callback(
            executor=executor,
            telegram=telegram_client,
            chat_id=chat_id,
            message_id=message_id,
            callback_data=callback_data,
            telegram_user_id=telegram_user_id,
        )

        # Answer callback query (remove loading state)
        await telegram_client.answer_callback_query(callback_query["id"])

        r.set(callback_idempotency_key, "completed", ex=86400)
        return {"success": True}

    except Exception as e:
        logger.error("callback_processing_failed", instance_id=instance_id, error=str(e))
        r.delete(callback_idempotency_key)  # Permitir reintento
        raise HTTPException(500, "Processing failed")


# =========================================================================
# DOLIBARR PROXY ENDPOINTS (Por instancia)
# =========================================================================


@app.get("/api/{instance_id}/dolibarr/thirdparties", tags=["Dolibarr"])
async def list_thirdparties(
    instance_id: str,
    user_context: UserContext = Depends(RequirePermission("thirdparty.read")),
    ctx: CompanyContext = Depends(get_company_context),
    limit: int = 100,
    offset: int = 0,
):
    """Listar terceros de Dolibarr para la instancia (requiere thirdparty.read)."""
    if ctx.instance_id != instance_id:
        raise HTTPException(400, "Instance ID mismatch")

    # Verify cross-instance consistency
    if user_context.instance_id != instance_id:
        raise HTTPException(403, "Cross-instance access denied")

    client = DolibarrClient.from_instance_config(
        ctx.dolibarr_config,
        db_host=ctx.instance_config.database.host,
        db_port=ctx.instance_config.database.port,
        db_name=ctx.instance_config.database.name,
        db_user=ctx.instance_config.database.user,
        db_password=ctx.instance_config.database.password,
    )
    async with client as c:
        return await c.list_thirdparties(limit=limit, offset=offset)


@app.post("/api/{instance_id}/dolibarr/thirdparties", tags=["Dolibarr"])
async def create_thirdparty(
    instance_id: str,
    data: dict,
    user_context: UserContext = Depends(RequirePermission("thirdparty.create")),
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
    user_context: UserContext = Depends(RequirePermission("ai.use")),
    ctx: CompanyContext = Depends(get_company_context),
):
    """Generar texto con IA según política de la instancia (requiere ai.use)."""
    if ctx.instance_id != instance_id:
        raise HTTPException(400, "Instance ID mismatch")
    if user_context.instance_id != instance_id:
        raise HTTPException(403, "Cross-instance access denied")

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
    user_context: UserContext = Depends(RequirePermission("audit.read")),
    ctx: CompanyContext = Depends(get_company_context),
    action: str = None,
    resource_type: str = None,
    limit: int = 100,
    offset: int = 0,
):
    """Consultar logs de auditoría de la instancia (requiere audit.read)."""
    if ctx.instance_id != instance_id:
        raise HTTPException(400, "Instance ID mismatch")
    if user_context.instance_id != instance_id:
        raise HTTPException(403, "Cross-instance access denied")

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
    user_context: UserContext = Depends(require_authenticated_user),
    ctx: CompanyContext = Depends(get_company_context),
):
    """Listar extensiones disponibles para la instancia (requiere autenticación)."""
    if ctx.instance_id != instance_id:
        raise HTTPException(400, "Instance ID mismatch")
    if user_context.instance_id != instance_id:
        raise HTTPException(403, "Cross-instance access denied")

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

    uvicorn.run("core.hermes.main:app", host="0.0.0.0", port=8000, reload=True)
