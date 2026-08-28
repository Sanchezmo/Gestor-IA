# Tareas de Implementación: Establecer Entorno DEVELOPMENT

## Change: `establish-development`

---

## Batch 1: Fix Autorización Crítico (BLOQUEANTE - HACER PRIMERO)

### T1.1: FAIL CLOSED en `create_dolibarr_client_for_user`
- **Archivo**: `core/hermes/context.py` (líneas 154-174)
- **Acción**: Reemplazar fallback a `db.api_key` con validación FAIL CLOSED
- **Test**: `pytest tests/identity/test_identity_resolver.py::TestIdentityResolver::test_resolve_*` - verificar que falla sin API key
- **Verificación**: `grep -n "FAIL CLOSED" core/hermes/context.py`

### T1.2: Eliminar ERP Mirrors de `GestorPermissions`
- **Archivo**: `core/hermes/identity.py` (líneas 216-220)
- **Acción**: Eliminar `PRODUCT_READ`, `THIRDPARTY_READ`, `CUSTOMER_INVOICE_READ`, `SUPPLIER_INVOICE_READ`
- **Actualizar**: `ALL` frozenset
- **Test**: `pytest tests/identity/test_authorization.py -v`
- **Verificación**: `grep -n "PRODUCT_READ\|THIRDPARTY_READ\|CUSTOMER_INVOICE_READ\|SUPPLIER_INVOICE_READ" core/hermes/identity.py` → 0 resultados

### T1.3: Verificar AuthorizationService y CapabilityResolver
- **Archivos**: `core/hermes/authorization.py`, `core/hermes/capabilities.py`
- **Acción**: Confirmar que solo enforcen capabilities propias de Hermes
- **Test**: `pytest tests/identity/test_authorization.py::TestAuthorizationService -v`

---

## Batch 2: Infraestructura DEVELOPMENT

### T2.1: Crear `docker-compose.development.yml`
- **Acción**: Nuevo archivo basado en `docker-compose.demo.yml` con renombrado:
  - Contenedores: `*-demo` → `*-development`
  - Red: `demo-network` → `development-network`
  - Volúmenes: `*-demo-*` → `*-development-*`
  - Secrets via `${VAR:-default}`
- **Verificación**: `docker compose -f docker-compose.development.yml config` válido

### T2.2: Renombrar `scripts/demo/` → `scripts/development/`
- **Archivos**: 
  - `scripts/demo/start-demo.sh` → `scripts/development/start-development.sh`
  - `scripts/demo/stop-demo.sh` → `scripts/development/stop-development.sh`
- **Actualizar en ambos**:
  - Log messages: "DEMO" → "DEVELOPMENT"
  - Docker compose file reference
  - Container names en healthchecks
  - Network name
  - Status output messages

### T2.3: Makefile Targets DEVELOPMENT
- **Archivo**: `Makefile`
- **Añadir/Verificar**:
  - `dev-start`
  - `dev-stop`
  - `dev-restart`
  - `dev-status`
  - `dev-health`
  - `dev-logs`
- **Verificación**: `make help | grep dev-`

### T2.4: Healthcheck CLI Mejoras
- **Archivo**: `core/hermes/cli/healthcheck.py` (o ubicación real)
- **Añadir checks**: Dolibarr REST, MariaDB, Ollama, Cloudflare, Telegram webhook, AI provider
- **Formato**: Tabla OK/WARNING/FAIL + resumen
- **Test**: `make dev-health` tras `make dev-start`

---

## Batch 3: Instancia DEVELOPMENT Config

### T3.1: Crear `instances/development/config.yml`
- **Acción**: Basado en `instances/empresa_a/config.yml` con ajustes:
  - `instance_id: development`
  - `database.host: mariadb-development` (Docker DNS)
  - `dolibarr.internal_url: http://dolibarr-development:80`
  - `telegram.webhook_path: /webhook/development`
  - `ai.ollama_endpoint: http://ollama-development:11434`
  - `secrets_refs` actualizados a `*_DEVELOPMENT`
- **Verificación**: Sintaxis YAML válida, referencias correctas

### T3.2: Crear `instances/development/instance.env` (local, NO Git)
- **Archivo**: `instances/development/instance.env`
- **Contenido**: Secrets reales para development
- **`.gitignore`**: Añadir `instances/development/instance.env` y `instances/development/identities.db`

### T3.3: Poblar Identity SQLite Development
- **Acción**: Insertar TelegramIdentity para usuario development
- **Comando**:
  ```bash
  sqlite3 instances/development/identities.db \
    "INSERT INTO telegram_identities (instance_id, telegram_user_id, dolibarr_user_id, enabled, created_at, dolibarr_api_key) VALUES ('development', <TU_TELEGRAM_ID>, <DOLIBARR_USER_ID>, 1, datetime('now'), '<API_KEY>');"
  ```
- **Verificación**: `sqlite3 instances/development/identities.db "SELECT * FROM telegram_identities;"`

---

## Batch 4: Arranque y Validación Inicial

### T4.1: Arrancar DEVELOPMENT
- **Comando**: `make dev-start`
- **Verificación**: 
  - `docker compose -f docker-compose.development.yml ps` → todos Up/healthy
  - `curl -sf http://localhost:8000/health` → 200 OK

### T4.2: Healthcheck Completo
- **Comando**: `make dev-health`
- **Verificación**: 9/9 componentes OK (Hermes, Redis, Dolibarr Web, Dolibarr REST, MariaDB, Ollama, Cloudflare, Telegram webhook, AI provider)

### T4.3: Configurar Webhook Telegram
- **Acción**: Verificar/actualizar webhook en Telegram Bot API
- **URL**: `https://bot.development.local/webhook/development` (via Cloudflare Tunnel)
- **Verificación**: `curl -X POST https://api.telegram.org/bot<TOKEN>/getWebhookInfo` → apunta a development

---

## Batch 5: Validación E2E Real (19 Fases)

### T5.1: Fase 7 - Identity Resolution Real
- **Acción**: En Telegram enviar `/start` o cualquier comando
- **Verificación**: Logs muestran identity resuelta, user_context creado con dolibarr_user_id y gestor_roles

### T5.2: Fase 8 - `/terceros` Read Real
- **Acción**: Telegram `/terceros`
- **Verificación**: Lista thirdparties Dolibarr real, NO error "thirdparty.read missing"

### T5.3: Fase 9 - `/productos` Read Real
- **Acción**: Telegram `/productos`
- **Verificación**: Lista products Dolibarr real, NO error "product.read missing"

### T5.4: Fase 10 - Natural Language Real
- **Acciones**: 
  - "Lista los primeros clientes"
  - "Busca el cliente Prueba Hermes"
  - "Lista los primeros productos"
- **Verificación**: IntentInterpreter + Query Layer → Dolibarr → respuesta formateada

### T5.5: Fase 11 - Create Thirdparty Preview/Confirm
- **Acción**: "Crea el cliente DEV HERMES TEST SL con CIF B44444444"
- **Verificación**: 
  1. Preview mostrado (NO escribe)
  2. Confirm → DolibarrClient user-scoped POST → Dolibarr 201
  3. Resource ID guardado como evidencia
  4. GET posterior verifica existencia

### T5.6: Fase 12 - Cancelación
- **Acción**: "Crea el cliente DEV CANCEL TEST SL con CIF B55555555" → Preview → Cancel
- **Verificación**: NO existe en Dolibarr (GET 404 o no en lista)

### T5.7: Fase 13 - Idempotencia
- **Acción**: Confirmar nuevamente mismo command fase 11
- **Verificación**: Una sola creación en Dolibarr (idempotency key)

### T5.8: Fase 14 - Product Create
- **Acción**: "Crea el producto DEV-PINT-001, Pintura blanca desarrollo, precio 25.50, IVA 21%"
- **Verificación**: Preview → Confirm → Dolibarr tiene producto (money como Decimal/string)

### T5.9: Fase 15 - Service Create
- **Acción**: "Crea el servicio DEV-SERV-001, Servicio pintura desarrollo, precio 100.00, IVA 21%"
- **Verificación**: Preview → Confirm → Dolibarr tiene servicio

### T5.10: Fase 16 - 403 Real Usuario Restringido
- **Preparación**: Crear usuario Dolibarr sin permiso `societe.thirdparty_customer.write`
- **Acción**: Con ese usuario → `/terceros` o create thirdparty
- **Verificación**: "No tienes permisos en Dolibarr para realizar esta operación" / "crear terceros"

### T5.11: Fase 17 - Business Insights Permisos
- **Acción**: "¿Cuánto tenemos pendiente de cobrar?"
- **Verificación**: Usa user API key → si 403 → "No tienes permisos...", si 2xx → respuesta

### T5.12: Fase 18 - Full Restart Survival
- **Acción**: `make dev-restart` → `make dev-health` → Telegram "Busca el cliente DEV HERMES TEST"
- **Verificación**: Datos previos accesibles, todo funcional

---

## Batch 6: Tests Finales y Calidad

### T6.1: Suite Completa Tests
- **Comando**: `pytest tests/ -v --tb=short`
- **Target**: 325 passed, 1 skipped, 0 failed

### T6.2: Lint y Type Check
- **Comandos**: 
  - `ruff check .`
  - `mypy core/ companies/ scripts/`
- **Target**: 0 errores

### T6.3: Git Diff Check
- **Comando**: `git diff --check`
- **Target**: 0 warnings

### T6.4: Commits Semánticos
- **Ejemplos**:
  - `fix(auth): implement FAIL CLOSED for user-scoped Dolibarr credentials`
  - `fix(auth): remove ERP permission mirrors from GestorPermissions`
  - `refactor(infra): promote demo infrastructure to development`
  - `feat(dev): add development lifecycle commands to Makefile`
  - `feat(dev): add development instance configuration`
  - `test(e2e): validate development telegram-dolibarr flow`

---

## Checklist de Progreso

### Batch 1: Autorización
- [ ] T1.1 FAIL CLOSED context.py
- [ ] T1.2 Eliminar ERP mirrors identity.py
- [ ] T1.3 Verificar AuthorizationService/CapabilityResolver
- [ ] Tests identidad/autorización pasan

### Batch 2: Infraestructura
- [ ] T2.1 docker-compose.development.yml
- [ ] T2.2 scripts/development/
- [ ] T2.3 Makefile dev-* targets
- [ ] T2.4 Healthcheck CLI

### Batch 3: Instancia
- [ ] T3.1 instances/development/config.yml
- [ ] T3.2 instance.env + .gitignore
- [ ] T3.3 Identity SQLite poblado

### Batch 4: Arranque
- [ ] T4.1 make dev-start
- [ ] T4.2 make dev-health (9/9 OK)
- [ ] T4.3 Webhook Telegram configurado

### Batch 5: E2E (19 fases)
- [ ] T5.1 Identity resolution
- [ ] T5.2 /terceros
- [ ] T5.3 /productos
- [ ] T5.4 Natural language
- [ ] T5.5 Create thirdparty
- [ ] T5.6 Cancel
- [ ] T5.7 Idempotencia
- [ ] T5.8 Product create
- [ ] T5.9 Service create
- [ ] T5.10 403 restringido
- [ ] T5.11 Business Insights
- [ ] T5.12 Full restart

### Batch 6: Calidad
- [ ] T6.1 Tests suite (325 passed)
- [ ] T6.2 Lint/mypy clean
- [ ] T6.3 Git diff clean
- [ ] T6.4 Commits semánticos + push

---

## Estimación de Tiempo

| Batch | Horas Estimadas |
|-------|-----------------|
| 1: Autorización | 1-2h |
| 2: Infraestructura | 2-3h |
| 3: Instancia | 1h |
| 4: Arranque | 1h |
| 5: E2E Validación | 4-6h |
| 6: Tests/Calidad | 1-2h |
| **Total** | **10-15h** |

---

## Notas de Implementación

1. **Orden estricto**: Batch 1 DEBE completarse antes de Batch 4-5 (autorización bloqueante)
2. **Evidencia real**: Cada fase E2E debe producir evidencia verificable en Dolibarr (GET posterior, resource ID)
3. **No mocks**: Validación contra Dolibarr REAL, MariaDB REAL, Telegram REAL
4. **Commits atómicos**: Un commit por tarea lógica, messages convencionales
5. **Push solo al final**: Cuando todo verde y verificado