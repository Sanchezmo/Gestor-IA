# Auditoría Arquitectónica: Transvega Animal → Gestor-IA

**Fecha**: 2026-08-22
**Repositorio origen**: `/home/saulo/transvega-animal` (solo lectura)
**Repositorio destino**: `/home/saulo/Gestor-IA`

---

## 1. Mapa Arquitectónico de Transvega Animal

### 1.1 Estructura de Servicios (Microservicios)

```
transvega-animal/
├── services/
│   ├── integration-api/     # FastAPI - Punto central Hermes/Dolibarr/Telegram
│   ├── approval-service/    # FastAPI - Workflows de aprobación humana
│   ├── audit-service/       # Python - Auditoría inmutable (hash chain)
│   ├── task-queue/          # Celery + Redis - Tareas asíncronas periódicas
│   └── dashboard/           # Frontend React (separado)
├── adapters/
│   ├── dolibarr/            # Cliente REST genérico + mappers
│   ├── cloudflare/          # Manager completo (DNS, Access, Tunnels, WAF)
│   ├── telegram/            # Cliente Bot API (en integration-api/core)
│   ├── google-workspace/    # Cliente Gmail/Drive
│   ├── advertising-platforms/ # Milanuncios, etc.
│   └── verifactu/           # Adapter VeriFactu
├── agents/                  # 22 agentes especializados
│   ├── supervisor/          # Orquestador principal
│   ├── dog_intake/          # Ingesta de perros (DOMINIO ESPECÍFICO)
│   ├── invoice_processing/  # OCR + validación facturas proveedor
│   ├── listing/             # Publicaciones Milanuncios
│   ├── publishing/          # Publicación con aprobaciones
│   ├── media_pipeline/      # Ingesta → Análisis → Selección → Variantes
│   ├── content_marketing/   # Generación contenido marketing
│   ├── sales/               # Ventas
│   ├── purchases/           # Compras
│   ├── accounting/          # Contabilidad
│   ├── banking/             # Banca
│   ├── tax/                 # Impuestos
│   ├── marketing/           # Marketing
│   ├── compliance/          # Cumplimiento
│   ├── technical/           # Técnico
│   ├── products/            # Productos
│   ├── invoicing/           # Facturación
│   ├── expedientes/         # Expedientes
│   └── media_generation/    # Generación media IA
├── infrastructure/
│   ├── docker/              # Dockerfiles (SOLO tests/CI)
│   ├── monitoring/          # Prometheus/Grafana/Loki
│   └── ollama/              # Modelfile
├── scripts/
│   ├── install/             # Instalación nativa por componente
│   ├── configure/           # Configuración post-instalación
│   ├── services/            # systemd start/stop/restart/status
│   └── backup/              # Backup/restore
├── config/                  # Configs Apache, MariaDB, PostgreSQL, Redis, systemd
├── skills/                  # Milanuncios-bot (skill externo)
├── tests/                   # Unit + Integration + E2E + Security
├── Makefile                 # Interfaz completa nativa
├── .env.example             # Template exhaustivo
├── pyproject.toml           # Ruff, MyPy, Pytest config
└── docker-compose*.yml      # SOLO para tests/CI
```

### 1.2 Flujo de Datos Principal

```
Telegram Webhook
       │
       ▼
┌─────────────────────────────────────┐
│  integration-api (FastAPI)          │
│  ├── /api/v1/telegram/webhook       │
│  ├── /api/v1/telegram/media         │
│  └── /api/v1/{terceros,productos...}│
└─────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────┐
│  SupervisorAgent (Orquestador)      │
│  ├── ConversationManager (Redis)    │
│  ├── DogIntakeAgent                 │
│  ├── InvoiceProcessingAgent         │
│  ├── MediaPipelineAgent             │
│  ├── ContentMarketingAgent          │
│  ├── PublishingAgent                │
│  └── ListingAgent                   │
└─────────────────────────────────────┘
       │
       ├──────────────────┬──────────────────┐
       ▼                  ▼                  ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Dolibarr   │    │   Ollama    │    │  NVIDIA     │
│  REST API   │    │  (Local)    │    │  (Cloud)    │
└─────────────┘    └─────────────┘    └─────────────┘
       │
       ▼
┌─────────────┐
│ MariaDB     │
│ (Dolibarr)  │
└─────────────┘
```

### 1.3 Base de Datos

| Servicio | BD | Propósito |
|----------|-----|-----------|
| Dolibarr | MariaDB | ERP (terceros, facturas, productos, expedientes animal) |
| Auditoría | PostgreSQL | Log inmutable con hash chain (audit-service) |
| Colas/Cache/Sesiones | Redis | Celery broker + result backend + ConversationManager + Idempotencia |

---

## 2. Clasificación de Componentes

### 2.1 REUTILIZAR (Código genérico, probado, directamente portable)

| Componente | Archivo(s) | Justificación |
|------------|------------|---------------|
| **DolibarrClient** | `adapters/dolibarr/client.py` | Cliente REST genérico, sin lógica de negocio animal. Recibe `base_url` y `api_key` por constructor. |
| **Dolibarr Mappers (base)** | `adapters/dolibarr/mappers.py` | Mapeo genérico thirdparty/product/invoice. Adaptar esquemas. |
| **CloudflareAdapter** | `adapters/cloudflare/manager.py` | API completa: DNS, Access, Tunnels, WAF, SSL, Workers. Genérico. |
| **CloudflareManager** | `adapters/cloudflare/manager.py` | Alto nivel: setup DNS, Access, Tunnel ingress. Genérico. |
| **TelegramClient** | `integration-api/app/core/telegram_client.py` | Abstracción limpia Bot API. Mock incluido para tests. |
| **ModelRouter / AI Providers** | `integration-api/app/core/model_router.py` | LOCAL_ONLY vs CLOUD_ALLOWED. OllamaProvider, NvidiaProvider. |
| **ConversationManager** | `integration-api/app/services/conversation_manager.py` | Sesiones Redis con TTL, workflow state, pending_media. Genérico. |
| **AuditLogger (core)** | `integration-api/app/services/audit_logger.py` | Log inmutable PostgreSQL con diff, hash chain. |
| **AuditService (base)** | `audit-service/app.py` | Estructura de auditoría, verificación integridad. |
| **Settings/Config (patrón)** | `integration-api/app/core/config.py` | Pydantic Settings con `.env.local`, computed URLs, agent API keys. |
| **Database setup** | `integration-api/app/core/database.py` | SQLAlchemy async + asyncpg + Redis. Pool, lifecycle. |
| **Makefile (patrón)** | `Makefile` | Targets: install, configure, start, stop, status, check, backup, restore, test, lint, format, type-check. |
| **Scripts install/configure** | `scripts/install/*.sh`, `scripts/configure/*.sh` | Idempotentes, validan .env, detectan PROJECT_ROOT, systemd. |
| **Systemd service templates** | `config/systemd/*.service` | Servicios nativos: hermes, hermes-worker, approvals, ollama, cloudflared. |
| **Dolibarr health check** | `scripts/dolibarr-health.sh` | Verificación granular endpoints. |
| **Test infrastructure** | `tests/conftest.py`, `pyproject.toml` | Pytest-asyncio, fixtures, ruff, mypy strict config. |

---

### 2.2 ADAPTAR (Código útil pero requiere cambios para multi-instancia/genérico)

| Componente | Archivo(s) | Cambios Requeridos |
|------------|------------|-------------------|
| **SupervisorAgent** | `agents/supervisor/agent.py` | Extraer lógica de orquestación genérica. Separar `DogIntakeAgent` y `InvoiceProcessingAgent` como plugins por instancia. Inyectar `CompanyContext`/`InstanceConfig`. |
| **InvoiceProcessingAgent** | `agents/invoice_processing/agent.py` | Genérico para facturas proveedor (válido para cualquier empresa). Quitar referencias hardcoded a dog/animal. |
| **MediaPipelineAgent** | `agents/media_pipeline/agent.py` | Genérico para ingesta/análisis/selección media. Quitar prompts específicos de perros. |
| **ContentMarketingAgent** | `agents/content_marketing/agent.py` | Genérico para generar contenido. Prompts como templates configurables por instancia. |
| **PublishingAgent** | `agents/publishing/agent.py` | Genérico para workflow aprobación→publicación. Canales (Milanuncios, web, etc.) como plugins. |
| **ListingAgent** | `agents/listing/agent.py` | Genérico para crear listings. Plataformas como adapters. |
| **Telegram routes** | `integration-api/app/routes/telegram.py` | Extraer webhook genérico. Routing a agentes por `InstanceConfig`. Webhook path configurable. |
| **Dolibarr dependency** | `integration-api/app/dependencies/dolibarr.py` | Recibir `InstanceConfig` (base_url, api_key) en lugar de settings globales. |
| **Settings/Config** | `integration-api/app/core/config.py` | Separar: `GlobalSettings` (infra compartida) + `InstanceConfig` (por empresa). |
| **Celery app** | `task-queue/app/celery_app.py` | Colas por instancia (`instance:{id}:high`, `instance:{id}:default`). Worker genérico. |
| **Approval service** | `approval-service/app/` | Genérico. Workflows de aprobación configurables. |
| **Backup scripts** | `scripts/backup/*.sh` | Backup por instancia (DB + documentos + config + metadata). |
| **Cloudflare ingress generation** | `scripts/configure/cloudflare.sh` | Generar ingress dinámico desde `InstanceConfig` de todas las instancias activas. |
| **Dolibarr configure** | `scripts/configure/dolibarr.sh` | Instalación por instancia (DB propia, usuario propio, conf.php propio, documents propio). |

---

### 2.3 REESCRIBIR (Lógica de negocio específica de Transvega → FUERA del Core)

| Componente | Archivo(s) | Destino en Gestor-IA |
|------------|------------|---------------------|
| **DogIntakeAgent** | `agents/dog_intake/agent.py` | `companies/transvega/agents/dog_intake/` |
| **Dog schemas** | `services/integration-api/app/schemas/conversation.py` (parcial) | `companies/transvega/schemas/` |
| **Expedientes animal (Dolibarr module)** | `adapters/dolibarr/client.py` (métodos expedientes_animal) | `companies/transvega/integrations/dolibarr/` |
| **Milanuncios publishing** | `agents/listing/agent.py`, `agents/publishing/agent.py`, `skills/milanuncios-bot/` | `companies/transvega/integrations/milanuncios/` |
| **Animal transport logic** | Múltiples agentes | `companies/transvega/workflows/` |
| **Dog-specific prompts** | `agents/content_marketing/agent.py`, `agents/media_pipeline/agent.py` | `companies/transvega/prompts/` |
| **VeriFactu adapter** | `adapters/verifactu/adapter.py` | `companies/transvega/integrations/verifactu/` (si solo Transvega lo usa) |
| **Google Workspace accounts** | `.env.example` (cuentas funcionales ventas@, administracion@, etc.) | `InstanceConfig` por empresa |
| **NVIDIA provider (hardcoded models)** | `model_router.py` | Configurable via `AIProvider` + `AIPolicy` |

---

### 2.4 DESCARTAR (Deuda técnica, trabajo temporal, obsoleto)

| Componente | Archivo(s) | Razón |
|------------|------------|-------|
| **MockDolibarr** | `adapters/dolibarr/mock.py`, `docker-compose.test.yml` mock-dolibarr | Solo para tests. En Gestor-IA usar testcontainers o DB real de test. |
| **Docker Compose (prod/staging/dev)** | `docker-compose*.yml` | Runtime nativo (systemd). Docker solo para CI/tests. |
| **Kubernetes/Helm** | No existe (bueno) | N/A |
| **Prometheus/Grafana/Loki Docker** | `infrastructure/monitoring/` | Opcional. Si se usa, nativo o externo. |
| **Skills/milanuncios-bot** | `skills/milanuncios-bot/` | Específico de Transvega → instancia. |
| **VeriFactu** | `adapters/verifactu/` | Solo si Transvega lo requiere. |
| **Google Workspace client** | `adapters/google-workspace/client.py` | Solo si empresa lo usa. Adapter opcional. |
| **Advertising platforms manager** | `adapters/advertising-platforms/manager.py` | Genérico pero incompleto. Reescribir como adapter plugin. |
| **Dashboard React** | `services/dashboard/` | Fuera de scope core. Cada instancia decide su UI. |
| **Approval service (separado)** | `services/approval-service/` | Integrar en Core como módulo, no microservicio separado. |
| **Audit service (separado)** | `services/audit-service/app.py` | Integrar en Core. `AuditLogger` ya existe en integration-api. |
| **Task queue (separado)** | `services/task-queue/` | Integrar en Core. `JobQueue` abstraction + Celery opcional. |
| **Hardcoded paths** | `/home/saulo/transvega-animal` en scripts | Usar `PROJECT_ROOT` detectado dinámicamente. |

---

## 3. Decisiones Críticas para Gestor-IA

### 3.1 PostgreSQL
| Análisis | Decisión |
|----------|----------|
| **Función actual**: Auditoría inmutable (hash chain) en `audit-service` y `integration-api/app/services/audit_logger.py` | **DESCARTAR PostgreSQL obligatorio** |
| **Datos almacenados**: audit_log (request/response, diff, hashes, cadena integridad) | **ALTERNATIVA**: MariaDB (misma instancia server) con tabla `audit_log` + JSON columns. MariaDB 10.6+ soporta JSON, generated columns, constraints. |
| **Dependencias**: asyncpg, SQLAlchemy async, Alembic | **RECOMENDACIÓN**: Usar MariaDB para todo. Un solo server BD, múltiples schemas/DBs. Simplifica backup, operaciones, infra. |
| **¿Necesario en Gestor-IA?** | **NO** como requisito obligatorio. Opcional si empresa ya tiene PostgreSQL y prefiere segregación física. |

### 3.2 Redis
| Análisis | Decisión |
|----------|----------|
| **Función actual**: Celery broker/result, ConversationManager sessions, Idempotency keys, Rate limiting | **MANTENER Redis** |
| **Dependencias**: redis-py, Celery | **JUSTIFICACIÓN**: Redis es ligero, nativo, estándar para colas + cache + sessions. No reemplazar por MariaDB (no es cola nativa). |
| **¿Necesario en Gestor-IA?** | **SÍ**. Compartido entre instancias (DB 0, 1, 2... por instancia). |

### 3.3 Celery / Jobs
| Análisis | Decisión |
|----------|----------|
| **Función actual**: Tareas periódicas (expedientes, publicaciones, facturación, notificaciones), colas priorizadas | **POSPONER Celery** |
| **Complejidad**: Requiere Redis + worker processes + beat scheduler + monitoring | **PRIMERA FASE**: Abstracción `JobQueue` + `JobHandler` en memoria/simple. Implementar `CeleryJobQueue` después si carga real lo justifica. |
| **Alternativas**: `asyncio` tasks + APScheduler para periódicas, `redis-py` para colas simples | **RECOMENDACIÓN**: Empezar sin Celery. Añadir cuando haya >10 tareas concurrentes reales. |

---

## 4. Arquitectura Objetivo: Gestor-IA Core

### 4.1 Principios Innegociables (del Prompt Maestro)

1. **Multiempresa = Multiinstancia** (NO Dolibarr MultiCompany)
2. **Cada empresa = Instancia independiente** (Dolibarr, DB, usuario, Telegram, dominio, workflows, agents, AI policy)
3. **Hermes Core = Compartido y Genérico** (sin conocimiento de dominio)
4. **CompanyContext** acompaña TODA operación empresarial
5. **InstanceConfig** modelo explícito por empresa
6. **AIProvider abstraction** (Ollama, NVIDIA, OpenAI, etc.)
7. **AIPolicy** decide LOCAL vs CLOUD por task/sensitivity
8. **Cloudflare Tunnel compartido** inicialmente, routing por hostname
9. **Dominios independientes** por empresa (configurables)
10. **Runtime nativo on-premise** (systemd, Apache, MariaDB, Ollama, cloudflared)
11. **Docker NO requisito producción** (solo tests/CI)
12. **Tests de aislamiento prioritarios** (Empresa A ≠ Empresa B)

### 4.2 Estructura de Directorios Propuesta

```
Gestor-IA/
├── core/
│   ├── hermes/
│   │   ├── __init__.py
│   │   ├── config.py           # GlobalSettings + InstanceConfig models
│   │   ├── context.py          # CompanyContext (request-scoped)
│   │   ├── resolver.py         # InstanceResolver (header/domain/token → InstanceConfig)
│   │   ├── security.py         # AuthZ, secrets, API keys per instance
│   │   ├── audit.py            # AuditLogger + AuditEvent models
│   │   ├── jobs.py             # JobQueue abstraction + JobHandler
│   │   ├── ai.py               # AIProvider (base) + OllamaProvider + NvidiaProvider
│   │   ├── policy.py           # AIPolicy (instance, task, sensitivity → provider)
│   │   ├── telegram.py         # TelegramClient + InstanceTelegramRouter
│   │   └── extensions.py       # Extension registry (agents, tools, workflows per instance)
│   ├── integrations/
│   │   ├── dolibarr/
│   │   │   ├── client.py       # DolibarrClient (REUSE from Transvega)
│   │   │   ├── mappers.py      # Base mappers (ADAPT)
│   │   │   └── dependency.py   # FastAPI dependency injecting InstanceConfig
│   │   ├── cloudflare/
│   │   │   ├── adapter.py      # CloudflareAdapter (REUSE)
│   │   │   ├── manager.py      # CloudflareManager (REUSE)
│   │   │   └── ingress.py      # Dynamic ingress generator from instances
│   │   └── ai/
│   │       ├── providers.py    # AIProvider implementations
│   │       └── router.py       # ModelRouter (REUSE/ADAPT)
│   └── events/                 # Event bus for cross-instance (optional)
├── companies/                  # Instance-specific code (OUTSIDE core)
│   ├── transvega/
│   │   ├── config/
│   │   ├── agents/
│   │   ├── tools/
│   │   ├── workflows/
│   │   └── prompts/
│   └── example/
├── instances/                  # Runtime instance configs (generated)
│   ├── transvega/
│   │   ├── config.yml
│   │   ├── instance.env
│   │   └── secrets/ (gitignored)
│   └── example/
├── infrastructure/
│   ├── apache/
│   ├── systemd/
│   ├── cloudflare/
│   └── database/
├── scripts/
│   ├── install/
│   ├── configure/
│   ├── instance/
│   ├── services/
│   └── backup/
├── tests/
│   ├── isolation/              # CRITICAL: cross-instance isolation tests
│   ├── unit/
│   └── integration/
├── .env.example                # Global infra only
├── Makefile                    # Core targets + instance management
└── README.md
```

---

## 5. Modelo InstanceConfig (Diseño)

```python
# core/hermes/config.py
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

class AIProviderType(str, Enum):
    OLLAMA = "ollama"
    NVIDIA = "nvidia"
    OPENAI = "openai"

class AIPolicyScope(str, Enum):
    LOCAL_ONLY = "LOCAL_ONLY"        # Never leaves server
    CLOUD_ALLOWED = "CLOUD_ALLOWED"  # Can use cloud providers

class DolibarrConfig(BaseModel):
    version: str = "23.0.4"
    internal_url: str                # http://127.0.0.1:8080
    public_url: Optional[str] = None # https://dolibarr.empresa.com
    api_key: str
    documents_path: str              # /var/lib/dolibarr/documents/empresa

class TelegramConfig(BaseModel):
    bot_token: str
    webhook_path: str                # /webhook/empresa_a
    webhook_secret: str
    allowed_user_ids: list[int] = []

class DomainConfig(BaseModel):
    base: str                        # empresa.com
    dolibarr: Optional[str] = None   # dolibarr.empresa.com
    hermes: Optional[str] = None     # bot.empresa.com
    custom: dict[str, str] = {}      # otros hostnames

class AIConfig(BaseModel):
    default_policy: AIPolicyScope = AIPolicyScope.LOCAL_ONLY
    ollama_endpoint: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:4b"
    nvidia_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None

class InstanceConfig(BaseModel):
    instance_id: str                 # slug: "empresa_a", "transvega"
    company_name: str                # "Empresa A S.L."
    
    # Infraestructura
    database: DolibarrConfig
    telegram: TelegramConfig
    domains: DomainConfig
    ai: AIConfig
    
    # Extensiones
    enabled_agents: list[str] = []           # ["invoice_processing", "dog_intake"]
    enabled_workflows: list[str] = []
    enabled_tools: list[str] = []
    
    # Secrets (referencias, no valores)
    secrets_refs: dict[str, str] = {}        # {"dolibarr_db_password": "vault:empresa_a/db"}
    
    # Document paths
    documents_path: str = "/var/lib/gestor-ia/{instance_id}/documents"
    backups_path: str = "/var/backups/gestor-ia/{instance_id}"
    runtime_path: str = "/var/lib/gestor-ia/{instance_id}/runtime"
```

---

## 6. Modelo CompanyContext (Diseño)

```python
# core/hermes/context.py
from dataclasses import dataclass
from typing import Optional
from core.hermes.config import InstanceConfig

@dataclass(frozen=True)
class CompanyContext:
    """Contexto inmutable de empresa para una operación.
    
    NO usar variables globales mutables. 
    Se crea por request y se propaga explícitamente.
    """
    instance_config: InstanceConfig
    actor_type: str              # "telegram_user", "api_key", "system", "webhook"
    actor_id: str                # user_id, api_key_id, etc.
    request_id: str              # UUID para trazabilidad
    correlation_id: Optional[str] = None
    
    # Acceso conveniente a config frecuente
    @property
    def instance_id(self) -> str:
        return self.instance_config.instance_id
    
    @property
    def dolibarr_client_config(self) -> DolibarrConfig:
        return self.instance_config.database
    
    @property
    def telegram_config(self) -> TelegramConfig:
        return self.instance_config.telegram
    
    @property
    def ai_policy(self) -> AIConfig:
        return self.instance_config.ai
```

### Instance Resolver (Middleware)

```python
# core/hermes/resolver.py
from fastapi import Request, HTTPException, Depends
from core.hermes.config import InstanceConfig, load_instance_config
from core.hermes.context import CompanyContext

async def resolve_instance(request: Request) -> InstanceConfig:
    """
    Resuelve InstanceConfig ANTES de procesar contenido.
    
    Prioridad:
    1. Header X-Instance-ID (API calls)
    2. Telegram webhook path: /webhook/{instance_id}
    3. Host header → DomainConfig lookup
    4. API Key → Instance mapping
    """
    # 1. Header explícito
    instance_id = request.headers.get("X-Instance-ID")
    
    # 2. Telegram webhook path
    if not instance_id and request.url.path.startswith("/webhook/"):
        instance_id = request.url.path.split("/webhook/")[1].split("/")[0]
    
    # 3. Host header (dominio)
    if not instance_id:
        host = request.headers.get("host", "").split(":")[0]
        instance_id = await lookup_instance_by_domain(host)
    
    # 4. API Key
    if not instance_id:
        api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
        instance_id = await lookup_instance_by_api_key(api_key)
    
    if not instance_id:
        raise HTTPException(400, "Cannot resolve instance")
    
    config = load_instance_config(instance_id)
    if not config:
        raise HTTPException(404, f"Instance {instance_id} not found")
    
    return config


async def get_company_context(
    request: Request,
    instance_config: InstanceConfig = Depends(resolve_instance)
) -> CompanyContext:
    """Dependency que provee CompanyContext completo."""
    return CompanyContext(
        instance_config=instance_config,
        actor_type=determine_actor_type(request),
        actor_id=extract_actor_id(request),
        request_id=request.headers.get("X-Request-ID", str(uuid4())),
    )
```

---

## 7. Próximos Pasos (Orden de Implementación)

1. **PREFLIGHT** ✅ (completado)
2. **Auditoría Transvega** ✅ (completado - este documento)
3. **Diseñar arquitectura concreta Gestor-IA** → Este documento
4. **Detectar riesgos** → Sección 8
5. **Definir primera fase** → Sección 9
6. **Implementar estructura mínima** (Paso 9)
7. **InstanceConfig model** (Paso 10)
8. **CompanyContext model** (Paso 11)
9. **Instance Resolver** (Paso 12)
10. **Sistema extensiones por empresa** (Paso 13)
11. **Telegram base** (Paso 14)
12. **Dolibarr base** (Paso 15)
13. **AIProvider** (Paso 16)
14. **AI Policy mínima** (Paso 17)
15. **Cloudflare domain/routing abstraction** (Paso 18)
16. **Audit** (Paso 19)
17. **Tests de aislamiento** (Paso 20)
18. **Scripts** (Paso 21)
19. **Makefile** (Paso 22)
20. **README / documentación** (Paso 23)

---

## 8. Riesgos Detectados

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| **Fuga de datos entre instancias** | CRÍTICO | Tests de aislamiento obligatorios ANTES de cualquier feature. CompanyContext inmutable + resolver estricto. |
| **Complejidad Cloudflare multi-dominio** | ALTO | Generar ingress dinámico validado. Dry-run antes de reload. |
| **Gestión secretos multi-instancia** | ALTO | Vault/SealedSecrets o `.env` por instancia con gitignore estricto. |
| **Migración Transvega futura** | MEDIO | Diseñar `InstanceConfig` compatible con Transvega actual. |
| **Celery prematuro** | MEDIO | Empezar con JobQueue simple. Celery solo si carga real lo requiere. |
| **PostgreSQL innecesario** | BAJO | Usar MariaDB para auditoría. PostgreSQL solo si empresa lo exige. |

---

## 9. Primera Fase: Mínimo Viable

**Objetivo**: Un Hermes Core funcional con **1 instancia de ejemplo** que demuestre aislamiento completo.

### Entregables Fase 1:

| # | Componente | Archivo(s) | Test |
|---|------------|------------|------|
| 1 | Estructura directorios + pyproject.toml | `core/`, `companies/`, `instances/`, `tests/` | `make check` |
| 2 | GlobalSettings + InstanceConfig | `core/hermes/config.py` | Unit: validación schemas |
| 3 | CompanyContext + InstanceResolver | `core/hermes/context.py`, `resolver.py` | Unit: resolver por header/path/host |
| 4 | DolibarrClient (REUSE) | `core/integrations/dolibarr/client.py` | Unit: mock client |
| 5 | TelegramClient (REUSE) | `core/integrations/telegram/client.py` | Unit: mock client |
| 6 | AIProvider + OllamaProvider | `core/hermes/ai.py` | Unit: local model call |
| 7 | AIPolicy (LOCAL_ONLY default) | `core/hermes/policy.py` | Unit: routing decision |
| 8 | AuditLogger (ADAPT) | `core/hermes/audit.py` | Unit: log + query |
| 9 | Extension Registry | `core/hermes/extensions.py` | Unit: load agents/tools |
| 10 | FastAPI app + middleware | `core/hermes/main.py` | Integration: health, context injection |
| 11 | Instance config loader | `scripts/instance/load_config.py` | Integration: load example instance |
| 12 | **Tests aislamiento** | `tests/isolation/test_cross_instance.py` | **CRITICAL: 9 tests mínimos** |
| 13 | Makefile core targets | `Makefile` | `make test`, `make lint` |
| 14 | Scripts bootstrap | `scripts/install/`, `scripts/configure/` | `make install`, `make configure` |
| 15 | README arquitectura | `README.md` | - |

### Tests de Aislamiento Mínimos (Obligatorios)

```python
# tests/isolation/test_cross_instance.py
async def test_context_a_cannot_resolve_secrets_b():
    ctx_a = create_context("empresa_a")
    ctx_b = create_context("empresa_b")
    assert ctx_a.instance_config.secrets_refs != ctx_b.instance_config.secrets_refs

async def test_webhook_a_cannot_load_workflows_b():
    # Webhook resuelto a empresa_a no puede acceder a workflows de empresa_b
    ...

async def test_dolibarr_client_a_cannot_receive_config_b():
    client_a = DolibarrClient(ctx_a.instance_config.database)
    client_b = DolibarrClient(ctx_b.instance_config.database)
    assert client_a.base_url != client_b.base_url

async def test_telegram_a_cannot_resolve_b():
    router = TelegramRouter()
    update_a = make_update(chat_id=1, bot_token="token_a")
    update_b = make_update(chat_id=1, bot_token="token_b")
    # Cada webhook path resuelve su instancia
    ...

async def test_tools_a_not_visible_in_b():
    registry = ExtensionRegistry()
    registry.register("empresa_a", "tool_x", ToolX)
    registry.register("empresa_b", "tool_y", ToolY)
    assert "tool_x" not in registry.list_tools("empresa_b")

async def test_documents_a_cannot_resolve_paths_b():
    ...

async def test_path_traversal_cannot_escape_instance():
    # Intentar ../../empresa_b/documents desde empresa_a
    ...

async def test_domain_a_does_not_resolve_service_b():
    # Cloudflare ingress de empresa_a no apunta a puertos de empresa_b
    ...

async def test_cloudflare_config_no_cross_instance():
    config = generate_ingress([instance_a, instance_b])
    # Verificar que hostnames de A apuntan a puertos de A
    ...
```

---

## 10. Commits Planificados (Fase 1)

```bash
chore: initialize gestor-ia repository structure
feat: add GlobalSettings and InstanceConfig models
feat: add CompanyContext and InstanceResolver middleware
feat: add DolibarrClient integration (reused from Transvega)
feat: add TelegramClient integration (reused from Transvega)
feat: add AIProvider abstraction with OllamaProvider
feat: add AIPolicy with LOCAL_ONLY default
feat: add AuditLogger with MariaDB backend
feat: add ExtensionRegistry for per-instance agents/tools/workflows
feat: add FastAPI app with context injection middleware
feat: add instance configuration loader
test: add cross-instance isolation tests (9 critical tests)
feat: add native deployment bootstrap (systemd, Apache, MariaDB)
feat: add Makefile with core and instance targets
docs: document Gestor-IA architecture and instance model
```

---

## 11. Conclusión

Transvega-animal proporciona una **base sólida de componentes genéricos reutilizables** (DolibarrClient, CloudflareManager, TelegramClient, ModelRouter, ConversationManager, AuditLogger, Settings pattern, Makefile, scripts nativos).

La **deuda técnica y lógica de dominio específico** (perros, Milanuncios, transporte animal, expedientes animal, VeriFactu, Google Workspace cuentas funcionales) se **aisla completamente** en `companies/transvega/`.

Gestor-IA Core será **más pequeño, simple y genérico** que Transvega, diseñado desde el día 1 para **multi-instancia con aislamiento probado por tests**.

---

*Documento generado automáticamente como parte de la auditoría obligatoria pre-implementación.*