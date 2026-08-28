# Diseño Técnico: Establecer Entorno DEVELOPMENT

## Change: `establish-development`

---

## 1. Visión General de Arquitectura

### Estado Objetivo
```
┌─────────────────────────────────────────────────────────────────┐
│                      DEVELOPMENT ENVIRONMENT                     │
├─────────────────────────────────────────────────────────────────┤
│  Telegram Bot (existente)                                        │
│       │                                                          │
│       ▼                                                          │
│  Cloudflare Tunnel (existente)                                   │
│       │                                                          │
│       ▼                                                          │
│  Hermes API (FastAPI) ──► docker-compose.development.yml        │
│       │                    ├─ mariadb-development (3306)         │
│       │                    ├─ dolibarr-development (8081)        │
│       │                    ├─ redis-development (6379)           │
│       │                    └─ ollama-development (11434)         │
│       │                                                          │
│       ▼                                                          │
│  Dolibarr REST API (user-scoped API keys)                       │
│       │                                                          │
│       ▼                                                          │
│  Dolibarr ERP (autoridad permisos)                              │
└─────────────────────────────────────────────────────────────────┘
```

### Principios de Diseño
1. **Reutilizar > Recrear**: Infraestructura existente funcional
2. **FAIL CLOSED**: Sin fallbacks silenciosos a credenciales admin
3. **Single Source of Truth**: Dolibarr = permisos ERP, Hermes = identidad/workflow
4. **Config-Driven**: Diferencias de entorno solo en configuración/secrets
5. **Observabilidad**: Healthcheck completo, logs estructurados, auditoría

---

## 2. Fix Autorización (FR-1) - Implementación Inmediata

### 2.1 `core/hermes/context.py` - FAIL CLOSED

**Archivo**: `core/hermes/context.py`
**Método**: `create_dolibarr_client_for_user` (líneas 154-174)

```python
def create_dolibarr_client_for_user(self, identity) -> DolibarrClient:
    """
    Crear cliente Dolibarr usando la API key DEL USUARIO.
    
    FAIL CLOSED: Si el usuario no tiene API key configurada, lanza error.
    NO usa fallback a la key de instancia/admin.
    """
    from core.integrations.dolibarr.client import DolibarrClient

    db = self.instance_config.dolibarr
    
    # FAIL CLOSED: Validar que existe identity y tiene API key
    if not identity:
        raise ValueError(
            "No TelegramIdentity provided. Cannot create user-scoped DolibarrClient."
        )
    
    if not identity.dolibarr_api_key:
        raise ValueError(
            f"User {identity.telegram_user_id} has no Dolibarr API key configured. "
            f"FAIL CLOSED: no admin fallback."
        )
    
    api_key = identity.dolibarr_api_key
    return DolibarrClient(
        base_url=db.internal_url,
        api_key=api_key,
        timeout=30,
    )
```

**Cambio**: Eliminar línea 169 `api_key = identity.dolibarr_api_key if identity and identity.dolibarr_api_key else db.api_key`

---

### 2.2 `core/hermes/identity.py` - Eliminar ERP Mirrors

**Archivo**: `core/hermes/identity.py`
**Clase**: `GestorPermissions` (líneas 205-248)

**Eliminar** (líneas 216-220):
```python
# ELIMINAR - Son ERP mirrors, Dolibarr los controla
PRODUCT_READ = "product.read"
THIRDPARTY_READ = "thirdparty.read"
CUSTOMER_INVOICE_READ = "customer_invoice.read"
SUPPLIER_INVOICE_READ = "supplier_invoice.read"
```

**Mantener** (solo capabilities propias de Hermes):
```python
# AI capabilities
AI_USE = "ai.use"
AI_EXTERNAL_PROVIDER = "ai.external_provider"

# Admin capabilities
ADMIN = "admin"
TELEGRAM_MANAGE = "telegram.manage"
INSTANCE_MANAGE = "instance.manage"
AUDIT_READ = "audit.read"

# Content capabilities
CONTENT_GENERATE = "content.generate"

# Advanced/Experimental (futuro)
BC3_IMPORT = "bc3.import"
MASS_OPERATIONS = "mass_operations"
MEDIA_PUBLISH = "media.publish"
SYSTEM_MANAGE = "system.manage"

# Write permissions Command Layer V1 (Hermes controla workflow, NO permiso ERP)
THIRDPARTY_CREATE = "thirdparty.create"
PRODUCT_CREATE = "product.create"
SERVICE_CREATE = "service.create"

# Write permissions Command Layer V2 (experimental)
PROPOSAL_CREATE = "proposal.create"
```

**Actualizar** `ALL` frozenset para reflejar solo los mantenidos.

---

### 2.3 Verificación `AuthorizationService` y `CapabilityResolver`

**Archivos**: 
- `core/hermes/authorization.py` - Ya correcto (solo usa `gestor_roles` via `CapabilityResolver`)
- `core/hermes/capabilities.py` - Ya correcto (`HERMES_CAPABILITIES` solo capacidades propias, `resolve()` devuelve `False` para ERP mirrors)

**Validación**: Ejecutar tests `tests/identity/test_authorization.py` y `tests/identity/test_identity_resolver.py`

---

## 3. Infraestructura DEVELOPMENT (FR-2)

### 3.1 `docker-compose.development.yml`

**Crear nuevo archivo** (basado en `docker-compose.demo.yml`):

```yaml
version: '3.8'

services:
  # Dolibarr ERP para instancia development
  dolibarr-development:
    image: tuxgasy/dolibarr:latest
    container_name: dolibarr-development
    environment:
      - DOLI_DB_HOST=mariadb-development
      - DOLI_DB_NAME=dolibarr_development
      - DOLI_DB_USER=dolibarr_development
      - DOLI_DB_PASSWORD=${DOLIBARR_DB_PASSWORD_DEVELOPMENT:-***REMOVED***}
      - DOLI_ADMIN_LOGIN=admin
      - DOLI_ADMIN_PASSWORD=admin123
      - DOLI_URL_ROOT=http://localhost:8081
      - PHP_MEMORY_LIMIT=512M
      - PHP_MAX_EXECUTION_TIME=300
      - PHP_UPLOAD_MAX_FILESIZE=64M
      - PHP_POST_MAX_SIZE=64M
      - TZ=Europe/Madrid
    ports:
      - "8081:80"
    volumes:
      - dolibarr-development-docs:/var/www/documents
      - dolibarr-development-custom:/var/www/html/custom
    depends_on:
      mariadb-development:
        condition: service_healthy
    networks:
      - development-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost/index.php"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s

  # MariaDB para Dolibarr
  mariadb-development:
    image: mariadb:10.11
    container_name: mariadb-development
    ports:
      - "3306:3306"
    environment:
      - MARIADB_ROOT_PASSWORD=${MARIADB_ROOT_PASSWORD:-***REMOVED***}
      - MARIADB_DATABASE=dolibarr_development
      - MARIADB_USER=dolibarr_development
      - MARIADB_PASSWORD=${DOLIBARR_DB_PASSWORD_DEVELOPMENT:-***REMOVED***}
    volumes:
      - mariadb-development-data:/var/lib/mysql
    networks:
      - development-network
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u", "root", "-p${MARIADB_ROOT_PASSWORD:-***REMOVED***}"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 30s

  # Redis para Hermes
  redis-development:
    image: redis:7-alpine
    container_name: redis-development
    command: redis-server --requirepass ${REDIS_PASSWORD:-***REMOVED***}
    ports:
      - "6379:6379"
    volumes:
      - redis-development-data:/data
    networks:
      - development-network
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD:-***REMOVED***}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Ollama para IA local (opcional - para LOCAL_ONLY)
  ollama-development:
    image: ollama/ollama:latest
    container_name: ollama-development
    ports:
      - "11434:11434"
    volumes:
      - ollama-development-data:/root/.ollama
    networks:
      - development-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434/api/tags"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s

volumes:
  dolibarr-development-docs:
  dolibarr-development-custom:
  mariadb-development-data:
  redis-development-data:
  ollama-development-data:

networks:
  development-network:
    driver: bridge
```

**Cambios clave vs demo**:
- Nombres contenedores: `*-demo` → `*-development`
- Red: `demo-network` → `development-network`
- Volúmenes: `*-demo-*` → `*-development-*`
- Secrets via `${VAR:-default}` para configuración por env

---

### 3.2 `scripts/development/start-development.sh`

**Renombrar** `scripts/demo/start-demo.sh` → `scripts/development/start-development.sh`

**Cambios**:
- Log messages: "DEMO" → "DEVELOPMENT"
- Docker compose file: `docker-compose.demo.yml` → `docker-compose.development.yml`
- Network: `demo-network` → `development-network`
- Container names en healthchecks: `mariadb-demo` → `mariadb-development`, etc.
- Status output: "DEMO GESTOR-IA LISTO" → "DEVELOPMENT GESTOR-IA LISTO"

---

### 3.3 `scripts/development/stop-development.sh`

**Renombrar** `scripts/demo/stop-demo.sh` → `scripts/development/stop-development.sh`

**Cambios**:
- Docker compose file: `docker-compose.demo.yml` → `docker-compose.development.yml`
- Container names actualizados

---

### 3.4 Makefile Targets

**Verificar/Añadir en `Makefile`**:

```makefile
# Development lifecycle
.PHONY: dev-start dev-stop dev-restart dev-status dev-health dev-logs

dev-start: ## Iniciar entorno DEVELOPMENT completo
	@./scripts/development/start-development.sh

dev-stop: ## Parar entorno DEVELOPMENT
	@./scripts/development/stop-development.sh

dev-restart: dev-stop dev-start ## Reiniciar entorno DEVELOPMENT

dev-status: ## Ver estado de servicios DEVELOPMENT
	@docker compose -f docker-compose.development.yml ps
	@echo ""
	@curl -sf http://localhost:8000/health 2>/dev/null && echo "Hermes API: OK" || echo "Hermes API: DOWN"

dev-health: ## Healthcheck completo DEVELOPMENT
	@source .venv/bin/activate && python -m core.hermes.cli healthcheck

dev-logs: ## Ver logs Hermes DEVELOPMENT
	@tail -f /tmp/hermes-development.log 2>/dev/null || echo "Log no encontrado"
```

---

### 3.5 Healthcheck CLI Mejoras

**Archivo**: `core/hermes/cli/healthcheck.py` (o donde esté el comando)

**Validar componentes**:
| Componente | Check | OK Criteria |
|------------|-------|-------------|
| Hermes API | `GET /health` | 200 + JSON válido |
| Redis | `PING` con auth | PONG |
| Dolibarr Web | `GET /index.php` | 200 |
| Dolibarr REST | `GET /api/index.php/users/1` con API key | 200 |
| MariaDB | `mysqladmin ping` | OK |
| Ollama | `GET /api/tags` | 200 |
| Cloudflare | `cloudflared tunnel list` | Tunnel activo |
| Telegram Webhook | `GET /webhook/development` | 200/405 (existe) |
| AI Provider | Según policy (Ollama/cloud) | Responde |

**Salida**: Tabla con OK/WARNING/FAIL por componente, resumen final.

---

## 4. Instancia DEVELOPMENT Config (FR-3)

### 4.1 Estructura `instances/development/`

```
instances/development/
├── config.yml          # Config NO secreta (referencia docker-compose.development.yml)
├── instance.env        # Secrets (NO en Git, .gitignore)
└── identities.db       # SQLite (runtime, NO en Git)
```

### 4.2 `instances/development/config.yml`

```yaml
instance_id: development
company_name: "Development Empresa SL"
database:
  host: mariadb-development
  port: 3306
  name: dolibarr_development
  user: dolibarr_development
  password: ***REMOVED***  # Referenciado via secret_ref
dolibarr:
  version: "23.0.4"
  internal_url: http://dolibarr-development:80
  public_url: https://dolibarr.development.local
  api_key: ***REMOVED***  # Referenciado via secret_ref
  documents_path: /var/lib/dolibarr/documents/development
telegram:
  bot_token: ***REMOVED***  # Reutilizado de empresa_a
  webhook_path: /webhook/development
  webhook_secret: ***REMOVED***
  webhook_secret_required: true
  allowed_user_ids: []
  max_file_size_mb: 10
  update_idempotency_ttl_hours: 24
domains:
  base: development.local
  dolibarr: dolibarr.development.local
  hermes: bot.development.local
  custom: {}
ai:
  default_policy: LOCAL_ONLY
  ollama_endpoint: http://ollama-development:11434
  ollama_model: qwen3.5:4b
  task_policies:
    invoice_processing: LOCAL_ONLY
    content_generation: CLOUD_ALLOWED
    general_chat: CLOUD_ALLOWED
enabled_agents:
  - invoice_processing
  - general_assistant
enabled_workflows:
  - invoice_approval
enabled_tools:
  - dolibarr_search
  - pdf_extract
secrets_refs:
  dolibarr_db_password: env:DOLIBARR_DB_PASSWORD_DEVELOPMENT
  dolibarr_api_key: env:DOLIBARR_API_KEY_DEVELOPMENT
  telegram_bot_token: env:TELEGRAM_BOT_TOKEN_DEVELOPMENT
  telegram_webhook_secret: env:TELEGRAM_WEBHOOK_SECRET_DEVELOPMENT
documents_path: /var/lib/gestor-ia/development/documents
backups_path: /var/backups/gestor-ia/development
runtime_path: /var/lib/gestor-ia/development/runtime
dolibarr_apache_port: 8081
active: true
```

### 4.3 `instances/development/instance.env` (NO en Git)

```bash
# Secrets para instancia development
DOLIBARR_DB_PASSWORD_DEVELOPMENT=***REMOVED***
DOLIBARR_API_KEY_DEVELOPMENT=***REMOVED***
TELEGRAM_BOT_TOKEN_DEVELOPMENT=***REMOVED***
TELEGRAM_WEBHOOK_SECRET_DEVELOPMENT=***REMOVED***
```

### 4.4 `.gitignore` Actualización

Añadir:
```
instances/development/instance.env
instances/development/identities.db
```

---

## 5. Validación E2E - Plan de Ejecución (FR-4)

### 5.1 Preparación Previa

1. **Crear usuario Dolibarr development** con API key:
   - En Dolibarr UI: Usuarios → Nuevo → Generar API key
   - Guardar API key en `TelegramIdentity.dolibarr_api_key` via SQLite

2. **Configurar identidad Telegram** en `instances/development/identities.db`:
   ```sql
   INSERT INTO telegram_identities (instance_id, telegram_user_id, dolibarr_user_id, enabled, created_at, dolibarr_api_key)
   VALUES ('development', <TU_TELEGRAM_ID>, <DOLIBARR_USER_ID>, 1, datetime('now'), '<API_KEY>');
   ```

3. **Configurar webhook Telegram** hacia Cloudflare:
   - `https://bot.development.local/webhook/development`
   - Verificar que Cloudflare Tunnel apunta a Hermes puerto 8000

### 5.2 Secuencia de Validación (19 Fases)

| Fase | Comando/Action | Verificación |
|------|----------------|--------------|
| 1-6 | `make dev-start` → `make dev-health` | Todos OK |
| 7 | Telegram: `/start` | Identity resuelta, user_context creado |
| 8 | Telegram: `/terceros` | Lista real Dolibarr, sin error perm |
| 9 | Telegram: `/productos` | Lista real Dolibarr, sin error perm |
| 10 | Telegram: "Lista primeros clientes" | NL → Query Layer → Dolibarr → respuesta |
| 11 | Telegram: "Crea cliente DEV TEST SL CIF B11111111" | Preview → Confirm → Dolibarr tiene ID |
| 12 | Telegram: "Crea cliente DEV CANCEL SL CIF B22222222" → Preview → Cancel | NO existe en Dolibarr |
| 13 | Repetir confirm fase 11 | Una sola creación (idempotencia) |
| 14 | Telegram: "Crea producto DEV-PROD-001, Producto Test, 25.50, IVA 21%" | Preview → Confirm → Dolibarr tiene producto |
| 15 | Telegram: "Crea servicio DEV-SERV-001, Servicio Test, 100.00, IVA 21%" | Preview → Confirm → Dolibarr tiene servicio |
| 16 | Usuario restringido: `/terceros` | "No tienes permisos en Dolibarr..." |
| 17 | Telegram: "¿Cuánto tenemos pendiente de cobrar?" | Business Insights usa user key, 403 si no permiso |
| 18 | `make dev-restart` → `make dev-health` → Telegram: "Busca cliente DEV TEST" | Datos persisten |
| 19 | `pytest tests/ -v` | 325 passed, 0 failed |

---

## 6. Tests y Calidad (FR-5)

### 6.1 Tests Existentes a Ejecutar
```bash
# Suite completa
pytest tests/ -v --tb=short

# Específicos autorización/identidad
pytest tests/identity/ -v
pytest tests/commands/ -v
pytest tests/e2e/ -v
pytest tests/isolation/ -v
```

### 6.2 Lint/Type Check
```bash
ruff check .
mypy core/ companies/ scripts/
```

### 6.3 Git Diff Check
```bash
git diff --check
```

---

## 7. Secuencia de Implementación (Orden de Dependencias)

### Batch 1: Fix Autorización (Bloqueante - HACER PRIMERO)
1. `core/hermes/context.py` - FAIL CLOSED
2. `core/hermes/identity.py` - Eliminar ERP mirrors
3. Tests: `pytest tests/identity/ -v`

### Batch 2: Infraestructura DEVELOPMENT
1. `docker-compose.development.yml` (nuevo)
2. `scripts/development/start-development.sh` (rename + update)
3. `scripts/development/stop-development.sh` (rename + update)
4. Makefile targets `dev-*`
5. Healthcheck CLI mejoras

### Batch 3: Instancia DEVELOPMENT
1. `instances/development/config.yml`
2. `instances/development/instance.env` (local, no Git)
3. `.gitignore` update
4. Identities SQLite: poblar usuario development

### Batch 4: Arranque y Validación
1. `make dev-start`
2. `make dev-health` → todos OK
3. Configurar webhook Telegram → Cloudflare → Hermes

### Batch 5: Validación E2E (19 fases)
Ejecutar secuencia completa documentada en sección 5.2

### Batch 6: Tests Finales
1. Suite completa
2. Lint/mypy
3. Git diff check

---

## 8. Riesgos Técnicos y Mitigación

| Riesgo | Mitigación |
|--------|------------|
| Volúmenes Docker nuevos pierden datos demo | Aceptable: development empieza limpio. Demo data no crítica. |
| Webhook Telegram cambia URL | Reutilizar bot/token existente. Solo cambiar path a `/webhook/development`. |
| Cloudflare Tunnel no apunta a development | Verificar ingress rules. Tunnel existente reutilizable. |
| MariaDB puerto 3306 conflicto local | Docker Compose usa red interna `development-network`. Puerto host 3306 solo si necesario. |
| Healthcheck falsos positivos | Validar conectividad real (no solo proceso vivo). |
| Tests E2E flaky por timing | Timeouts generosos, reintentos en healthchecks. |

---

## 9. Rollback Plan

Si algo falla irreparablemente:
```bash
# Restaurar estado previo
git checkout -- core/hermes/context.py core/hermes/identity.py
docker compose -f docker-compose.demo.yml up -d  # Volver a demo
./scripts/demo/start-demo.sh
```

---

## 10. Métricas de Éxito

| Métrica | Target |
|---------|--------|
| Tiempo `make dev-start` → healthcheck OK | < 3 min |
| Tiempo `make dev-restart` → Telegram funcional | < 2 min |
| Tests suite | 325 passed, 0 failed |
| Healthcheck components | 9/9 OK |
| E2E phases | 19/19 pass |
| Secretos en Git | 0 |